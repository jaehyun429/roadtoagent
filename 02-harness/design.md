1. 구조도
TOOLS (모델에게 보여줄 스키마 4개):
  - list_courses, get_assignments, get_notices  ← MCP list_tools()로 받아온 것
  - browser_fallback                            ← 손으로 쓴 것

TOOL_MAP (이름 → 실행):
  - 위 3개 → mcp_client.call_tool(name, args)
  - browser_fallback → BUA 실행 함수


2. browser_fallback description
공지사항, 과제, 이번학기 수강 과목 확인과 같은 단순 작업은 이 도구 말고 다른 전용 도구를 사용하세요. 
다른 도구에서 COURSE_NOT_FOUND와 같은 에러가 발생한 경우엔, 과목이 없어서 발생한 문제이므로 이 도구를 추가 실행하지 마세요.
출석, 계획서, 강의자료 확인과 같은 다른 도구로 정보를 얻을 수 없는 일의 경우에만 사용을 하세요.
한 번 호출에 한 과목, 한 페이지만. 여러 과목이면 과목마다 따로 부르세요
앞서 말한 다른 도구로 먼저 해결해보고 안되는 경우에만 사용을 하세요. 느리고 토큰이 많이 드는 작업입니다.

3. SESSION_EXPIRED 
몇번째 스텝이었는지, 어떤 Task(최종 목표)를 향해서 가고 있는지, 이전까지 사고했던 과정(messages 전체), 토큰 누적량
재개시에는, 모델에게 쿠키가 갱신되었으니 실패한 도구를 다시 부르라는 메세지를 보냄.

4. 측정 항목
성공여부는 tasks.md 기대 결과와 문자열을 비교하거나, 파일을 다운로드 받아야하는 경우에는 task에 적힌 파일이 실제로 다운받아졌는지 비교, 경로는 browser_fallback이 불렸다면, bua가 작동한것이고, 단순 도구 호출이라면 get_notices()와 같은 도구 호출이 적혀있을 것임. 스텝수는 API호출 1회당 +1, 입력+출력 토큰은 매 스텝마다 더해지는 식으로, 소요시간 또한 처음에 사용자가 명령을 입력하고 명령을 이해한 뒤에 목표를 세우는 때부터 시작해서 최종 결과물이 받아지고 성공/실패를 할때까지를 소요시간으로 본다.

5. results.jsonl 
{"task_num": 2, "variation": false, "date": "2026-11-13", "model": "claude-sonnet-5-5", "success": true, "channel": "tool",
 "steps": 2, "tokens": 2748, "elapsed_sec": 4.9,
 "browser_calls": 0, "browser_steps": 0, "browser_tokens": 0, "browser_sec": 0.0,
 "judge_reason": "최종 답의 제목 '출석, 과제 점수 공지'가 기대 결과와 정확히 일치한다. ...", "judge_tokens": 756}


