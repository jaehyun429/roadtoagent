# 01-mcp-server NOTES

## 설계 메모 (F1·F2) 

### F1. 도구

- `list_courses()` — 이번 학기 수강 과목. 반환: name, professor, course_id. 학기(term_year/term_cd)는 서버가 계산해 넣는다. 모델이 몰라도 되는 값.
- `get_assignments(course, only_pending=False)` — 과목명 부분일치, 과제 목록. 반환: task_name, deadline, submitted, is_overdue. 본문(report_content)은 안 넘긴다.
- `get_notices(course, limit=10)` — 과목명 부분일치, 최근 공지 limit건 (최대 20). 반환: title, date, course_name, has_attachment.
- 과목명이 안 맞으면 COURSE_NOT_FOUND → list_courses로 유도.
- 캐시 인자 없음. `clear_cache` 없음. 캐시는 서버 내부 TTL(300초)로만. 과목명→course_id 변환은 "courses" 캐시가 담당.

결정 사항
- only_pending = 미제출(apply_yn=N) **이면서 마감 안 지남**. 마감 지난 미제출은 "못 한 것"이라 제외. 안 그러면 "과제 뭐 남았어?"에 지난 것까지 나온다.
- 공지 목록 API에는 본문이 없다 (제목·날짜·작성자만). 본문은 별도 도구(get_notice_detail) 범위라 이번엔 뺀다. 과제 본문도 같은 이유로 제외.
- 응답에 교수 전화번호(writeruser_phone), 암호화 토큰(pdf_attach_file_encdata)이 섞여 온다. 파서가 설계 필드만 고르므로 파서가 곧 필터.
- is_overdue는 응답의 서버 날짜(current_date) 기준. 로컬 시계보다 정확.
- 요청 본문은 사이트 프론트엔드가 AES-256-CBC(IV=0, 키는 공개 JS의 getSecKey 상수)로 감싸 `e=<base64>` 폼 필드로 보낸다. 서버도 같은 방식으로 감싼다. 키는 레포에 안 올리고 .env(CAMPUS_AES_KEY). Network 탭에서 잡은 암호문 3개를 코드가 바이트 단위로 재현하는지로 검증했다.
-> 프론트엔드가 보낸 것처럼 암호화해서 보내려면 키가 필요한데 getSecKey라는 함수로 받을 수 있는 키가 JS에 노출돼 있어 누구나 볼 수 있는 대칭 키이다.
- 쿠키 없이 API를 치면 HTTP 302 → 로그인 페이지. 그래서 follow_redirects=False로 두고 3xx를 SESSION_EXPIRED로 본다. header.code != 200은 구조 문제로 보고 PARSING_FAILED.
- 과제·공지 API에 offset/limit이 있어 공지는 서버가 limit만큼만 요청한다 (받아서 자르지 않음). 과제는 limit=20으로 요청하고 tot_cnt가 total.
- 과목 캐시는 내부용으로 class_no 등 payload에 필요한 필드를 들고 있고, 모델에게는 public_course()로 name/professor/course_id 셋만 넘긴다.
- 학기는 서버가 오늘 날짜로 계산하되 환경변수(CAMPUS_TERM_YEAR/CAMPUS_TERM_CD)로 고정 가능. 2학기 수강 과목이 없어 개발 중엔 2026/10(1학기)으로 고정. 어느 쪽이든 모델 인자에는 없다.

### F2. 응답

성공:

```json
{"ok": true, "total": 37, "returned": 20, "truncated": true, "items": [...]}
```

- 결과 0건은 `ok: true, total: 0, items: []`. 실패가 아니다.
- 20건 넘으면 잘라서 보내고 `truncated: true`. `total`은 서버가 아는 전체, `returned`는 실제 넘긴 수.

실패 (5종, 최종본):

