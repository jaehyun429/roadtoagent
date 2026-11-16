"""judge-check.md 요약표의 '모델 판정'과 '내 판정'을 비교해 일치 칸과 일치율을 채운다.
사용: python3 agree.py  (judge-check.md 를 제자리에서 고친다)"""
import re
from pathlib import Path

p = Path(__file__).with_name("judge-check.md")
lines = p.read_text(encoding="utf-8").splitlines()
total = same = 0
for i, l in enumerate(lines):
    m = re.match(r"^\| (\d+v?) \| (.*?) \| ([✓✗]) \| ([✓✗ ]*) \| ([○✗ ]*) \|$", l)
    if not m:
        continue
    tag, task, model, mine = m.group(1), m.group(2), m.group(3), m.group(4).strip()
    if not mine:
        print(f"{tag}: 내 판정 비어 있음")
        continue
    total += 1
    agree = "○" if mine == model else "✗"
    same += mine == model
    lines[i] = f"| {tag} | {task} | {model} | {mine} | {agree} |"
if total:
    rate = f"{same}/{total} = {same/total:.0%}"
    lines = [re.sub(r"^- 일치율:.*", f"- 일치율: {rate}", l) for l in lines]
    print(f"일치율 {rate}")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
