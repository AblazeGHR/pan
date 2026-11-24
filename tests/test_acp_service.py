"""Service-state tests use synthetic operations, not an IPC security claim."""
import asyncio
from types import SimpleNamespace
import pytest

from packages.acp.policy import validate
from packages.acp.projection import Unconfirmed
from packages.acp.service import Service
from packages.acp.pan_service import receipt_status
from packages.acp.backend import Backend, RecoveredTerminal
from tests.test_acp_projection import delta, frame, receipt


def test_cancel_before_submit_is_bound_to_original_turn(tmp_path):
    async def run():
        profile = validate('p', {'enabled':True, 'template':'text', 'workspaceRoots':[str(tmp_path)]})
        effects = []
        async def create(*_): effects.append('create'); return 's1'
        async def enqueue(*_): effects.append('enqueue'); return 'q1'
        service = Service(lambda _:profile, create, enqueue)
        handle = service.prepare('p', str(tmp_path))
        await service.cancel('p', handle, 'turn-1')
        await service.submit('p', handle, 'turn-1', 'hello')
        assert service.poll('p', handle, 'turn-1')['status'] == 'cancelled'
        assert effects == []
    asyncio.run(run())


def test_abandoned_active_context_does_not_exhaust_capacity(tmp_path):
    async def run():
        profile = validate('p', {'enabled':True, 'template':'text', 'workspaceRoots':[str(tmp_path)]})
        async def create(*_): return 's' + str(len(service.by_session))
        async def enqueue(*_): return 'q'
        service = Service(lambda _:profile, create, enqueue)
        for n in range(140):
            handle = service.prepare('p', str(tmp_path))
            await service.submit('p', handle, str(n), 'hello')
            service.abandon('p', handle)
        assert len(service.contexts) == 0
    asyncio.run(run())


def test_cancel_after_claim_waits_for_delivery_identity_and_not_successor(tmp_path):
    async def run():
        profile = validate('p', {'enabled':True, 'template':'text', 'workspaceRoots':[str(tmp_path)]})
        calls = []
        async def create(*_): return 's1'
        async def enqueue(*_): return 'q1'
        async def withdraw(*_): calls.append('withdraw'); return 'not_queued'
        async def interrupt(*args): calls.append(args); return 'submitted'
        service = Service(lambda _:profile, create, enqueue, withdraw, interrupt)
        handle = service.prepare('p', str(tmp_path))
        await service.submit('p', handle, 'first', 'hello')
        await service.cancel('p', handle, 'first')
        assert calls == ['withdraw']
        service.observe(receipt())
        await asyncio.sleep(0)
        assert calls == ['withdraw', ('s1', 'w1', 0, 1)]
        service.observe({'type':'worker.result', 'sessionId':'s1', 'workerId':'w1', 'generation':0,
                         'executionSeq':1, 'terminalKey':'t', 'resultCursor':1,
                         'status':'done', 'eventEpoch':'epoch', 'eventSeq':1})
        assert service.poll('p', handle, 'first')['status'] == 'done'
        service.finish('p', handle, 'first', 0)
        await service.submit('p', handle, 'successor', 'next')
        await service.cancel('p', handle, 'first')
        assert calls == ['withdraw', ('s1', 'w1', 0, 1)]
        service.observe({**receipt(), 'executionSeq':2})
        service.observe({'type':'worker.result', 'sessionId':'s1', 'workerId':'w1', 'generation':0,
                         'executionSeq':2, 'terminalKey':'t2', 'resultCursor':2,
                         'status':'done', 'eventEpoch':'epoch', 'eventSeq':2})
        service.finish('p', handle, 'successor', 0)
        await service.cancel('p', handle, 'first')
        assert service.contexts[handle].turn is None
    asyncio.run(run())


def test_cancel_during_session_creation_prevents_queue_admission(tmp_path):
    async def run():
        profile = validate('p', {'enabled':True, 'template':'text', 'workspaceRoots':[str(tmp_path)]})
        gate, started, calls = asyncio.Event(), asyncio.Event(), []
        async def create(*_):
            started.set()
            await gate.wait()
            return 's1'
        async def enqueue(*_): calls.append('enqueue'); return 'q1'
        service = Service(lambda _:profile, create, enqueue)
        handle = service.prepare('p', str(tmp_path))
        task = asyncio.create_task(service.submit('p', handle, 'turn', 'hello'))
        await started.wait()
        await service.cancel('p', handle, 'turn')
        gate.set()
        await task
        assert calls == []
        assert service.poll('p', handle, 'turn')['status'] == 'cancelled'
    asyncio.run(run())


