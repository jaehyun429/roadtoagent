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

import json
import os
import sys
import time
from datetime import date
from typing import Annotated

import httpx
from fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("cybercampus")

# ---------------------------------------------------------------- 설정 ---
CAMPUS_URL = os.environ.get("CAMPUS_URL", "").rstrip("/")
CAMPUS_COOKIE = os.environ.get("CAMPUS_COOKIE", "")
CACHE_TTL_SEC = 300          # 캐시는 서버 내부 TTL로만. clear_cache 도구 없음.
MAX_ITEMS = 20               # 한 응답 최대 건수. 넘으면 잘라서 truncated=True
TIMEOUT_SEC = 10

# ----- 세 API 정의. Network 탭에서 확인한 값으로 채울 것 (TODO) -----
# path    : CAMPUS_URL 뒤에 붙는 경로
# method  : GET 또는 POST
# payload : 요청 본문. {term_year}, {term_cd}, {course_id}는 서버가 채운다.
#           모델은 이 값들을 몰라도 된다 — 그래서 도구 인자에 없다.
API_COURSES = {
    "method": "POST",
    "path": "/TODO/course/list",
    "payload": {"term_year": "{term_year}", "term_cd": "{term_cd}"},
}
API_ASSIGNMENTS = {
    "method": "POST",
    "path": "/TODO/report/list",
    "payload": {"course_id": "{course_id}"},
}
API_NOTICES = {
    "method": "POST",
    "path": "/TODO/board/list",
    "payload": {"course_id": "{course_id}"},
}
PAYLOAD_AS_JSON = True       # Network 탭 Payload가 Form Data면 False로


def current_term() -> tuple[str, str]:
    """현재 학기. 1학기 코드는 "10"으로 확인됨. 2학기 코드는 2학기 페이지에서 확인할 것 (TODO)."""
    today = date.today()
    term_cd = "10" if 3 <= today.month <= 8 else "20"
    return str(today.year), term_cd


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
def fetch_json(api: dict, **vars) -> dict:
    """API를 호출해 body(dict)를 돌려준다. 실패는 SourceError로 올린다."""
    if not CAMPUS_COOKIE:
        raise SourceError(ERR_SESSION_EXPIRED)

    payload = {k: v.format(**vars) if isinstance(v, str) else v for k, v in api["payload"].items()}
    kwargs = {"json": payload} if PAYLOAD_AS_JSON else {"data": payload}
    if api["method"] == "GET":
        kwargs = {"params": payload}

    try:
        r = httpx.request(
            api["method"],
            CAMPUS_URL + api["path"],
            headers={"Cookie": CAMPUS_COOKIE},
            timeout=TIMEOUT_SEC,
            follow_redirects=False,   # 로그인 페이지로 가는 302를 그대로 보기 위해
            **kwargs,
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
    # TODO: 쿠키 빼고 한 번 쳐서 세션 만료 때 header.code가 뭔지 확인하고 아래 조건 확정
    header = doc.get("header") or {}
    if header.get("code") != 200:
        raise SourceError(ERR_SESSION_EXPIRED)
    body = doc.get("body")
    if not isinstance(body, dict):
        raise SourceError(ERR_PARSING_FAILED)
    return body


# ------------------------------------------------------------------ parse ---
# 파서가 곧 필터다. 설계한 필드 외에는 아무것도 넘기지 않는다.
# (응답에는 교수 전화번호, 암호화 토큰, 과제 본문 HTML 수천 자가 섞여 온다)

def parse_courses(body: dict) -> list[dict]:
    """body.course_list -> [{"name", "professor", "course_id"}]"""
    return [
        {
            "name": c["course_nm"],
            "professor": c["prof_nm"],
            "course_id": c["course_id"],
        }
        for c in body["course_list"]
    ]


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
    return ok(courses)


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
            lambda: load(API_ASSIGNMENTS, parse_assignments, course_id=c["course_id"]),
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
        items, total = cached(
            f"notices:{c['course_id']}",
            lambda: load(API_NOTICES, parse_notices, course_id=c["course_id"]),
        )
    except SourceError as e:
        return e.response
    return ok(items[:limit], total)


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
