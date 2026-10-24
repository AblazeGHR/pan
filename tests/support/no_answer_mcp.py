"""One real stdio call against the explicitly owned isolated HTTP host."""
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    base, owner, arguments = sys.argv[1:]
    url = urlsplit(base)
    assert url.hostname == '127.0.0.1' and url.port not in {8767, 8768}
    params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, 'PAN_API_URL': base, 'PAN_AGENT_SESSION_ID': owner})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            tool = next(t for t in tools if t.name == 'interaction_card')
            assert 'no_answer_needed' in tool.description and 'list_pending' in tool.description
            result = await client.call_tool('interaction_card', json.loads(arguments))
            assert not result.isError
            print(result.content[0].text)


if __name__ == '__main__':
    asyncio.run(main())
