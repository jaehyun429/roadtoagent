"""
01-mcp-server: 사이버캠퍼스 조회 MCP 서버

실행
  stdio : uv run server.py
  HTTP  : uv run server.py --http            (http://127.0.0.1:8000/mcp)
  스키마: uv run server.py --dump-schema     (schema.json 저장)

환경변수 (.env 또는 쉘)
  CAMPUS_URL     사이버캠퍼스 기본 URL (예: https://cyber.xxx.ac.kr)
  CAMPUS_COOKIE  로그인 세션 쿠키 문자열
"""

import base64
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Annotated

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastmcp import FastMCP
from pydantic import Field


def load_dotenv() -> None:
    """server.py 옆의 .env를 읽어 환경변수에 넣는다 (이미 있는 변수는 건드리지 않음).
    Claude Desktop이 서버를 띄울 땐 쉘 환경이 없어서 이게 필요하다."""
    env = Path(__file__).with_name(".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_dotenv()
mcp = FastMCP("cybercampus")

# ---------------------------------------------------------------- 설정 ---
CAMPUS_URL = os.environ.get("CAMPUS_URL", "https://dcs-learning.cnu.ac.kr").rstrip("/")
CAMPUS_COOKIE = os.environ.get("CAMPUS_COOKIE", "")
# 사이트 프론트엔드가 요청 본문을 AES로 감싸서 보낸다. 키는 사이트의 공개 JS
# (/js/assets/gibberish-aes-*.js 의 getSecKey)에 적힌 32글자 상수. 레포엔 올리지 않고 .env로.
CAMPUS_AES_KEY = os.environ.get("CAMPUS_AES_KEY", "")
CACHE_TTL_SEC = 300          # 캐시는 서버 내부 TTL로만. clear_cache 도구 없음.
MAX_ITEMS = 20               # 한 응답 최대 건수. 넘으면 잘라서 truncated=True
TIMEOUT_SEC = 10

# ----- 세 API. 경로와 Payload는 브라우저 Network 탭에서 잡은 요청을 복호화해 확인한 것 -----
# 모두 POST, 본문은 form 필드 e=<AES(JSON)>. {term_year} 등은 서버가 채운다.
# 모델은 이 값들을 몰라도 된다 — 그래서 도구 인자에 없다.
API_COURSES = {
    "path": "/api/v1/course/getStdMyCourseList",
    "payload": {"term_year": "{term_year}", "term_cd": "{term_cd}", "is_current": "Y"},
}
# 과제·공지 payload는 브라우저가 과목 객체를 그대로 실어 보내는 형태. 필드 순서까지 맞춘다.
COURSE_FIELDS = {
    "class_no": "{class_no}", "course_id": "{course_id}", "course_nm": "{course_nm}",
    "term_cd": "{term_cd}", "term_cd_nm": "{term_cd_nm}", "term_year": "{term_year}",
    "subject_cd": "{subject_cd}", "attendance_standard": "{attendance_standard}",
    "offline_ag_use_yn": "{offline_ag_use_yn}", "individual_ag_use_yn": "{individual_ag_use_yn}",
    "ocw_yn": "{ocw_yn}", "auditor_course_use_yn": "{auditor_course_use_yn}",
}
API_ASSIGNMENTS = {
    "path": "/api/v1/task/stdList",
    "payload": {
        "offset": 0, "limit": "{limit}", **COURSE_FIELDS,
        "search_type": "0", "page_order": "WEEK_NO DESC, SEQ_NO DESC",
    },
}
API_NOTICES = {
    "path": "/api/v1/board/notice/list",
    "payload": {
        "q_key": "", "q_where_type": "BOARDITEM_TITLE", **COURSE_FIELDS,
        "offset": 0, "limit": "{limit}",
    },
}

def current_term() -> tuple[str, str]:
    """조회할 학기. 모델이 정하지 않고 서버가 정한다.

    기본은 오늘 날짜로 계산 (1학기 코드 "10" 확인됨, 2학기는 "20"으로 추정).
    환경변수 CAMPUS_TERM_YEAR / CAMPUS_TERM_CD가 있으면 그걸 쓴다.
    (2학기 수강 과목이 없어서 1학기(2026/10)를 보고 싶을 때 등)
    """
    today = date.today()
    year = os.environ.get("CAMPUS_TERM_YEAR") or str(today.year)
    term_cd = os.environ.get("CAMPUS_TERM_CD") or ("10" if 3 <= today.month <= 8 else "20")
    return year, term_cd


# ------------------------------------------------------------ F2 응답 ---
# 성공: {"ok": true, "total": N, "returned": n, "truncated": bool, "items": [...]}
#   - 결과 0건은 ok=true, total=0, items=[] (실패가 아니다)
#   - total은 서버가 아는 전체 건수, returned는 실제로 넘긴 건수. 다르면 truncated=true
# 실패: {"ok": false, "error": "CODE", "message": "상황 + 모델이 다음에 할 일"}

def ok(items: list, total: int | None = None) -> dict:
    total = len(items) if total is None else total
    items = items[:MAX_ITEMS]
    return {
        "ok": True,
        "total": total,
        "returned": len(items),
        "truncated": total > len(items),
        "items": items,
    }


def fail(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}


ERR_SESSION_EXPIRED = fail(
    "SESSION_EXPIRED",
    "세션 만료, 사용자에게 쿠키 갱신을 요청하세요.",
)
ERR_SERVER_NOT_RESPONDING = fail(
    "SERVER_NOT_RESPONDING",
    "서버 응답없음, 잠시 후 재시도하세요.",
)
ERR_PARSING_FAILED = fail(
    "PARSING_FAILED",
    "파싱 실패, 사이트 구조가 변경된 것 같습니다. 사용자에게 알리세요.",
)


def err_course_not_found(name: str) -> dict:
    return fail(
        "COURSE_NOT_FOUND",
        f"'{name}'과 일치하는 과목이 없습니다. list_courses로 정확한 과목명을 확인하세요.",
    )


class SourceError(Exception):
    """fetch/parse 단계의 실패. .response에 F2 실패 dict를 담아 도구 함수까지 올린다."""

    def __init__(self, response: dict):
        super().__init__(response["error"])
        self.response = response


# -------------------------------------------------------------- 캐시 ---
# key -> (저장 시각, 값). 과목 목록은 "courses", 과목별 데이터는 "assignments:<id>" 식으로 분리.
_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, loader):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


