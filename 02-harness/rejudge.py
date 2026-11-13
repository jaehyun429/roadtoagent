"""
02-harness/rejudge.py — 판정 규칙을 바꾼 뒤, 이미 실행한 results 파일의 success/judge_reason만 다시 매긴다.
에이전트를 다시 돌리지 않는다. 최종 답은 logs/run-*.jsonl 의 end 이벤트에서 가져오고,
어느 로그가 어느 결과 줄인지는 (태스크 문장, steps, tokens) 이 같은 것으로 맞춘다.
사용: uv run python rejudge.py results.jsonl [--write]
"""
import argparse
import glob
import json
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import run_all

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")


def load_logs() -> list[dict]:
    """로그 파일마다 {task, steps, tokens, answer} 하나씩 (end 이벤트 없는 로그는 제외)."""
    out = []
    for p in sorted(glob.glob(str(HERE / "logs" / "run-*.jsonl"))):
        events = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        end = next((e for e in events if e["event"] == "end"), None)
        if end:
            out.append({"task": events[0]["task"], "steps": end["steps"], "tokens": end["tokens"],
                        "answer": end.get("answer"), "file": p})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--write", action="store_true", help="파일을 실제로 덮어쓴다 (없으면 바뀌는 줄만 보여줌)")
    a = ap.parse_args()
    path = HERE / a.results
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    logs = load_logs()
    tasks = {(t["task_num"], t["variation"]): t for t in run_all.TASKS}
    client = anthropic.Anthropic()

    changed = 0
    for r in rows:
        t = tasks[(r["task_num"], r["variation"])]
        hits = [l for l in logs if l["task"] == t["text"] and l["steps"] == r["steps"] and l["tokens"] == r["tokens"]]
        tag = f"{r['task_num']}{'v' if r['variation'] else ''}"
        if len(hits) != 1:
            print(f"{tag}: 로그 {len(hits)}개 매칭 — 건너뜀", file=sys.stderr)
            continue
        success, reason, jtok = run_all.judge(client, t, hits[0]["answer"], r["browser_calls"])
        mark = "변경" if success != r["success"] else "동일"
        if success != r["success"]:
            changed += 1
        print(f"{tag}: {r['success']} -> {success} ({mark}) | {reason[:120]}", file=sys.stderr)
        r["success"], r["judge_reason"], r["judge_tokens"] = success, reason, jtok

    if a.write:
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{path.name}: {changed}줄 변경, 저장함", file=sys.stderr)
    else:
        print(f"{path.name}: {changed}줄이 바뀔 예정 (--write 로 저장)", file=sys.stderr)


if __name__ == "__main__":
    main()
