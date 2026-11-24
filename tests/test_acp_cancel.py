"""Scoped interrupt is only written for an exact live native turn."""
import asyncio
import json
from types import SimpleNamespace
from packages.core import worker
from packages.core.adapters.codex.app_server_wrapper import AppServer
from queue import Queue


def test_scoped_cancel_execution_and_native_identity(monkeypatch):
    async def run():
        writes = []
        class Pipe:
            def write(self, data): writes.append(json.loads(data))
            async def drain(self): pass
        adapter = SimpleNamespace(name='codex', encode_control_message=lambda value: json.dumps(value).encode())
        w = SimpleNamespace(worker_id='w', session_id='s', generation=0, adapter=adapter, status='running',
            _execution_seq=1, _terminal_handled=False, _current_handoff_acked=True,
            _acp_native_turn=(1, 'native-1'), native_control_mode='pan',
            process=SimpleNamespace(returncode=None, stdin=Pipe()))
        monkeypatch.setitem(worker.workers, 'w', w)
        monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: w if sid == 's' else None)
        async def get_lock(_): return asyncio.Lock()
        monkeypatch.setattr(worker, '_session_spawn_lock', get_lock)
        assert (await worker.interrupt_expected_execution('s', 'w', 0, 2))['status'] == 'rejected'
        w._acp_native_turn = (2, 'native-2')
        assert (await worker.interrupt_expected_execution('s', 'w', 0, 1))['reason'] == 'native_turn_unavailable'
        w._acp_native_turn = (1, 'native-1')
        w._terminal_handled = True
        assert (await worker.interrupt_expected_execution('s', 'w', 0, 1))['status'] == 'rejected'
        w._terminal_handled = False
        assert (await worker.interrupt_expected_execution('s', 'w', 0, 1))['status'] == 'submitted'
        assert writes == [{'type':'interrupt','expectedTurnId':'native-1'}]
    asyncio.run(run())


def test_scoped_approval_preserves_successor_and_legacy_contract(monkeypatch):
    app = AppServer('node', 'codex.js', 'C:/work', [])
    sent = []
    monkeypatch.setattr(app, '_send', sent.append)
    state = {'turn_id': 'successor', 'done': False,
             'pending_requests': {'1': {'id': 1, 'deadline': float('inf')}}}
    queue = Queue()
    for expected in ('previous', '', None):
        queue.put({'type': 'approval_response', 'request_id': 1,
                   'decision': 'accept', 'expectedTurnId': expected})
    app._drain_controls(state, queue)
    assert not sent and '1' in state['pending_requests']
    state['done'] = True
    queue.put({'type': 'approval_response', 'request_id': 1,
               'decision': 'accept', 'expectedTurnId': 'successor'})
    app._drain_controls(state, queue)
    assert not sent and '1' in state['pending_requests']
    state['done'] = False
    queue.put({'type': 'approval_response', 'request_id': 1,
               'decision': 'accept', 'expectedTurnId': 'successor'})
    app._drain_controls(state, queue)
    assert sent == [{'id': 1, 'result': {'decision': 'accept'}}]
    state['pending_requests']['2'] = {'id': 2, 'deadline': float('inf')}
    queue.put({'type': 'approval_response', 'request_id': 2, 'decision': 'decline'})
    app._drain_controls(state, queue)
    assert sent[-1] == {'id': 2, 'result': {'decision': 'decline'}}


def test_approval_requires_exact_execution_and_pending_request(monkeypatch):
    async def run():
        writes = []
        class Pipe:
            def write(self, data): writes.append(json.loads(data))
            async def drain(self): pass
        adapter = SimpleNamespace(name='codex', encode_control_message=lambda value: json.dumps(value).encode())
        w = SimpleNamespace(worker_id='w', session_id='s', generation=0, adapter=adapter, status='running',
            _execution_seq=1, _terminal_handled=False, _current_handoff_acked=True,
            _acp_native_turn=(1, 'native-1'), native_control_mode='pan',
            pending_interactions={'request:5': {'type':'approval.request', 'request_id':5,
                                   'method':'item/commandExecution/requestApproval'}},
            process=SimpleNamespace(returncode=None, stdin=Pipe()))
        monkeypatch.setitem(worker.workers, 'w', w)
        monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: w if sid == 's' else None)
        async def get_lock(_): return asyncio.Lock()
        monkeypatch.setattr(worker, '_session_spawn_lock', get_lock)
        call = worker.approval_expected_execution
        assert (await call('s', 'w', 0, 2, 5, 'accept'))['status'] == 'rejected'
        assert (await call('s', 'w', 0, 1, '5', 'accept'))['status'] == 'rejected'
        assert (await call('s', 'w', 0, 1, 5, 'acceptForSession'))['status'] == 'rejected'
        w._terminal_handled = True
        assert (await call('s', 'w', 0, 1, 5, 'accept'))['status'] == 'rejected'
        w._terminal_handled = False
        assert (await call('s', 'w', 0, 1, 5, 'decline'))['status'] == 'submitted'
        assert writes == [{'type':'approval_response', 'request_id':5, 'decision':'decline',
                           'expectedTurnId':'native-1'}]
    asyncio.run(run())
