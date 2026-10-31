# 01-mcp-server NOTES

## 설계 메모 (F1·F2) — 수요일 제출용

### F1. 도구

- `list_courses(keyword="")` — 과목 목록. keyword 있으면 과목명 부분일치 필터.
- `get_course(name)` — 과목명 부분일치로 상세 1건. 여러 건이면 첫 건 + `candidates`.
- 캐시 인자 없음. `clear_cache` 없음. 캐시는 서버 내부 TTL(300초)로만.

### F2. 응답

성공:

```json
{"ok": true, "data": [...], "count": 20, "total": 37, "truncated": true}
```

- 결과 0건은 `ok: true, data: [], count: 0`. 실패가 아니다.
- 20건 넘으면 잘라서 보내고 `truncated: true`, `total`에 전체 건수.

실패 (4종, 최종본):

```json
{"ok": false, "error": "SESSION_EXPIRED",       "message": "세션 만료, 사용자에게 쿠키 갱신을 요청하세요."}
{"ok": false, "error": "COURSE_NOT_FOUND",      "message": "'알고리즘'과 일치하는 과목이 없습니다. list_courses로 정확한 과목명을 확인하세요."}
{"ok": false, "error": "SERVER_NOT_RESPONDING", "message": "서버 응답없음, 잠시 후 재시도하세요."}
{"ok": false, "error": "PARSING_FAILED",        "message": "파싱 실패, 사이트 구조가 변경된 것 같습니다. 사용자에게 알리세요."}
```

- 코드명은 전부 대문자 스네이크. 모델은 문자열 그대로 비교하니 일관성이 곧 정확도.
- message는 상황 + 모델이 다음에 할 일 한 줄.
- 재시도가 통하는 에러(SERVER_NOT_RESPONDING)와 안 통하는 에러(나머지 셋)를 message로 구분한다.
  PARSING_FAILED에 "재시도하세요"를 붙이면 모델이 무한 재시도한다.

---

## 제출 NOTES 항목 (작성 예정)

### 1. 과제 00의 TOOLS 딕셔너리와 schema.json 비교 — 같은 것, 다른 것

(TODO)

### 2. "0건"과 "실패"를 어떻게 구분했고 왜 그 형식인가

(TODO — 위 F2 설계 참고해서 본인 말로)

### 3. stdio와 HTTP 중 어느 걸 언제 쓰나 — 두 줄

(TODO)

### 4. 원본 서버에서 바꾼 것 3가지와 이유

(TODO)