# ------------------------------------------------------------------ fetch ---
def aes_encrypt(plaintext: str) -> str:
    """사이트 JS의 util.aesEncode와 같은 결과: AES-256-CBC, IV=0, PKCS7, base64."""
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(CAMPUS_AES_KEY.encode()), modes.CBC(bytes(16))).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def build_payload(api: dict, **vars) -> dict:
    out = {}
    for k, v in api["payload"].items():
        if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
            out[k] = vars[v[1:-1]]
        else:
            out[k] = v
    return out


def fetch_json(api: dict, **vars) -> dict:
    """API를 호출해 body(dict)를 돌려준다. 실패는 SourceError로 올린다."""
    if not CAMPUS_COOKIE or not CAMPUS_AES_KEY:
        raise SourceError(ERR_SESSION_EXPIRED)

    payload = build_payload(api, **vars)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))  # JS JSON.stringify와 동일

    try:
        r = httpx.post(
            CAMPUS_URL + api["path"],
            data={"e": aes_encrypt(body)},
            headers={"Cookie": CAMPUS_COOKIE},
            timeout=TIMEOUT_SEC,
            follow_redirects=False,   # 세션 만료면 302로 로그인 페이지에 보낸다 (쿠키 없이 쳐서 확인함)
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError):
        raise SourceError(ERR_SERVER_NOT_RESPONDING)

    if r.status_code >= 500:
        raise SourceError(ERR_SERVER_NOT_RESPONDING)
    if r.status_code in (301, 302, 303, 307, 308, 401, 403):
        raise SourceError(ERR_SESSION_EXPIRED)

    try:
        doc = r.json()
    except ValueError:
        # 200인데 JSON이 아니면 로그인 HTML이 온 것. 세션 만료로 본다.
        raise SourceError(ERR_SESSION_EXPIRED)

    # 응답 구조: {"header": {"code": 200, "msg": "OK"}, "body": {...}}
    header = doc.get("header") or {}
    body_ = doc.get("body")
    if header.get("code") != 200 or not isinstance(body_, dict):
        raise SourceError(ERR_PARSING_FAILED)
    return body_

# ------------------------------------------------------------------ parse ---
# 파서가 곧 필터다. 설계한 필드 외에는 아무것도 넘기지 않는다.
# (응답에는 교수 전화번호, 암호화 토큰, 과제 본문 HTML 수천 자가 섞여 온다)

def parse_courses(body: dict) -> list[dict]:
    """body.course_list -> 내부용 과목 dict. class_no, term_*는 과제·공지 API payload를 만들 때 쓴다."""
    keys = ["class_no", "course_id", "course_nm", "term_cd", "term_cd_nm", "term_year", "subject_cd",
            "attendance_standard", "offline_ag_use_yn", "individual_ag_use_yn", "ocw_yn", "auditor_course_use_yn"]
    return [
        {"name": c["course_nm"], "professor": c["prof_nm"], **{k: c.get(k) for k in keys}}
        for c in body["course_list"]
    ]


def public_course(c: dict) -> dict:
    """모델에게 넘기는 건 설계한 셋만."""
    return {"name": c["name"], "professor": c["professor"], "course_id": c["course_id"]}


