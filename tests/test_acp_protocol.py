"""ACP framing/state contract against a synthetic backend, not a Pan service."""
import asyncio
import io
import json
import os

from packages.acp.protocol import Protocol, serve


class Backend:
    def __init__(self):
        self.prepared = []
        self.turns = []
        self.cancelled = []
        self.hold = asyncio.Event()
        self.hold.set()

    async def prepare(self, cwd):
        self.prepared.append(cwd)
        return {'cwd': cwd}

    async def prompt(self, context, blocks, turn_id, emit):
        self.turns.append(turn_id)
        self.last_emit = emit
        await emit('synthetic')
        await self.hold.wait()
        return 'cancelled' if turn_id in self.cancelled else 'end_turn'

    async def cancel(self, context, turn_id):
        self.cancelled.append(turn_id)
        self.hold.set()

    async def disconnect(self): pass


def test_v1_lifecycle_busy_cancel_and_session_isolation(tmp_path):
    async def run():
        backend, output = Backend(), []
        async def send(msg): output.append(msg)
        engine = Protocol(backend, send)
        async def request(id, method, params):
            await engine.handle({'jsonrpc':'2.0', 'id':id, 'method':method, 'params':params})
        await request(1, 'session/new', {})
        assert output[-1]['error']['code'] == -32000
        await request(2, 'initialize', {'protocolVersion':1, 'clientCapabilities':{}})
        assert output[-1]['result']['protocolVersion'] == 1
        await request(3, 'session/new', {'cwd':os.fspath(tmp_path), 'mcpServers':[]})
        sid = output[-1]['result']['sessionId']
        assert len(backend.prepared) == 1 and not backend.turns
        await request(4, 'session/prompt', {'sessionId':sid, 'prompt':[{'type':'text','text':'first'}]})
        await engine.sessions[sid].task
        assert output[-2]['method'] == 'session/update' and output[-2]['params']['sessionId'] == sid
        assert output[-1]['result']['stopReason'] == 'end_turn'
        size = len(output)
        await backend.last_emit('late')
        assert len(output) == size
        other = Protocol(Backend(), send)
        await other.handle({'jsonrpc':'2.0','id':8,'method':'initialize','params':{'protocolVersion':1}})
        await other.handle({'jsonrpc':'2.0','id':9,'method':'session/prompt',
                            'params':{'sessionId':sid,'prompt':[{'type':'text','text':'foreign'}]}})
        assert output[-1]['error']['code'] == -32602
        backend.hold.clear()
        await request(5, 'session/prompt', {'sessionId':sid, 'prompt':[{'type':'text','text':'second'}]})
        await asyncio.sleep(0)
        await request(6, 'session/prompt', {'sessionId':sid, 'prompt':[{'type':'text','text':'duplicate'}]})
        assert output[-1]['error']['message'] == 'Session is busy'
        await engine.handle({'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':'foreign'}})
        assert not backend.cancelled
        await engine.handle({'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':sid}})
        await engine.sessions[sid].task
        assert output[-1]['result']['stopReason'] == 'cancelled' and len(backend.turns) == 2
        await engine.close()
    asyncio.run(run())


def test_stdio_is_only_jsonrpc_and_handles_invalid_lines():
    async def run():
        source = io.BytesIO(b'{invalid}\n' + json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize',
            'params':{'protocolVersion':1,'clientCapabilities':{}}}).encode() + b'\n')
        target = io.BytesIO()
        await serve(Backend(), source, target)
        rows = [json.loads(line) for line in target.getvalue().splitlines()]
        assert rows[0]['error']['code'] == -32700
        assert rows[1]['result']['protocolVersion'] == 1
    asyncio.run(run())
