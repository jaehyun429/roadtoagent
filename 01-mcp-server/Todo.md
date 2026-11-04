F3. 전송 방식 2개

stdio 버전: 지금 Claude에 연결된 그거입니다. 이미 됐습니다. Claude Desktop에서 대화한 화면 캡처 1장만 logs/stdio-claude.png로.
HTTP(streamable) 버전: 같은 server.py를 mcp.run(transport="streamable-http") 같은 옵션으로 띄우고, curl이나 MCP Inspector로 list_courses를 호출한 로그를 logs/http-call.txt로. “stdio는 로컬 혼자, HTTP는 원격 여럿”이라는 차이를 실제로 띄워보는 게 목적입니다.

F4. schema.json

FastMCP가 자동 생성한 JSON Schema를 파일로 저장. 코드에 dump_schema()가 이미 있으니 실행해서 커밋. 과제 00에서 손으로 쓴 TOOLS와 나란히 비교하는 용도입니다.