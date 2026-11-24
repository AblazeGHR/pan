"""Scoped interrupt is only written for an exact live native turn."""
import asyncio
import json
from types import SimpleNamespace
from packages.core import worker


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
