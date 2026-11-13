"""
02-harness/run_all.py — tasks.json의 태스크를 차례로 돌리고 results.jsonl에 한 줄씩 기록한다.

  uv run run_all.py                 # 20개 전부 (기본 10 + 변형 10)
  uv run run_all.py --tasks 2,4     # 번호 골라서 (기본·변형 둘 다)
  uv run run_all.py --tasks 2 --set base      # base | variation | both(기본값)

한 줄 = 바깥 층(steps, tokens, elapsed_sec) + 안쪽 층 합계(browser_*) + 판정(success, judge_reason).
판정은 실행 모델과 다른 모델(JUDGE_MODEL)이 tasks.md 기대 결과와 대조해서 내린다. 판정 토큰은 하네스 비용에 섞지 않는다.
"""
import argparse
import asyncio
import json
import pathlib
import sys
import time

import anthropic

import harness

HERE = pathlib.Path(__file__).parent
TASKS = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
RESULTS = HERE / "results.jsonl"
JUDGE_MODEL = "claude-haiku-5-5"                 # 실행 모델(harness.MODEL)과 분리. 싼 모델로 충분한 일.

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"success": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["success", "reason"],
    "additionalProperties": False,
}


def judge(client: anthropic.Anthropic, task: dict, answer: str | None, browser_calls: int) -> tuple[bool, str, int]:
    """기대 결과와 최종 답을 대조해 (success, 근거 한 줄, 판정 토큰)을 돌려준다."""
    if answer is None:
        return False, "최종 답 없음 (스텝 제한 또는 API 에러)", 0
    prompt = (
        "다음은 사이버캠퍼스 에이전트의 실행 결과다. 기대 결과를 충족했는지 판정하라.\n"
        "기대 결과에 '브라우저 호출 로그 없음'이 있으면 browser_calls가 0이어야 충족이다.\n"
        "수치나 제목은 기대 결과와 같아야 한다. 추정이나 '확인하지 못함'은 불충족이다.\n\n"
        f"[태스크] {task['text']}\n[기대 결과] {task['expected']}\n[browser_calls] {browser_calls}\n[최종 답]\n{answer}"
    )
    # max_tokens에는 모델의 생각(thinking)도 포함된다. 512로 두니 생각만 하다 JSON이 잘렸다(JSONDecodeError). 넉넉히 주고 effort는 낮춘다.
    r = client.messages.create(
        model=JUDGE_MODEL, max_tokens=4096,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    jtok = r.usage.input_tokens + r.usage.output_tokens
    if r.stop_reason != "end_turn":                  # 잘렸거나 거절이면 판정 보류: 사람이 채운다
        return False, f"판정 실패 (stop_reason={r.stop_reason}) — 수동 판정 필요", jtok
    data = json.loads(next(b.text for b in r.content if b.type == "text"))
    return bool(data["success"]), data["reason"], jtok


def select(nums: set[int] | None, which: str) -> list[dict]:
    rows = [t for t in TASKS if nums is None or t["task_num"] in nums]
    if which == "base":
        rows = [t for t in rows if not t["variation"]]
    elif which == "variation":
        rows = [t for t in rows if t["variation"]]
    return rows


async def main() -> None:
    # 태스크마다 asyncio.run()을 따로 돌리면 두 번째부터 "Event loop is closed"로 MCP 연결이 실패한다
    # (harness의 transport 객체가 첫 이벤트 루프에 묶임). 그래서 전체를 하나의 루프 안에서 await 한다.
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", help="예: 2,4,10")
    ap.add_argument("--set", choices=["base", "variation", "both"], default="both")
    a = ap.parse_args()
    nums = {int(x) for x in a.tasks.split(",")} if a.tasks else None
    rows = select(nums, a.set)
    client = anthropic.Anthropic()
    print(f"{len(rows)}개 실행 → {RESULTS}", file=sys.stderr)

    for t in rows:
        tag = f"{t['task_num']}{'v' if t['variation'] else ''}"
        print(f"\n===== task {tag}: {t['text']}", file=sys.stderr)
        s = await harness.main(t["text"])
        success, reason, jtok = judge(client, t, s["answer"], s["browser_calls"])
        row = {
            "task_num": t["task_num"], "variation": t["variation"], "date": time.strftime("%Y-%m-%d"),
            "model": harness.MODEL, "success": success,
            "channel": "browser" if s["browser_calls"] > 0 else "tool",      # design.md 4번: browser_fallback이 한 번이라도 불렸나
            "steps": s["steps"], "tokens": s["tokens"], "elapsed_sec": s["elapsed_sec"],
            "browser_calls": s["browser_calls"], "browser_steps": s["browser_steps"],
            "browser_tokens": s["browser_tokens"], "browser_sec": s["browser_sec"],
            "judge_reason": reason, "judge_tokens": jtok,
        }
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"RESULT {tag}: success={success} channel={row['channel']} steps={row['steps']} tokens={row['tokens']} "
              f"browser={row['browser_calls']}/{row['browser_steps']}/{row['browser_tokens']} | {reason}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
