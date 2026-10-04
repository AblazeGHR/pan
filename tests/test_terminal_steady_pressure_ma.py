"""Three-minute real output/slow observer isolation, not an hours-long SLA."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
from pathlib import Path
import socket
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='real Windows ConPTY')


@pytest.mark.timeout(300)
def test_three_minute_natural_output_isolates_nonconsuming_observer(tmp_path):
    sidecar = Path(__file__).resolve().parents[1] / 'packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless/package.json'
    if not sidecar.exists():
        pytest.skip('sidecar dependencies absent')

    async def scenario():
        import httpx
        import psutil
        import uvicorn
        import websockets
        from websockets.exceptions import ConnectionClosed
        from fastapi import FastAPI
        from packages.core.terminal.service import TerminalService
        from packages.web import terminal_api, terminal_ws

        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        assert port != 8768
        origin = f'http://127.0.0.1:{port}'
        service = TerminalService(tmp_path / 'terminals', log_stderr=False)
        runtime = terminal_api.build_runtime(env={}, platform=sys.platform, host='127.0.0.1', port=port,
                                             service_factory=lambda: service, shutdown_budget=20)
        @asynccontextmanager
        async def lifespan(app):
            await terminal_api.start_runtime(app, runtime=runtime)
            try:
                async with terminal_ws.websocket_lifespan(app, runtime=runtime):
                    yield
            finally:
                await terminal_api.stop_runtime(app, runtime=runtime)
        app = FastAPI(lifespan=lifespan)
        app.include_router(terminal_api.router)
        app.include_router(terminal_ws.router)
        host = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                             log_level='error', timeout_graceful_shutdown=5,
                                             ws_ping_interval=None))
        host_task = asyncio.create_task(host.serve())
        sampler = None
        sampling_done = asyncio.Event()
        samples = []
        tid = None
        started = time.monotonic()
        try:
            for _ in range(300):
                if host.started and runtime.state == 'ready':
                    break
                await asyncio.sleep(.1)
            assert host.started and runtime.state == 'ready'
            async with httpx.AsyncClient(base_url=origin, headers={'Origin': origin}, timeout=30, trust_env=False) as client:
                response = await client.post('/api/terminals', json={'cwd': str(tmp_path), 'rows': 24, 'cols': 120})
                assert response.status_code == 200, response.text
                tid = response.json()['result']['terminal_id']
                original = service.get(tid)
                done = tmp_path / 'producer-finished.json'
                producer = tmp_path / 'steady.py'
                producer.write_text("import json,sys,time\nfrom pathlib import Path\n"
                                    "chunk=('STEADY_ROW|'+('x'*150)+'\\n')*400\n"
                                    "start=time.monotonic();count=0\n"
                                    "while time.monotonic()-start<180:\n"
                                    " sys.stdout.write(chunk);sys.stdout.flush();count+=len(chunk);time.sleep(.1)\n"
                                    "print('STEADY_FINAL_MARKER');sys.stdout.flush()\n"
                                    "Path(sys.argv[1]).write_text(json.dumps({'bytes':count,'seconds':time.monotonic()-start}))\n",
                                    encoding='utf-8')
                url = f'ws://127.0.0.1:{port}/ws/terminal/{tid}'
                # Disable transport ping timers only in this fixture. An unread
                # client cannot process ping/pong; test server backpressure,
                # rather than that independent keepalive timeout.
                async with websockets.connect(url, origin=origin, max_queue=1, close_timeout=5,
                                              ping_interval=None) as slow, \
                           websockets.connect(url, origin=origin, close_timeout=5) as fast:
                    slow_hello = json.loads(await slow.recv())
                    fast_hello = json.loads(await fast.recv())
                    assert slow_hello['type'] == fast_hello['type'] == 'hello'
                    slow_conn = app.state.terminal_ws_manager._conns[slow_hello['connection_id']]
                    # No fake transport/queue size or injected sender delay. The
                    # slow client performs no more recv or ack until output ends.
                    async def command(op, **fields):
                        await fast.send(json.dumps({'v': 1, 'type': 'command', 'terminal_id': tid,
                                                    'op': op, **fields}))
                    await command('claim')
                    while True:
                        event = json.loads(await asyncio.wait_for(fast.recv(), 20))
                        if event['type'] == 'claim-result':
                            generation = event['generation']
                            break
                    async def sample():
                        while not sampling_done.is_set():
                            manager = app.state.terminal_ws_manager
                            conns = list(manager._conns.values())
                            occupancy = []
                            for conn in conns:
                                if conn is not None:
                                    out = conn.outbound
                                    assert out.total_bytes <= out.max_bytes
                                    assert out.queued_items + bool(out.inflight_bytes) <= out.max_items
                                    occupancy.append({'bytes': out.total_bytes, 'items': out.queued_items,
                                                      'slow': out.slow, 'paused': conn.paused})
                            heartbeat = service.describe()['heartbeats'][tid]
                            # Read only: this is the verified owned launcher PID,
                            # not a process-name scan and never a kill target.
                            rss = psutil.Process(original['pid']).memory_info().rss
                            samples.append({'seconds': round(time.monotonic()-started, 3), 'queues': occupancy,
                                            'heartbeat': heartbeat['beats'], 'launcher_rss': rss})
                            try:
                                await asyncio.wait_for(sampling_done.wait(), 1)
                            except asyncio.TimeoutError:
                                pass
                    sampler = asyncio.create_task(sample())
                    invocation = f'"{sys._base_executable}" "{producer}" "{done}"\r'
                    await command('input', generation=generation,
                                  data_b64=base64.b64encode(invocation.encode()).decode())
                    deadline = time.monotonic() + 215
                    seen, gaps, marker, tail = 0, 0, False, b''
                    while time.monotonic() < deadline and not marker:
                        event = json.loads(await asyncio.wait_for(fast.recv(), 15))
                        if event['type'] == 'output':
                            data = base64.b64decode(event['data_b64'])
                            seen += len(data)
                            combined = tail + data
                            marker = b'STEADY_FINAL_MARKER' in combined
                            tail = combined[-128:]
                            await command('ack', next_seq=event['next_seq'])
                        elif event['type'] == 'gap':
                            gaps += 1
                            await command('snapshot', timeout_ms=2000)
                        elif event['type'] == 'snapshot':
                            assert event['recovery'] != 'full' or event['fidelity'] == 'full'
                            if event.get('cursors_valid') is True and not event.get('applied_evicted'):
                                await command('resume', cursor=event['cursor'])
                                marker = b'STEADY_FINAL_MARKER' in base64.b64decode(event['data_b64'])
                            else:
                                await command('snapshot', timeout_ms=2000)
                        elif event['type'] == 'error':
                            assert event['code'] == 'busy', event
                    assert marker, (seen, gaps)
                    sampling_done.set()
                    await sampler
                    for _ in range(100):
                        if done.exists():
                            break
                        await asyncio.sleep(.05)
                    produced = json.loads(done.read_text())
                    assert produced['seconds'] >= 180 and produced['bytes'] > 50_000_000
                    assert seen > 50_000_000
                    assert len(samples) >= 150
                    assert samples[-1]['heartbeat'] > samples[0]['heartbeat'] + 100
                    current = service.get(tid)
                    assert current['pid'] == original['pid']
                    assert current['process_created_at_filetime'] == original['process_created_at_filetime']
                    raw = service.read(tid, 0, max_bytes=1)
                    assert raw['gap'] is not None
                    assert raw['total_bytes'] - raw['first_retained_seq'] <= 262144
                    # Drain the natural client receive backlog only now so the
                    # actual protocol close code becomes observable to this peer.
                    slow_code = None
                    slow_close_detail = None
                    slow_drained = 0
                    close_deadline = time.monotonic() + 15
                    while time.monotonic() < close_deadline:
                        try:
                            message = await asyncio.wait_for(slow.recv(), 5)
                            slow_drained += len(message)
                        except ConnectionClosed as exc:
                            slow_code = exc.rcvd.code if exc.rcvd else None
                            slow_close_detail = {'received': str(exc.rcvd), 'sent': str(exc.sent)}
                            break
                    print(json.dumps({'producer': produced, 'fast_bytes': seen, 'fast_gaps': gaps,
                                      'slow_close_code': slow_code, 'slow_backlog_drained': slow_drained,
                                      'slow_close_detail': slow_close_detail,
                                      'slow_server_paused': slow_conn.paused,
                                      'slow_server_flag': slow_conn.outbound.slow,
                                      'raw_retained_bytes': raw['total_bytes']-raw['first_retained_seq'],
                                      'sample_count': len(samples), 'samples': samples,
                                      'same_pid_filetime': True}))
                    assert slow_code == 1013, (slow_code, slow_drained, slow_close_detail,
                                               slow_conn.paused, slow_conn.outbound.slow)
                cleanup_deadline = time.monotonic() + 20
                while time.monotonic() < cleanup_deadline:
                    response = await client.post(f'/api/terminals/{tid}/close', json={})
                    if response.status_code == 200:
                        break
                    assert response.status_code == 409, response.text
                    await asyncio.sleep(.2)
                assert response.status_code == 200 and response.json()['result']['status'] == 'exited', response.text
                tid = None
        finally:
            sampling_done.set()
            print(json.dumps({'final_elapsed': round(time.monotonic()-started, 3),
                              'samples': samples}))
            try:
                if sampler is not None:
                    await sampler
            finally:
                host.should_exit = True
                try:
                    await asyncio.wait_for(host_task, 30)
                finally:
                    if tid is not None:
                        service.shutdown(budget=20)
    asyncio.run(scenario())