```json
{"ok": false, "error": "SESSION_EXPIRED",       "message": "세션 만료, 사용자에게 쿠키 갱신을 요청하세요."}
{"ok": false, "error": "COURSE_NOT_FOUND",      "message": "'알고리즘'과 일치하는 과목이 없습니다. list_courses로 정확한 과목명을 확인하세요."}
{"ok": false, "error": "COURSE_AMBIGUOUS",      "message": "후보: [알고리즘, 고급 알고리즘]. 하나를 지정하거나 둘 다 조회하세요"}
{"ok": false, "error": "SERVER_NOT_RESPONDING", "message": "서버 응답없음, 잠시 후 재시도하세요."}
{"ok": false, "error": "PARSING_FAILED",        "message": "파싱 실패, 사이트 구조가 변경된 것 같습니다. 사용자에게 알리세요."}
```

- 코드명은 전부 대문자 스네이크. 모델은 문자열 그대로 비교하니 일관성이 곧 정확도.
- message는 상황 + 모델이 다음에 할 일 한 줄.
- 재시도가 통하는 에러(SERVER_NOT_RESPONDING)와 안 통하는 에러(나머지 셋)를 message로 구분한다.
  PARSING_FAILED에 "재시도하세요"를 붙이면 모델이 무한 재시도한다.

---

## 제출 NOTES 항목

### 1. 과제 00의 TOOLS 딕셔너리와 schema.json 비교 — 같은 것, 다른 것


같은 것: 손으로 쓴거랑 데코레이터가 만든 것이 같은 포맷임.
다른 것: 
1. 과제 0에선 내가 타이핑 했지만, 여기서는 FastMCP가 타입 힌트랑 DocString만 보고 자동생성함.
2. 과제 0에서는 agent.py에서 Gemini API 호출에 직접 붙었었는데, 이번 과제는 MCP 프로토콜의 tool/list 응답으로 어떤 클라이언트든 받아감. 이게 MCP존재의 이유(표준 프로토콜)

### 2. "0건"과 "실패"를 어떻게 구분했고 왜 그 형식인가


0건 자체는 실패한건 아니고 잘 확인헀으나 없는 것이므로 ok: true라고 놓는다.( 모델이 첫줄만 보고 갈리니까)
탐색이 세션이 만료가 되었거나 이름이 비슷한 것들이 있는 경우 등 이런 예외 상황에 대해서는 에러문을 띄우게 하였다.(코드가 고정 문자열이라 error == "SESSION_EXPIRED"처럼 비교,분기할 수 있다)

### 3. stdio와 HTTP 중 어느 걸 언제 쓰나 — 두 줄

stdio는 (여기선 claude desktop) 모델을 띄워서 서버 1 클라이언트 1끼리 연결될때 사용하고.(1:1연결)
HTTP는 서버를 하나 띄워서 거기에 여러 클라이언트가 붙는 서버 1 클라이언트 다수 끼리의 연결이 가능하다.(1:다연결)

-> stdio는 클라이언트가 직접 프로세스를 띄우니 인증이 필요 없고, HTTP는 네트워크의 누구나 칠 수 있으니 인증이 필요하다.

### 4. 원본 서버에서 바꾼 것 3가지와 이유

1. 에러코드를 종류별로 나눔 -> 에러라고 다 같은 에러가 아니고 세션이 만료될수도 있고, AMBIGUOUS한 요청이 왔을 수도 있고 하니 분류를 세세하게 나누어서 에러 상황에 대해 유연하게 대처할 수 있게함.
2. 서버 내부 사정을 모델이 결정하게 떠넘긴 것 -> clear_cache같은 도구 삭제. 캐시를 쓸지 말지는 서버가 알아서 TTL로 할 일인데 모델에게 선택지를 넘겨줬었음 -> 삭제
3. 전송방식 -> 기존에는 stdio 방식으로만 했는데 지금은 stdio와 http 두가지를 놓고 사용(저번에는 인자가 없으니 stdio로만 뜨고, 클로드 데스크탑이 자식프로세스로 띄우는 용도로만 사용가능했음)

