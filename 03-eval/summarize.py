"""results-x3.jsonl → 태스크별 성공률·토큰/시간 평균과 편차(표준편차) 표.  사용: python3 summarize.py [결과파일]"""
import json, statistics as st, sys
from collections import defaultdict
path = sys.argv[1] if len(sys.argv) > 1 else "/Users/jaehyun/Desktop/roadtoagent/02-harness/results-x3.jsonl"
rows = [json.loads(l) for l in open(path) if l.strip()]
by = defaultdict(list)
for r in rows: by[(r["variation"], r["task_num"])].append(r)
sd = lambda xs: st.pstdev(xs) if len(xs) > 1 else 0
print(f"행 {len(rows)}개, run 번호 {sorted({r['run'] for r in rows})}\n")
print("| # | 성공 | 경로 | 스텝 | 총 토큰 평균±편차 | 초 평균±편차 | BUA 토큰(회차별) |")
print("|---|---|---|---|---|---|---|")
tot_ok = tot_n = 0
for (v, n), rs in sorted(by.items()):
    tag = f"{n}{'v' if v else ''}"
    oks = sum(r["success"] for r in rs); tot_ok += oks; tot_n += len(rs)
    toks = [r["tokens"] + r["browser_tokens"] for r in rs]; secs = [r["elapsed_sec"] for r in rs]
    ch = "/".join("B" if r["browser_calls"] else "T" for r in rs)
    steps = "/".join(str(r["steps"]) for r in rs)
    bua = "/".join(f"{r['browser_tokens']:,}" if r["browser_calls"] else "-" for r in rs)
    print(f"| {tag} | {oks}/{len(rs)} | {ch} | {steps} | {st.mean(toks):,.0f} ± {sd(toks):,.0f} | {st.mean(secs):.0f} ± {sd(secs):.0f} | {bua} |")
print(f"\n전체 성공 {tot_ok}/{tot_n} = {tot_ok/tot_n:.0%}")
for run in sorted({r["run"] for r in rows}):
    rr = [r for r in rows if r["run"] == run]
    print(f"run {run}: 성공 {sum(r['success'] for r in rr)}/{len(rr)}, 총 토큰 {sum(r['tokens']+r['browser_tokens'] for r in rr):,}, 총 {sum(r['elapsed_sec'] for r in rr):.0f}초")
unstable = [f"{n}{'v' if v else ''}" for (v, n), rs in sorted(by.items()) if 0 < sum(r["success"] for r in rs) < len(rs)]
print("회차마다 결과가 갈린 태스크:", unstable or "없음")