def test_cancel_during_admission_withdraws_original_item(tmp_path):
    async def run():
        profile = validate('p', {'enabled':True, 'template':'text', 'workspaceRoots':[str(tmp_path)]})
        gate, started, removed = asyncio.Event(), asyncio.Event(), []
        async def create(*_): return 's1'
        async def enqueue(*_):
            started.set()
            await gate.wait()
            return 'q1'
        async def withdraw(*args): removed.append(args); return 'deleted'
        async def interrupt(*_): raise AssertionError('queued item must not be interrupted')
        service = Service(lambda _:profile, create, enqueue, withdraw, interrupt)
        handle = service.prepare('p', str(tmp_path))
        task = asyncio.create_task(service.submit('p', handle, 'turn', 'hello'))
        await started.wait()
        await service.cancel('p', handle, 'turn')
        gate.set()
        await task
        assert removed == [('s1','q1')]
        assert service.poll('p', handle, 'turn')['status'] == 'cancelled'
    asyncio.run(run())


def test_cancel_waits_for_native_identity_only_after_definite_rejection(tmp_path):
    async def run():
        profile = validate('p', {'enabled':True, 'template':'text', 'workspaceRoots':[str(tmp_path)]})
        attempts = []
        async def create(*_): return 's1'
        async def enqueue(*_): return 'q1'
        async def withdraw(*_): return 'not_queued'
        async def interrupt(*identity):
            attempts.append(identity)
            return 'native_pending' if len(attempts) == 1 else 'submitted'
        service = Service(lambda _:profile, create, enqueue, withdraw, interrupt)
        handle = service.prepare('p', str(tmp_path))
        await service.submit('p', handle, 'turn', 'hello')
        service.observe(receipt())
        await service.cancel('p', handle, 'turn')
        await asyncio.sleep(.2)
        assert attempts == [('s1','w1',0,1), ('s1','w1',0,1)]
        assert service.poll('p', handle, 'turn')['status'] is None
    asyncio.run(run())


def test_unreachable_clients_are_reaped_without_replay(tmp_path):
    async def run():
        profile = validate('p', {'enabled':True, 'template':'text', 'workspaceRoots':[str(tmp_path)]})
        effects = []
        async def create(*_): return 's' + str(len(effects))
        async def enqueue(*_): effects.append(1); return 'q'
        service = Service(lambda _:profile, create, enqueue)
        for n in range(140):
            handle = service.prepare('p', str(tmp_path))
            await service.submit('p', handle, str(n), 'hello')
            service.contexts[handle].last_access -= 61
        assert len(effects) == 140
        assert len(service.contexts) == 1
    asyncio.run(run())


def test_lazy_authorized_two_turns_and_private_projection(tmp_path):
    asyncio.run(_two_turns(tmp_path))


async def _two_turns(tmp_path):
    profile = validate('editor', {'enabled': True, 'template': 'text', 'workspaceRoots': [str(tmp_path)]})
    active = {'editor': profile}
    calls = []
    async def create(p, cwd):
        calls.append(('create', p, cwd))
        return 's1'
    service = None
    async def enqueue(sid, turn_id, text):
        calls.append(('enqueue', sid, turn_id, text))
        # Output may precede the returned admission and delivery receipt.
        service.observe(frame(1, delta('a', 'a')))
        service.observe(frame(2, delta('secret', 'secret'), session='another'))
        service.observe(receipt())
        service.observe({'type':'worker.result', 'sessionId':'s1', 'workerId':'w1',
                         'generation':0, 'executionSeq':1, 'terminalKey':'t', 'resultCursor':1,
                         'status':'done', 'eventEpoch':'epoch', 'eventSeq':3, 'result':'private'})
        return 'q1'
    service = Service(active.get, create, enqueue)
    handle = service.prepare('editor', str(tmp_path))
    assert calls == []
    with pytest.raises(PermissionError):
        service.poll('other', handle, 'turn1')
    for turn_id in ('turn1', 'turn2'):
        await service.submit('editor', handle, turn_id, 'hello')
        expected = {'chunks':['a'], 'cursor':1, 'status':'done'}
        assert service.poll('editor', handle, turn_id) == expected
        assert service.poll('editor', handle, turn_id) == expected
        assert service.poll('editor', handle, turn_id, 1)['chunks'] == []
        service.finish('editor', handle, turn_id, 1)
    assert [call[0] for call in calls] == ['create', 'enqueue', 'enqueue']
    active.clear()
    with pytest.raises(PermissionError):
        await service.submit('editor', handle, 'turn3', 'hello')


def test_unknown_admission_is_not_replayed(tmp_path):
    asyncio.run(_unknown_admission(tmp_path))


