"""
02-harness/harness.py — 과제 00 루프 + MCP 클라이언트(도구 3개) + browser_fallback(도구 1개)

과제 00 agent.py에서 바뀐 자리:
  TOOLS    : 손으로 쓴 스키마 3개   →  MCP list_tools() 결과 3개 + 손으로 쓴 browser_fallback 1개
  TOOL_MAP : 이름 → 내 파이썬 함수  →  3개는 mcp.call_tool(name, args), browser_fallback은 run_bua (호출 상한 가드 포함)
  모델 API : Gemini Interactions    →  Anthropic Messages (2026-11-13 교체. Gemini는 서버가 대화를 들고 있어 previous_interaction_id만
             넘겼지만, Messages API는 무상태라 messages 리스트를 내가 쌓아서 매번 전부 보낸다 — 과제 00 NOTES 1번 그대로)
while 루프(스텝 제한, 도구 호출, 결과 되돌려주기)는 그대로.

실행:  uv run harness.py                     # tasks.md 1번
       uv run harness.py "딥러닝 과목 출석률 알려줘"
       uv run harness.py --resume logs/paused-<시각>.json   # SESSION_EXPIRED/LOGIN_FAILED 로 멈춘 실행 재개 (design.md 3번)
"""
import asyncio
import json
import os
import pathlib
import sys
import time

import anthropic
from browser_use import Agent, Browser, ChatAnthropic
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

HERE = pathlib.Path(__file__).parent
load_dotenv(HERE / ".env")                      # ANTHROPIC_API_KEY, CNU_ID, CNU_PASSWORD (01 서버의 쿠키는 서버 쪽 .env가 읽음)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")   # browser-use 사용 통계 전송 끄기

MODEL = "claude-sonnet-5-5"
MAX_STEPS = 15                                   # 바깥 루프: API 호출 1회 = 1스텝
MAX_TOKENS = 16000                               # 응답 1회 출력 상한 (스트리밍 없이 쓸 때 권장값)
BUA_MAX_STEPS = 20                               # 기존 BUA(test_finetuned.py)와 같은 값
BUA_MAX_CALLS = 4                                # 태스크 1개당 browser_fallback 호출 상한 (예산 가드, design.md 3번)
SERVER_DIR = (HERE / ".." / "01-mcp-server").resolve()
CAMPUS_HOME = "https://dcs-lcms.cnu.ac.kr"
LOG_DIR = HERE / "logs"                          # 실행 1회 = 파일 1개, 도구 호출 1건 = JSON 1줄 (결과 전문)
# 사용자 조치가 필요한 에러: 여기서 멈추고 상태를 저장한다 (design.md 3번). 자동 로그인이 생긴 뒤로는
# SESSION_EXPIRED가 거의 안 나고, 대신 비밀번호 오류·사이트 점검 때 LOGIN_FAILED가 같은 자리에 온다.
PAUSE_ERRORS = {"SESSION_EXPIRED", "LOGIN_FAILED"}
RESUME_MESSAGE = "쿠키(로그인)가 갱신되었습니다. 방금 실패한 도구를 같은 인자로 다시 호출하세요."

# MCP 서버 실행 명령: Claude Desktop 설정과 같은 명령을 쓴다.
# (서버는 자기 .venv에 cryptography/httpx가 있어서 uv run --directory 로 띄워야 한다)
transport = StdioTransport(
    command="uv",
    args=["run", "--directory", str(SERVER_DIR), "server.py"],
    env={**os.environ, "FASTMCP_SHOW_SERVER_BANNER": "0"},
)

# ---- 손으로 쓴 도구 1개: 설명문은 design.md 2번 그대로. Anthropic 도구 형식 = {name, description, input_schema} ----
BROWSER_FALLBACK_TOOL = {
    "name": "browser_fallback",
    "description": (
        "공지사항, 과제, 이번학기 수강 과목 확인과 같은 단순 작업은 이 도구 말고 다른 전용 도구를 사용하세요. "
        "다른 도구에서 COURSE_NOT_FOUND와 같은 에러가 발생한 경우엔, 과목이 없어서 발생한 문제이므로 이 도구를 추가 실행하지 마세요. "
        "출석, 계획서, 강의자료 확인과 같은 다른 도구로 정보를 얻을 수 없는 일의 경우에만 사용을 하세요. "
        "한 번 호출에 한 과목, 한 페이지만. 여러 과목이면 과목마다 따로 부르세요. "
        "앞서 말한 다른 도구로 먼저 해결해보고 안되는 경우에만 사용을 하세요. 느리고 토큰이 많이 드는 작업입니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "브라우저로 사이버캠퍼스에서 수행할 일을 한국어 문장으로 (예: 딥러닝 과목 출석률 확인)",
            }
        },
        "required": ["task"],
    },
}


