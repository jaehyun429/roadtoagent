import asyncio
from fastmcp import Client
from pathlib import Path

async def main():
    async with Client(Path("../01-mcp-server/server.py")) as client:
        print(await client.list_tools())
        result = await client.call_tool(
            "get_notices", {"course": "딥러닝"}
        )
        print(result)

asyncio.run(main())