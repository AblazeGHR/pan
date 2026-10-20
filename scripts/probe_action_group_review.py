"""Independent acceptance probes for known lifecycle blockers; no deployed service.

Run through run_tests_isolated.py. These observations are NOT passing contracts.
All application state is synthetic or stored in a TemporaryDirectory.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main():
    import httpx
    from packages.actions import api, store
    from packages.core import worker
    from packages.web import server
    from packages.plugins import lifecycle

    entered, proceed = asyncio.Event(), asyncio.Event()
    async def slow_spawn(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return SimpleNamespace(pid=0)

    owner = SimpleNamespace(id='synthetic_startup', adapter_config={}, workdir=None)
    adapter = SimpleNamespace(name='synthetic', build_spawn_args=lambda *args: ['synthetic-never-executed'])
    with patch.object(worker._sess, 'get', return_value=owner), patch.object(lifecycle, 'capture'), \
            patch.object(asyncio, 'create_subprocess_exec', slow_spawn), \
            patch.object(server, '_update_session_settings', AsyncMock(return_value={'ok': True})):
        spawning = asyncio.create_task(worker._spawn_process(owner.id, adapter))
        await entered.wait()
        started = time.monotonic()
        unrelated = asyncio.create_task(server.api_update_session('synthetic_other', {'name': 'unrelated'}))
        try:
            await asyncio.wait_for(asyncio.shield(unrelated), .2)
            blocked = False
        except TimeoutError:
            blocked = True
        finally:
            proceed.set()
            await spawning
            await unrelated
        gate_wait = time.monotonic() - started

    with tempfile.TemporaryDirectory(prefix='pan-action-review-') as directory:
        registry = store.Store(directory)
        action = {'id': 'probe', 'name': 'Probe', 'enabled': False,
                  'input_schema': {'type': 'object', 'additionalProperties': False},
                  'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/models'}}
        atomic = store.atomic
        def slow_disk(path, value):
            time.sleep(.2)  # Deterministic owned IO latency, no service or lock waits.
            atomic(path, value)
        async def heartbeat():
            await asyncio.sleep(.02)
            return time.monotonic()
        with patch.object(api, 'Store', return_value=registry), patch.object(store, 'atomic', slow_disk), \
                patch.object(server.sess, 'list_all', return_value=[]):
            pulse = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)
            started = time.monotonic()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://isolated') as client:
                response = await client.put('/api/actions/actions/probe', json={'item': action})
                response.raise_for_status()
            lag = (await pulse) - started
    result = {'observationsOnly': True, 'unrelatedSettingsBlockedByStartup': blocked,
              'settingsWaitSeconds': round(gate_wait, 3),
              'eventLoopHeartbeatDelaySecondsDuringActionSave': round(lag, 3),
              'protectedServicesUsed': False}
    output = Path('.validation/action-review/blockers.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