def to_claude_tool(tool) -> dict:
    """MCP Tool(name, description, input_schema) → Anthropic 도구 dict. 키 이름이 MCP와 같아서 그대로 옮기면 된다."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.input_schema,
    }


async def run_bua(name: str, args: dict):
    """기존 BUA 호출(test_finetuned.py)을 그대로 — 결과를 dict로 돌려준다."""
    user_id, user_pw = os.environ.get("CNU_ID", ""), os.environ.get("CNU_PASSWORD", "")
    if not (user_id and user_pw):
        return "ERROR: CNU_ID / CNU_PASSWORD 가 02-harness/.env 에 없음"
    browser = Browser(
        "chromium",                              # 로컬 크롬 (기본값은 Browser Use 클라우드라 꼭 지정)
        headless=True,
        # 강의계획서 전문은 "강의계획서 바로가기" 팝업이 여는 학사 사이트(cnuit)에 있다. 11/13 실행에서
        # 이 도메인이 막혀 강의계획서 태스크 6개가 전부 실패해 추가함.
        allowed_domains=["dcs-lcms.cnu.ac.kr", "dcs-learning.cnu.ac.kr", "cnuit.cnu.ac.kr"],
    )
    agent = Agent(
        task=f"사이버캠퍼스 {CAMPUS_HOME} 에서 다음을 수행해줘. 결과가 없으면 없다고 알려주면 돼: {args['task']}",
        llm=ChatAnthropic(MODEL),                # 루프와 같은 모델 (비교 실험에서 변수 하나 줄이기)
        browser=browser,
        sensitive_data={CAMPUS_HOME: {"x_user_id": user_id, "x_user_pw": user_pw}},
    )
    try:
        history = await agent.run(max_steps=BUA_MAX_STEPS)
    except Exception as e:
        return f"ERROR: browser_fallback 실패 — {e}"
    # 안쪽 층 수치는 루프의 200자 출력에 잘리므로 한 줄로 따로 남긴다 (stderr)
    print(f"BUA: steps={history.number_of_steps()} tokens={history.usage['input'] + history.usage['output']} "
          f"sec={round(history.total_duration_seconds(), 1)} ok={bool(history.is_successful())}", file=sys.stderr)
    return {
        "ok": bool(history.is_successful()),
        "result": history.final_result(),
        "browser_steps": history.number_of_steps(),
        "browser_tokens": history.usage["input"] + history.usage["output"],   # BUA 안쪽 LLM 토큰 (바깥 루프와 별도 층)
        "elapsed_sec": round(history.total_duration_seconds(), 1),
    }


def jsonable(messages: list) -> list:
    """messages를 JSON으로 저장할 수 있게. assistant 턴의 블록은 SDK 객체라 dict로 바꾼다 (재개 때 dict 그대로 API에 보내도 된다)."""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            content = [b if isinstance(b, dict) else b.model_dump(exclude_none=True) for b in content]
        out.append({"role": m["role"], "content": content})
    return out


def logger(task: str):
    """logs/run-<시각>.jsonl 에 한 줄씩 덧붙이는 함수를 돌려준다. 터미널 출력은 200자로 자르지만 파일엔 전부 남긴다."""
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    def log(**row):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%H:%M:%S"), **row}, ensure_ascii=False) + "\n")
    log(event="start", task=task, model=MODEL)
    print("LOG:", path)
    return log


async def main(task: str, browser_only: bool = False, state: dict | None = None) -> dict:
    """태스크 1개 실행. results.jsonl 한 줄을 만들 재료(바깥 층 + 안쪽 층 합계)를 dict로 돌려준다.
    browser_only: TODO 4 비교 실험용. 루프는 그대로 두고 모델에 주는 도구를 browser_fallback 하나로 줄여
    도구로 풀던 태스크를 브라우저 경로로 강제한다 (같은 일을 두 경로로 한 비용 비교).
    state: --resume 로 읽은 저장 상태. 있으면 스텝·토큰·messages를 이어받고 모델에 RESUME_MESSAGE를 붙여 계속한다."""
    log = logger(task)
    started = time.time()                                    # 소요 시간: 명령 입력 ~ 최종 답 (design.md 4번)
    inner = {"browser_calls": 0, "browser_steps": 0, "browser_tokens": 0, "browser_sec": 0.0}   # 안쪽 층 합계
    async with Client(transport) as mcp:
        # ---- TOOLS: list_tools() 결과 3개 + 손으로 쓴 1개 ----
        mcp_tools = await mcp.list_tools()
        TOOLS = [to_claude_tool(t) for t in mcp_tools] + [BROWSER_FALLBACK_TOOL]
        if browser_only:
            TOOLS = [BROWSER_FALLBACK_TOOL]
        print("TOOLS:", [t["name"] for t in TOOLS])

        # ---- TOOL_MAP: 서버 도구 3개는 call_mcp, browser_fallback은 run_bua (A안: 분기는 여기 한 곳) ----
        async def call_mcp(name: str, args: dict):
            try:
                r = await mcp.call_tool(name, args)
                # 서버가 dict를 돌려주면 structured_content에 그대로 들어온다 ({"ok":...})
                if r.structured_content is not None:
                    return r.structured_content
                return "".join(getattr(c, "text", "") for c in r.content)
            except Exception as e:
                return f"ERROR: {e}"

        # 예산 가드: 모델 판단에 맡기지 않고 하네스가 센다. 상한을 넘으면 브라우저를 띄우지 않고 거절 응답만 돌려준다.
        bua_calls = 0

        async def guarded_bua(name: str, args: dict):
            nonlocal bua_calls
            if bua_calls >= BUA_MAX_CALLS:
                return {"ok": False, "error": "BROWSER_BUDGET_EXCEEDED",
                        "message": f"browser_fallback 호출 상한({BUA_MAX_CALLS}회) 초과. 더 시도하지 말고 지금까지의 결과로 사용자에게 알리세요."}
            bua_calls += 1
            result = await run_bua(name, args)
            if isinstance(result, dict):                     # 안쪽 층 수치를 합계에 더한다 (10번처럼 여러 번 부르면 합산)
                inner["browser_calls"] += 1
                inner["browser_steps"] += result["browser_steps"]
                inner["browser_tokens"] += result["browser_tokens"]
                inner["browser_sec"] += result["elapsed_sec"]
            return result

        TOOL_MAP = {t.name: call_mcp for t in mcp_tools}
        TOOL_MAP["browser_fallback"] = guarded_bua

        # ---- 아래는 과제 00 루프. 모델 호출 두 줄과 messages 쌓기만 Anthropic 식으로 바뀜 ----
        client = anthropic.Anthropic()                           # ANTHROPIC_API_KEY 를 환경에서 읽는다
        messages = [{"role": "user", "content": task}]           # 첫 호출엔 태스크 하나
        elapsed_before = 0.0                                      # 멈추기 전까지 쓴 시간 (재개 후 합산)
        if state:                                                 # ---- 재개: 저장해 둔 상태를 그대로 이어받는다 ----
            messages = state["messages"]
            inner.update(state["inner"])
            elapsed_before = state["elapsed_sec"]
            # 마지막 user 턴(실패한 tool_result)에 "다시 부르라"는 문장을 덧붙인다. 별도 턴으로 넣지 않는 이유:
            # tool_result 뒤에 바로 같은 role이 또 오면 안 되고, 한 턴 안의 블록 추가는 허용된다.
            messages[-1]["content"].append({"type": "text", "text": RESUME_MESSAGE})
            log(event="resume", step=state["step"], tokens=state["tokens"], paused_file=state["file"])
            print(f"RESUME: step {state['step']} 부터, 누적 토큰 {state['tokens']}, 저장 파일 {state['file']}")

        def ask():
            # 안전 분류기가 거절(stop_reason=refusal)하면 서버가 다른 모델로 같은 요청을 이어 돌리는 옵션(fallbacks).
            return client.beta.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, tools=TOOLS, messages=messages,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )

        def summary(answer, stop):
            return {"steps": step, "tokens": tokens, "elapsed_sec": round(elapsed_before + time.time() - started, 1),
                    "answer": answer, "stop": stop, **inner, "browser_sec": round(inner["browser_sec"], 1)}

        tokens = state["tokens"] if state else 0
        step = state["step"] if state else 0

        def pause(step: int, failed: dict) -> str:
            """상태 저장: 몇 번째 스텝, 어떤 태스크, 지금까지의 messages 전체, 토큰 누적량 (design.md 3번) + 안쪽 층 합계와 시간."""
            path = LOG_DIR / f"paused-{time.strftime('%Y%m%d-%H%M%S')}.json"
            saved = {"task": task, "step": step, "tokens": tokens, "inner": inner,
                     "elapsed_sec": round(elapsed_before + time.time() - started, 1),
                     "failed": failed, "browser_only": browser_only,
                     "messages": jsonable(messages)}
            path.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
            return str(path)

        while step < MAX_STEPS:
            step += 1
            try:
                response = ask()
            except anthropic.APIError as e:                      # 402/429/5xx 등. 지금은 기록하고 멈춘다 (재개는 design.md 3번)
                print(f"API_ERROR: {e}")
                log(event="api_error", step=step, error=str(e)[:300])
                return summary(None, "api_error")
            tokens += response.usage.input_tokens + response.usage.output_tokens
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                answer = "".join(b.text for b in response.content if b.type == "text")
                print(answer)
                print(f"총 스텝: {step}")
                print(f"총 토큰 사용량: {tokens}")
                log(event="end", steps=step, tokens=tokens, stop_reason=response.stop_reason, answer=answer)
                return summary(answer, response.stop_reason)
            messages.append({"role": "assistant", "content": response.content})   # 모델이 고른 도구 호출을 이력에
            results = []
            for c in calls:
                if c.name in TOOL_MAP:
                    result = await TOOL_MAP[c.name](c.name, c.input)
                else:
                    result = f"ERROR: Unknown tool {c.name}"
                results.append({
                    "type": "tool_result",
                    "tool_use_id": c.id,                                           # 어느 호출의 결과인지 id로 짝 맞춤
                    "content": json.dumps(result, ensure_ascii=False),
                })
                print(f"STEP {step} / {c.name} {c.input} / {json.dumps(result, ensure_ascii=False)[:200]}")
                log(event="tool", step=step, name=c.name, args=c.input, result=result)
            messages.append({"role": "user", "content": results})                 # 도구 결과를 이력에 → 다음 호출 때 전부 다시 보냄
            # ---- 사용자 조치가 필요한 에러면 여기서 멈춘다. 모델에게 돌려보내 봐야 할 수 있는 게 없다 ----
            failed = next(({"tool": c.name, "args": c.input, "error": r["error"], "message": r.get("message")}
                           for c, r in zip(calls, [json.loads(x["content"]) for x in results])
                           if isinstance(r, dict) and r.get("error") in PAUSE_ERRORS), None)
            if failed:
                path = pause(step, failed)
                log(event="paused", step=step, tokens=tokens, failed=failed, file=path)
                print(f"PAUSED: {failed['error']} — {failed['message']}")
                print(f"상태 저장: {path}")
                print(f"조치 후 재개: uv run harness.py --resume {path}")
                return summary(None, "paused")
        else:
            print("STEP_LIMIT")
            print(f"총 스텝: {step}")
            print(f"총 토큰 사용량: {tokens}")
            log(event="step_limit", steps=step, tokens=tokens)
            return summary(None, "step_limit")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?", default="딥러닝 공지사항에 기말고사 관련 공지 올라왔어?")   # 기본값 = tasks.md 1번
    ap.add_argument("--browser-only", action="store_true")
    ap.add_argument("--resume", metavar="PAUSED_JSON", help="PAUSED 로 멈춘 실행을 저장 파일에서 이어서 돌린다")
    a = ap.parse_args()
    if a.resume:
        st = json.loads(pathlib.Path(a.resume).read_text(encoding="utf-8"))
        st["file"] = a.resume
        asyncio.run(main(st["task"], browser_only=st.get("browser_only", False), state=st))
    else:
        asyncio.run(main(a.task, browser_only=a.browser_only))
