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

# 사이버캠퍼스 경로. 실제 사이트 보고 채울 것.
PATH_COURSES = "/TODO/course-list"
PATH_ASSIGNMENTS = "/TODO/course/{course_id}/assignments"
PATH_NOTICES = "/TODO/course/{course_id}/notices"


# ------------------------------------------------------------ F2 응답 ---
# 성공: {"ok": true, "data": [...], "count": n, "total": N, "truncated": bool}
#   - 결과 0건은 ok=true, data=[], count=0 (실패가 아니다)
# 실패: {"ok": false, "error": "CODE", "message": "상황 + 모델이 다음에 할 일"}

def ok(items: list, total: int | None = None) -> dict:
    total = len(items) if total is None else total
    data = items[:MAX_ITEMS]
    return {
        "ok": True,
        "data": data,
        "count": len(data),
        "total": total,
        "truncated": total > len(data),
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
_cache: dict[str, tuple[float, list[dict]]] = {}


def cached(key: str, loader):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


# ------------------------------------------------------------------ fetch ---
def fetch_html(path: str) -> str:
    if not CAMPUS_COOKIE:
        raise SourceError(ERR_SESSION_EXPIRED)
    try:
        r = httpx.get(
            CAMPUS_URL + path,
            headers={"Cookie": CAMPUS_COOKIE},
            timeout=TIMEOUT_SEC,
            follow_redirects=False,   # 로그인 페이지로 가는 302를 그대로 보기 위해
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError):
        raise SourceError(ERR_SERVER_NOT_RESPONDING)
    if r.status_code >= 500:
        raise SourceError(ERR_SERVER_NOT_RESPONDING)
    if r.status_code in (301, 302, 303, 307, 308, 401, 403):
        raise SourceError(ERR_SESSION_EXPIRED)
    return r.text


# ------------------------------------------------------------------ parse ---
# TODO: 실제 사이버캠퍼스 HTML을 보고 채운다. 각 함수는 dict 리스트를 돌려준다.
#   parse_courses     -> [{"id": str, "name": str, "professor": str}]
#   parse_assignments -> [{"title": str, "due": str, "submitted": bool}]
#   parse_notices     -> [{"title": str, "date": str, "url": str}]
# 구조가 안 맞으면 SourceError(ERR_PARSING_FAILED)를 올린다.

def parse_courses(html: str) -> list[dict]:
    raise SourceError(ERR_PARSING_FAILED)


def parse_assignments(html: str) -> list[dict]:
    raise SourceError(ERR_PARSING_FAILED)


def parse_notices(html: str) -> list[dict]:
    raise SourceError(ERR_PARSING_FAILED)


def load(path: str, parser) -> list[dict]:
    html = fetch_html(path)
    try:
        return parser(html)
    except SourceError:
        raise
    except Exception:
        raise SourceError(ERR_PARSING_FAILED)


def find_course(name: str) -> dict | None:
    """과목명 부분일치로 과목 1건. 없으면 None."""
    courses = cached("courses", lambda: load(PATH_COURSES, parse_courses))
    for c in courses:
        if name in c["name"]:
            return c
    return None


# --------------------------------------------------------------- F1 도구 ---
@mcp.tool()
def list_courses() -> dict:
    """수강 중인 과목 목록을 돌려준다. 과목명은 다른 도구의 course 인자에 그대로 쓴다."""
    try:
        courses = cached("courses", lambda: load(PATH_COURSES, parse_courses))
    except SourceError as e:
        return e.response
    return ok(courses)


@mcp.tool()
def get_assignments(
    course: Annotated[str, Field(description="과목명 일부 또는 전체 (부분일치)")],
    only_pending: Annotated[bool, Field(description="true면 미제출 과제만")] = False,
) -> dict:
    """특정 과목의 과제 목록을 돌려준다."""
    try:
        c = find_course(course)
        if c is None:
            return err_course_not_found(course)
        items = cached(
            f"assignments:{c['id']}",
            lambda: load(PATH_ASSIGNMENTS.format(course_id=c["id"]), parse_assignments),
        )
    except SourceError as e:
        return e.response
    if only_pending:
        items = [a for a in items if not a.get("submitted")]
    return ok(items)


@mcp.tool()
def get_notices(
    course: Annotated[str, Field(description="과목명 일부 또는 전체 (부분일치)")],
    limit: Annotated[int, Field(description="가져올 공지 수 (최대 20)", ge=1, le=MAX_ITEMS)] = 10,
) -> dict:
    """특정 과목의 최근 공지를 최신순으로 돌려준다."""
    try:
        c = find_course(course)
        if c is None:
            return err_course_not_found(course)
        items = cached(
            f"notices:{c['id']}",
            lambda: load(PATH_NOTICES.format(course_id=c["id"]), parse_notices),
        )
    except SourceError as e:
        return e.response
    return ok(items[:limit], total=len(items))


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
