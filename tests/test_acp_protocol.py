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
        assert output[-1]['error']['code'] == -32600
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
        assert output[-1]['error'] == {'code':-32002, 'message':'Session not found', 'data':{'sessionId':sid}}
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


def test_prompt_immediate_cancel_when_transport_reorders(tmp_path):
    from packages.acp.backend import Backend as LocalBackend
    from packages.acp.policy import validate
    from packages.acp.service import Service
    async def run():
        profile = validate('p', {'enabled':True, 'template':'text', 'workspaceRoots':[str(tmp_path)]})
        effects, output = [], []
        gate = asyncio.Event()
        async def create(*_): effects.append('create'); return 's'
        async def enqueue(*_): effects.append('enqueue'); return 'q'
        service = Service(lambda _:profile, create, enqueue)
        async def call(operation, args):
            if operation == 'prepare': return {'handle':service.prepare('p', args['cwd'])}
            if operation == 'cancel': return await service.cancel('p', args['handle'], args['turnId'])
            if operation == 'submit':
                await gate.wait()
                await service.submit('p', args['handle'], args['turnId'], args['blocks'])
                return {}
            if operation == 'poll': return service.poll('p', args['handle'], args['turnId'], args['cursor'])
            if operation == 'finish': service.finish('p', args['handle'], args['turnId'], args['cursor']); return {}
        async def send(item): output.append(item)
        protocol = Protocol(LocalBackend(call), send)
        await protocol.handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':1}})
        await protocol.handle({'jsonrpc':'2.0','id':2,'method':'session/new',
                               'params':{'cwd':str(tmp_path),'mcpServers':[]}})
        sid = output[-1]['result']['sessionId']
        await protocol.handle({'jsonrpc':'2.0','id':3,'method':'session/prompt',
                               'params':{'sessionId':sid,'prompt':[{'type':'text','text':'hello'}]}})
        await protocol.handle({'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':sid}})
        gate.set()
        await protocol.sessions[sid].task
        assert output[-1]['result']['stopReason'] == 'cancelled'
        assert effects == []
    asyncio.run(run())


def test_jsonrpc_cancel_is_bound_to_original_request_and_terminal(tmp_path):
    async def run():
        backend, out = Backend(), []
        async def send(msg): out.append(msg)
        p = Protocol(backend, send)
        await p.handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':1}})
        await p.handle({'jsonrpc':'2.0','id':2,'method':'session/new','params':{'cwd':str(tmp_path),'mcpServers':[]}})
        sid = out[-1]['result']['sessionId']
        backend.hold.clear()
        await p.handle({'jsonrpc':'2.0','id':10,'method':'session/prompt',
                        'params':{'sessionId':sid,'prompt':[{'type':'text','text':'x'}]}})
        await asyncio.sleep(0)
        await p.handle({'jsonrpc':'2.0','method':'$/cancel_request','params':{'requestId':11}})
        assert not backend.cancelled
        await p.handle({'jsonrpc':'2.0','method':'$/cancel_request','params':{'requestId':10}})
        await p.sessions[sid].task
        assert [m for m in out if m.get('id') == 10] == [
            {'jsonrpc':'2.0','id':10,'result':{'stopReason':'cancelled'}}]
        await p.handle({'jsonrpc':'2.0','method':'$/cancel_request','params':{'requestId':10}})
        assert len(backend.cancelled) == 1
    asyncio.run(run())