async def _unknown_admission(tmp_path):
    profile = validate('p', {'enabled': True, 'template': 'text', 'workspaceRoots':[str(tmp_path)]})
    calls = []
    async def create(*_): return 's1'
    async def enqueue(*_):
        calls.append(1)
        raise TimeoutError('unconfirmed admission')
    service = Service(lambda _: profile, create, enqueue)
    handle = service.prepare('p', str(tmp_path))
    with pytest.raises(TimeoutError):
        await service.submit('p', handle, 'one', 'hello')
    with pytest.raises(Unconfirmed):
        await service.submit('p', handle, 'one', 'hello')
    with pytest.raises(Unconfirmed):
        service.poll('p', handle, 'one')
    assert calls == [1]


def test_cancel_queued_and_running_with_exact_identity(tmp_path):
    asyncio.run(_cancel(tmp_path))


async def _cancel(tmp_path):
    profile = validate('p', {'enabled': True, 'template': 'text', 'workspaceRoots':[str(tmp_path)]})
    outcomes = iter(['deleted', 'not_queued'])
    controls = []
    async def create(*_): return 's1'
    async def enqueue(*_): return 'q1'
    async def withdraw(*args):
        controls.append(('withdraw', args))
        return next(outcomes)
    async def interrupt(*args):
        controls.append(('interrupt', args))
        return 'submitted'
    service = Service(lambda _: profile, create, enqueue, withdraw, interrupt)
    handle = service.prepare('p', str(tmp_path))
    await service.submit('p', handle, 'queued', 'one')
    await service.cancel('p', handle, 'queued')
    assert service.poll('p', handle, 'queued')['status'] == 'cancelled'
    service.finish('p', handle, 'queued', 0)
    await service.submit('p', handle, 'running', 'two')
    service.observe(receipt())
    await service.cancel('p', handle, 'running')
    await service.cancel('p', handle, 'running')
    assert controls == [('withdraw', ('s1', 'q1')), ('withdraw', ('s1', 'q1')),
                        ('interrupt', ('s1', 'w1', 0, 1))]
    assert service.poll('p', handle, 'running')['status'] is None
    service.observe({'type':'worker.result', 'sessionId':'s1', 'workerId':'w1',
                     'generation':0, 'executionSeq':1, 'terminalKey':'cancelled-t',
                     'resultCursor':1, 'status':'cancelled', 'eventEpoch':'epoch', 'eventSeq':1})
    assert service.poll('p', handle, 'running')['status'] == 'cancelled'


def test_recovery_requires_matching_original_receipt_and_terminal():
    receipt = {'clientMessageId':'acp:turn', 'workerId':'w', 'generation':0, 'executionSeq':2}
    terminal = {'workerId':'w', 'generation':0, 'executionSeq':2, 'status':'done',
                'terminalKey':'t', 'resultCursor':7, 'result':'private'}
    session = SimpleNamespace(queue_delivery_ledger={'q':receipt}, terminal_results=[terminal])
    found = receipt_status(session, 'turn', 'q')
    assert found == {'state':'terminal', 'queueItemId':'q', 'workerId':'w', 'generation':0,
                     'executionSeq':2, 'status':'done', 'terminalKey':'t', 'resultCursor':7}
    assert receipt_status(session, 'other', 'q') == {'state':'unknown'}
    assert receipt_status(session, 'turn', 'other') == {'state':'unknown'}
    session.terminal_results = [{**terminal, 'executionSeq':3}]
    assert receipt_status(session, 'turn', 'q') == {'state':'unknown'}
    session.terminal_results = [terminal, terminal]
    assert receipt_status(session, 'turn', 'q') == {'state':'unknown'}


def test_backend_reports_recovered_terminal_without_claiming_stream():
    async def run():
        operations = []
        async def request(operation, args):
            operations.append(operation)
            if operation == 'poll':
                raise RuntimeError('service restarted')
            if operation == 'recover':
                return {'state':'terminal', 'status':'done', 'terminalKey':'t'}
            return {}
        async def emit(_):
            raise AssertionError('no recovered text should be emitted')
        with pytest.raises(RecoveredTerminal) as error:
            await Backend(request).prompt('handle', [{'text':'x'}], 'turn', emit)
        assert error.value.receipt['terminalKey'] == 't'
        assert operations == ['submit', 'poll', 'recover']
    asyncio.run(run())


def test_lost_finish_response_keeps_identity_for_reconciliation():
    async def run():
        calls = []
        async def request(operation, args):
            calls.append(operation)
            if operation == 'prepare': return {'handle':'h'}
            if operation == 'poll': return {'chunks':[], 'cursor':0, 'status':'done'}
            if operation == 'finish': raise OSError('response lost')
            return {}
        async def emit(_): pass
        backend = Backend(request)
        handle = await backend.prepare('cwd')
        with pytest.raises(RuntimeError):
            await backend.prompt(handle, [{'text':'x'}], 'turn', emit)
        await backend.disconnect()
        assert calls == ['prepare', 'submit', 'poll', 'finish', 'recover', 'abandon']
    asyncio.run(run())
