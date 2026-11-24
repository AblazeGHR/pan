"""Independent ACP Python SDK client, used only with an isolated server."""
import asyncio
import os
import sys

from acp import spawn_stdio_connection


async def main():
    python, root, cwd = sys.argv[1:]
    updates = []
    async def handler(method, params, notification):
        if method == 'session/update':
            updates.append(params['update']['content']['text'])
        return None
    async with spawn_stdio_connection(handler, python, '-m', 'packages.core.cli', '--root', root,
                                      'acp', '--profile', 'editor', cwd=cwd,
                                      env={'PYTHONPATH':os.environ['PYTHONPATH']}) as (conn, _):
        init = await conn.send_request('initialize', {'protocolVersion':1, 'clientCapabilities':{}})
        assert init['protocolVersion'] == 1
        new = await conn.send_request('session/new', {'cwd':cwd, 'mcpServers':[]})
        for text in ('sdk-one', 'sdk-two'):
            result = await conn.send_request('session/prompt', {'sessionId':new['sessionId'],
                                             'prompt':[{'type':'text','text':text}]})
            assert result['stopReason'] == 'end_turn'
        assert ''.join(updates) == 'ack:sdk-oneack:sdk-two'
    print('ACP Python SDK two-turn fixture passed')


asyncio.run(main())
