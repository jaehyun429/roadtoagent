"""
02-harness/harness.py — 과제 00 루프 + MCP 클라이언트(도구 3개)

과제 00 agent.py에서 바뀐 것은 두 자리뿐이다.
  TOOLS    : 손으로 쓴 스키마 3개        →  MCP 서버 list_tools() 결과를 같은 모양으로 변환
  TOOL_MAP : 이름 → 내 파이썬 함수       →  이름 → mcp.call_tool(name, args)
while 루프(스텝 제한, 도구 호출, 결과 되돌려주기)는 그대로.

실행:  uv run harness.py                     # tasks.md 1번
       uv run harness.py "딥러닝 공지에서 가장 최신글 제목 알려줘"
"""
import asyncio
import json
import os
import pathlib
import sys

from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from google import genai

HERE = pathlib.Path(__file__).parent
load_dotenv(HERE / ".env")                      # GEMINI_API_KEY (01 서버의 쿠키는 서버 쪽 .env가 읽음)

MODEL = "gemini-3.6-flash"
MAX_STEPS = 15
SERVER_DIR = (HERE / ".." / "01-mcp-server").resolve()

# MCP 서버 실행 명령: Claude Desktop 설정과 같은 명령을 쓴다.
# (서버는 자기 .venv에 cryptography/httpx가 있어서 uv run --directory 로 띄워야 한다)
transport = StdioTransport(
    command="uv",
    args=["run", "--directory", str(SERVER_DIR), "server.py"],
    env={**os.environ, "FASTMCP_SHOW_SERVER_BANNER": "0"},
)


def to_gemini_tool(tool) -> dict:
    """MCP Tool(name, description, input_schema) → 과제 00에서 손으로 쓰던 딕셔너리 모양."""
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.input_schema,
    }


async def main(task: str) -> None:
    async with Client(transport) as mcp:
        # ---- TOOLS: 손으로 쓰던 자리를 list_tools() 결과로 ----
        mcp_tools = await mcp.list_tools()
        TOOLS = [to_gemini_tool(t) for t in mcp_tools]
        print("TOOLS:", [t["name"] for t in TOOLS])

        # ---- TOOL_MAP: 어떤 이름이든 전부 mcp.call_tool 로 간다 ----
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