def parse_assignments(body: dict) -> tuple[list[dict], int]:
    """body.list -> ([{"task_name", "deadline", "submitted", "is_overdue"}], total)

    is_overdue는 응답에 든 서버 날짜(current_date)로 비교한다. 로컬 시계보다 정확하다.
    """
    today = body.get("current_date") or date.today().isoformat()
    today = str(today)[:10]
    items = []
    for a in body["list"]:
        end_dt = str(a["apply_end_dt"])[:10]
        items.append({
            "task_name": a["report_nm"],
            "deadline": f"{end_dt} {a.get('apply_end_tm', '')}".strip(),
            "submitted": a.get("apply_yn") == "Y",
            "is_overdue": end_dt < today,
        })
    total = int(body.get("tot_cnt", len(items)))
    return items, total


def parse_notices(body: dict) -> tuple[list[dict], int]:
    """body.list -> ([{"title", "date", "course_name", "has_attachment"}], total)

    목록 API에는 본문(content)이 없다. 본문은 별도 도구 범위.
    """
    items = [
        {
            "title": n["boarditem_title"],
            "date": n["insert_dt_addtime"],
            "course_name": n["course_nm"],
            "has_attachment": (n.get("file_yn") or 0) > 0,
        }
        for n in body["list"]
    ]
    total = int(body.get("total", len(items)))
    return items, total


def load(api: dict, parser, **vars):
    body = fetch_json(api, **vars)
    try:
        return parser(body)
    except SourceError:
        raise
    except Exception:
        raise SourceError(ERR_PARSING_FAILED)


def load_courses() -> list[dict]:
    year, term = current_term()
    return cached("courses", lambda: load(API_COURSES, parse_courses, term_year=year, term_cd=term))


def find_course(name: str) -> dict | None:
    """과목명 부분일치로 과목 1건. 없으면 None. 과목명→course_id 변환이 여기서 일어난다."""
    for c in load_courses():
        if name in c["name"]:
            return c
    return None


# --------------------------------------------------------------- F1 도구 ---
@mcp.tool()
def list_courses() -> dict:
    """이번 학기 수강 과목 목록을 돌려준다. 과목명은 다른 도구의 course 인자에 그대로 쓴다."""
    try:
        courses = load_courses()
    except SourceError as e:
        return e.response
    return ok([public_course(c) for c in courses])


@mcp.tool()
def get_assignments(
    course: Annotated[str, Field(description="과목명 일부 또는 전체 (부분일치)")],
    only_pending: Annotated[bool, Field(description="true면 아직 마감 안 된 미제출 과제만")] = False,
) -> dict:
    """특정 과목의 과제 목록을 돌려준다. 과제 본문은 포함하지 않는다."""
    try:
        c = find_course(course)
        if c is None:
            return err_course_not_found(course)
        items, total = cached(
            f"assignments:{c['course_id']}",
            lambda: load(API_ASSIGNMENTS, parse_assignments, limit=MAX_ITEMS, **c),
        )
    except SourceError as e:
        return e.response
    if only_pending:
        # "미제출" = 아직 안 냈고 마감도 안 지난 것. 마감 지난 미제출은 "못 한 것"이라 제외.
        items = [a for a in items if not a["submitted"] and not a["is_overdue"]]
        total = len(items)
    return ok(items, total)


@mcp.tool()
def get_notices(
    course: Annotated[str, Field(description="과목명 일부 또는 전체 (부분일치)")],
    limit: Annotated[int, Field(description="가져올 공지 수 (최대 20)", ge=1, le=MAX_ITEMS)] = 10,
) -> dict:
    """특정 과목의 최근 공지 목록을 돌려준다. 본문은 포함하지 않는다."""
    try:
        c = find_course(course)
        if c is None:
            return err_course_not_found(course)
        # API에 limit 인자가 있어 서버가 limit만큼만 요청한다. 캐시 키에도 limit을 넣는다.
        items, total = cached(
            f"notices:{c['course_id']}:{limit}",
            lambda: load(API_NOTICES, parse_notices, limit=limit, **c),
        )
    except SourceError as e:
        return e.response
    return ok(items, total)


# ------------------------------------------------------------------ main ---
def dump_schema(path: str = "schema.json") -> None:
    import asyncio

    tools = [t.to_mcp_tool() for t in asyncio.run(mcp.list_tools())]
    schema = [
        {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
        for t in tools
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    print(f"saved {path} ({len(schema)} tools)", file=sys.stderr)


if __name__ == "__main__":
    if "--dump-schema" in sys.argv:
        dump_schema()
    elif "--http" in sys.argv:
        mcp.run(transport="http", host="127.0.0.1", port=8000)
    else:
        mcp.run(transport="stdio")
