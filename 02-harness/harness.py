"""
02-harness/harness.py — 과제 00 루프 + MCP 클라이언트(도구 3개) + browser_fallback(도구 1개)

과제 00 agent.py에서 바뀐 자리:
  TOOLS    : 손으로 쓴 스키마 3개   →  MCP list_tools() 결과 3개 + 손으로 쓴 browser_fallback 1개
  TOOL_MAP : 이름 → 내 파이썬 함수  →  3개는 mcp.call_tool(name, args), browser_fallback은 run_bua
while 루프(스텝 제한, 도구 호출, 결과 되돌려주기)는 그대로.

실행:  uv run harness.py                     # tasks.md 1번
       uv run harness.py "딥러닝 과목 출석률 알려줘"
"""
import asyncio
import json
import os
import pathlib
import sys

from browser_use import Agent, Browser, ChatGoogle
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from google import genai

HERE = pathlib.Path(__file__).parent
load_dotenv(HERE / ".env")                      # GEMINI_API_KEY, CNU_ID, CNU_PASSWORD (01 서버의 쿠키는 서버 쪽 .env가 읽음)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")   # browser-use 사용 통계 전송 끄기

MODEL = "gemini-3.6-flash"
MAX_STEPS = 15
BUA_MAX_STEPS = 20                               # 기존 BUA(test_finetuned.py)와 같은 값
SERVER_DIR = (HERE / ".." / "01-mcp-server").resolve()
CAMPUS_HOME = "https://dcs-lcms.cnu.ac.kr"

# MCP 서버 실행 명령: Claude Desktop 설정과 같은 명령을 쓴다.
# (서버는 자기 .venv에 cryptography/httpx가 있어서 uv run --directory 로 띄워야 한다)
transport = StdioTransport(
    command="uv",
    args=["run", "--directory", str(SERVER_DIR), "server.py"],
    env={**os.environ, "FASTMCP_SHOW_SERVER_BANNER": "0"},
)

# ---- 손으로 쓴 도구 1개: 설명문은 design.md 2번 그대로 ----
BROWSER_FALLBACK_TOOL = {
    "type": "function",
    "name": "browser_fallback",
    "description": (
        "공지사항, 과제, 이번학기 수강 과목 확인과 같은 단순 작업은 이 도구 말고 다른 전용 도구를 사용하세요. "
        "다른 도구에서 COURSE_NOT_FOUND와 같은 에러가 발생한 경우엔, 과목이 없어서 발생한 문제이므로 이 도구를 추가 실행하지 마세요. "
        "출석, 계획서, 강의자료 확인과 같은 다른 도구로 정보를 얻을 수 없는 일의 경우에만 사용을 하세요. "
        "앞서 말한 다른 도구로 먼저 해결해보고 안되는 경우에만 사용을 하세요. 느리고 토큰이 많이 드는 작업입니다."
    ),
    "parameters": {
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


def to_gemini_tool(tool) -> dict:
    """MCP Tool(name, description, input_schema) → 과제 00에서 손으로 쓰던 딕셔너리 모양."""
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.input_schema,
    }


async def run_bua(name: str, args: dict):
    """기존 BUA 호출(test_finetuned.py)을 그대로 — 결과를 dict로 돌려준다."""
    user_id, user_pw = os.environ.get("CNU_ID", ""), os.environ.get("CNU_PASSWORD", "")
    if not (user_id and user_pw):
        return "ERROR: CNU_ID / CNU_PASSWORD 가 02-harness/.env 에 없음"
    browser = Browser(
        "chromium",                              # 로컬 크롬 (기본값은 Browser Use 클라우드라 꼭 지정)
        headless=True,
        allowed_domains=["dcs-lcms.cnu.ac.kr", "dcs-learning.cnu.ac.kr"],
    )
    agent = Agent(
        task=f"사이버캠퍼스 {CAMPUS_HOME} 에서 다음을 수행해줘. 결과가 없으면 없다고 알려주면 돼: {args['task']}",
        llm=ChatGoogle(MODEL),                   # 루프와 같은 모델 (비교 실험에서 변수 하나 줄이기)
        browser=browser,
        sensitive_data={CAMPUS_HOME: {"x_user_id": user_id, "x_user_pw": user_pw}},
    )
    try:
        history = await agent.run(max_steps=BUA_MAX_STEPS)
    except Exception as e:
        return f"ERROR: browser_fallback 실패 — {e}"
    return {
        "ok": bool(history.is_successful()),
        "result": history.final_result(),
        "browser_steps": history.number_of_steps(),
        "elapsed_sec": round(history.total_duration_seconds(), 1),
    }


async def main(task: str) -> None:
    async with Client(transport) as mcp:
        # ---- TOOLS: list_tools() 결과 3개 + 손으로 쓴 1개 ----
        mcp_tools = await mcp.list_tools()
        TOOLS = [to_gemini_tool(t) for t in mcp_tools] + [BROWSER_FALLBACK_TOOL]
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

        TOOL_MAP = {t.name: call_mcp for t in mcp_tools}
        TOOL_MAP["browser_fallback"] = run_bua

        # ---- 아래는 과제 00 루프 그대로 ----
        client = genai.Client()
        interaction = client.interactions.create(model=MODEL, input=task, tools=TOOLS)

        tokens = 0
        step = 0
        while step < MAX_STEPS:
            step += 1
            calls = [s for s in interaction.steps if s.type == "function_call"]
            if not calls:
                print(interaction.output_text)
                print(f"총 스텝: {step}")
                tokens += interaction.usage.total_tokens
                print(f"총 토큰 사용량: {tokens}")
                break
            results = []
            for c in calls:
                if c.name in TOOL_MAP:
                    result = await TOOL_MAP[c.name](c.name, c.arguments)
                else:
                    result = f"ERROR: Unknown tool {c.name}"
                results.append({
                    "type": "function_result",
                    "name": c.name,
                    "call_id": c.id,
                    "result": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                })
                print(f"STEP {step} / {c.name} {c.arguments} / {json.dumps(result, ensure_ascii=False)[:200]}")
            tokens += interaction.usage.total_tokens
            interaction = client.interactions.create(
                model=MODEL,
                previous_interaction_id=interaction.id,
                tools=TOOLS,
                input=results,
            )
        else:
            print("STEP_LIMIT")
            print(f"총 스텝: {step}")
            print(f"총 토큰 사용량: {tokens}")


if __name__ == "__main__":
    # 기본값 = tasks.md 1번
    task = sys.argv[1] if len(sys.argv) > 1 else "딥러닝 공지사항에 기말고사 관련 공지 올라왔어?"
    asyncio.run(main(task))
