"""
01-mcp-server: 과목 조회 MCP 서버

실행
  stdio : uv run server.py
  HTTP  : uv run server.py --http            (http://127.0.0.1:8000/mcp)
  스키마: uv run server.py --dump-schema     (schema.json 저장)

환경변수 (.env 또는 쉘)
  COURSE_URL     과목 목록 페이지 URL
  COURSE_COOKIE  로그인 세션 쿠키 문자열
"""

import json
import os
import sys
import time
from typing import Annotated

import httpx
from fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("courses")

# ---------------------------------------------------------------- 설정 ---
COURSE_URL = os.environ.get("COURSE_URL", "")
COURSE_COOKIE = os.environ.get("COURSE_COOKIE", "")
CACHE_TTL_SEC = 300          # 캐시는 서버 내부 TTL로만. clear_cache 도구 없음.
MAX_ITEMS = 20               # 한 응답 최대 건수. 넘으면 잘라서 truncated=True
TIMEOUT_SEC = 10


# ------------------------------------------------------------ F2 응답 ---
# 성공: {"ok": true, "data": [...], "count": n, "total": N, "truncated": bool}
#   - 결과 0건은 ok=true, data=[], count=0 (실패가 아니다)
# 실패: {"ok": false, "error": "CODE", "message": "상황 + 모델이 다음에 할 일"}
#   - 코드는 전부 대문자. 모델이 문자열 그대로 비교한다.
#   - 재시도가 통하는 에러(SERVER_NOT_RESPONDING)와 안 통하는 에러
#     (SESSION_EXPIRED, PARSING_FAILED, COURSE_NOT_FOUND)를 message로 구분.

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
    """fetch/parse 단계의 실패를 F2 응답 dict로 들고 올라오는 예외."""

    def __init__(self, response: dict):
        super().__init__(response["error"])
        self.response = response


# -------------------------------------------------------------- 캐시 ---
_cache: dict[str, tuple[float, list[dict]]] = {}


def cached(key: str, loader):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


# ------------------------------------------------------- fetch / parse ---
def fetch_html() -> str:
    if not COURSE_COOKIE:
        raise SourceError(ERR_SESSION_EXPIRED)
    try:
        r = httpx.get(
            COURSE_URL,
            headers={"Cookie": COURSE_COOKIE},
            timeout=TIMEOUT_SEC,
            follow_redirects=False,
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError):
        raise SourceError(ERR_SERVER_NOT_RESPONDING)
    if r.status_code >= 500:
        raise SourceError(ERR_SERVER_NOT_RESPONDING)
    # 로그인 페이지로 튕기거나 401/403이면 세션 만료로 본다
    if r.status_code in (301, 302, 303, 307, 308, 401, 403):
        raise SourceError(ERR_SESSION_EXPIRED)
    return r.text


def parse_courses(html: str) -> list[dict]:
    """
    TODO: 실제 사이트 구조에 맞게 작성.
    반환 형식(각 항목): {"name": str, "code": str, "professor": str, "credits": int, ...}
    구조가 안 맞아 0건이 나오거나 예외가 나면 PARSING_FAILED로 올린다.
    """
    raise SourceError(ERR_PARSING_FAILED)


def load_courses() -> list[dict]:
    html = fetch_html()
    try:
        courses = parse_courses(html)
    except SourceError:
        raise
    except Exception:
        raise SourceError(ERR_PARSING_FAILED)
    return courses


# --------------------------------------------------------------- F1 도구 ---
@mcp.tool()
def list_courses(
    keyword: Annotated[str, Field(description="과목명에 포함될 문자열. 비우면 전체 목록")] = "",
) -> dict:
    """수강 가능한 과목 목록을 돌려준다.

    keyword가 있으면 과목명에 그 문자열이 포함된 과목만 돌려준다.
    결과는 최대 20건이며, 더 있으면 truncated=true와 전체 건수(total)가 함께 온다.
    """
    try:
        courses = cached("courses", load_courses)
    except SourceError as e:
        return e.response
    if keyword:
        courses = [c for c in courses if keyword in c.get("name", "")]
    return ok(courses)


@mcp.tool()
def get_course(
    name: Annotated[str, Field(description="과목명 일부 또는 전체 (부분일치)")],
) -> dict:
    """과목명 부분일치로 과목 상세 1건을 돌려준다.

    여러 개가 걸리면 첫 번째를 돌려주고 candidates에 나머지 이름을 담아준다.
    없으면 COURSE_NOT_FOUND. list_courses로 정확한 이름을 먼저 확인하면 좋다.
    """
    try:
        courses = cached("courses", load_courses)
    except SourceError as e:
        return e.response
    matched = [c for c in courses if name in c.get("name", "")]
    if not matched:
        return err_course_not_found(name)
    result = {"ok": True, "data": matched[0]}
    if len(matched) > 1:
        result["candidates"] = [c.get("name") for c in matched[1:MAX_ITEMS]]
    return result


# ------------------------------------------------------------------ main ---
def dump_schema(path: str = "schema.json") -> None:
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    tools = [t.to_mcp_tool() for t in tools]
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
