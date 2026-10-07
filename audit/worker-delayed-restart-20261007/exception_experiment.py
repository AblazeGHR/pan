"""Incremental direct experiment; no suite runner, provider or user-data writes.

Loads the existing experiment's fixtures without invoking its 17 scenarios.
Terminal tests use the real persistence ticket/executor and real Task.cancel;
only the final filesystem operation is replaced with an in-memory barrier.
"""
import ast
import asyncio
import copy
import json
from pathlib import Path
import threading

fixture_path = Path(__file__).with_name('experiment.py')
tree = ast.parse(fixture_path.read_text(encoding='utf-8'))
tree.body.pop()  # omit asyncio.run(main()), retain fixtures only
f = {'__file__': str(fixture_path)}
exec(compile(tree, str(fixture_path), 'exec'), f)
m, sm = f['m'], f['sm']
originals = {name: getattr(m, name) for name in (
    '_requeue_queue_unit', '_record_legal_worker_state', '_consumer_stream',
    '_reserve_queue_unit', '_save_receipt', '_bcast', '_maybe_inject_memory')}
save_body = sm._save_body
rows = []

async def noop(*args, **kwargs):
    pass

async def memory(s, text):
    return text

async def restore():
    await f['clear']()
    for name, value in originals.items():
        setattr(m, name, value)
    sm._save_body = save_body
    m._sess.get = lambda sid: f['sessions'].get(sid)
    m._get_session_shallow = m._sess.get
    m._bcast = f['bcast']
    m._schedule_queue_retry = lambda sid: None
    m._schedule_usage_enrichment = lambda sid: None
    m._has_pending_usage_enrichment = lambda s: False
    m._maybe_inject_memory = memory
    from packages.core import qq_reports
    qq_reports.kick = lambda sid: None
    m._notifications.dispatch_completion_nonblocking = lambda *args: None

def make_case(name):
    s = f['setup']('exception-' + name)
    w = f['make_worker'](s.id)
    # Liveness is a fixture; no real CLI process is created.
    m.request_delayed_restart(s.id)
    return s, w

def assert_aborted(s, w, cancelled=False):
    state = m.delayed_restart_state(s.id)
    assert state['status'] == ('cancelled' if cancelled else 'failed'), state
    assert state['error'] and not w.pending_restart
    assert not w._terminal_finishing and not w._queue_unit_active
    m._maybe_restart_pending(w)
    assert w.status == 'error' and not f['lifecycle']
    assert not any(e.get('type') == 'worker.result' for e in f['events'])

async def prove_recovery(s, w):
    # Exercise the actual public immediate Restart wrapper and Session lock;
    # only provider lifecycle body is replaced (no claim of OS restart).
    previous = m._restart_worker_unlocked
    async def restart(wid):
        assert wid == w.worker_id
        w.status = 'idle'
        w._terminal_finishing = False
        w._queue_unit_active = False
        return None
    m._restart_worker_unlocked = restart
    try:
        assert await m.restart_or_start_worker(s.id) is w
        assert w.status == 'idle'
    finally:
        m._restart_worker_unlocked = previous

async def terminal_case(cancel, write_fails):
    name = 'terminal-' + ('cancel-' if cancel else '') + ('fail' if write_fails else 'commit')
    await restore()
    s, w = make_case(name)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    error = OSError('injected terminal save failure')
    committed = []
    def body(session, **kwargs):
        assert w._terminal_finishing
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise TimeoutError('experiment barrier did not release')
        if write_fails:
            raise error
        committed.append(copy.deepcopy(session.last_result))
    sm._save_body = body
    task = asyncio.create_task(m._persist_terminal_state(w, s, 'done', 'result'))
    await asyncio.wait_for(entered.wait(), 5)
    assert w._terminal_finishing and not f['lifecycle']
    if cancel:
        task.cancel()  # real cancellation while actual executor owns save ticket
        await f['settle']()
        assert not task.done()  # cancellation waits for the writer's verdict
    release.set()
    try:
        await task
        assert False, 'expected propagation'
    except asyncio.CancelledError:
        assert cancel
    except OSError as caught:
        assert not cancel and caught is error
    assert_aborted(s, w, cancel)
    assert w._terminal_handled == (not write_fails)
    assert bool(s.last_result) == (not write_fails)
    # Retry the actual terminal path, including durable dedup after cancelled
    # successful commit. Disk remains in memory, same persistence executor.
    sm._save_body = lambda session, **kwargs: committed.append(copy.deepcopy(session.last_result))
    terminal = await m._persist_terminal_state(w, s, 'done', 'result')
    if write_fails:
        assert terminal and len(committed) == 1
        m._signal_task_done(w)
    else:
        assert terminal is None and len(committed) == 1
    assert not w._terminal_finishing and w._terminal_handled
    assert len(s.terminal_results) == 1
    await prove_recovery(s, w)
    rows.append({'case': name, 'intent': 'cancelled' if cancel else 'failed',
                 'exception_propagated': True, 'terminal_retry_or_dedup': True,
                 'immediate_restart_wrapper_recoverable': True})

async def queue_case(boundary, cancel=False):
    await restore()
    s, w = make_case('queue-' + boundary + ('-cancel' if cancel else ''))
    item = f['row'](1)
    s.queue_pending = [item]
    error = OSError('injected ' + boundary)
    entered = asyncio.Event()
    release = asyncio.Event()
    async def fail(*args, **kwargs):
        if cancel:
            entered.set()
            await release.wait()
        raise error
    async def reserve(*args, **kwargs):
        item['deliveryState'] = 'reserved' if boundary in {'requeue', 'save'} else 'queued'
        return True
    m._reserve_queue_unit = reserve
    m._consumer_stream = noop
    m._record_legal_worker_state = f['legal']
    if boundary == 'requeue':
        m._requeue_queue_unit = fail
    elif boundary == 'save':
        # Actual requeue + actual shielded receipt save + actual ticket writer.
        if cancel:
            writer_release = threading.Event()
            loop = asyncio.get_running_loop()
            def body(*args, **kwargs):
                loop.call_soon_threadsafe(entered.set)
                if not writer_release.wait(5):
                    raise TimeoutError('receipt writer barrier')
            sm._save_body = body
        else:
            sm._save_body = lambda *args, **kwargs: (_ for _ in ()).throw(error)
    elif boundary == 'legal':
        m._record_legal_worker_state = fail
    elif boundary == 'broadcast':
        async def bcast(event):
            if event['type'] == 'worker.status':
                await fail()
            else:
                await f['bcast'](event)
        m._bcast = bcast
    task = asyncio.create_task(m._deliver_queue_unit(w, s, [item]))
    if cancel:
        await asyncio.wait_for(entered.wait(), 3)
        assert w._queue_unit_active
        task.cancel()
        if boundary == 'save':
            await f['settle']()
            assert not task.done()
            writer_release.set()
    try:
        await task
        assert False, 'expected propagation'
    except asyncio.CancelledError:
        assert cancel
    except OSError as caught:
        assert not cancel and caught is error
    assert_aborted(s, w, cancel)
    assert len(s.queue_pending) == 1 and s.queue_pending[0] is item
    # Recover unfinished pre-handoff row using the actual requeue operation.
    sm._save_body = lambda *args, **kwargs: None
    await originals['_requeue_queue_unit'](w, s, [item], 'explicit recovery', immediate=True)
    assert item['deliveryState'] == 'queued'
    assert not item.get('retryAfter')
    await prove_recovery(s, w)
    rows.append({'case': 'queue-' + boundary + ('-cancel' if cancel else ''),
                 'intent': 'cancelled' if cancel else 'failed', 'exception_propagated': True,
                 'queue_row_retained_and_requeued': True, 'guards_cleared': True,
                 'immediate_restart_wrapper_recoverable': True})

async def recheck_existing_priority():
    # Reuse only the first three existing scenarios: running/idempotence,
    # terminal/accepted-unit guards, and actual FIFO + gated result/report.
    # The other fourteen existing scenarios are not rerun.
    tree = ast.parse(fixture_path.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'main')
    clears = 0
    for i, statement in enumerate(function.body):
        if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Await)
                and isinstance(statement.value.value, ast.Call)
                and isinstance(statement.value.value.func, ast.Name)
                and statement.value.value.func.id == 'clear'):
            clears += 1
            if clears == 2:
                function.body = function.body[:i+1] + [ast.Return(value=ast.Name(id='outcomes', ctx=ast.Load()))]
                break
    else:
        raise AssertionError('existing fixture layout changed')
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(fixture_path), 'exec'), f)
    return await f['main']()

async def main():
    for cancel, failure in [(False, True), (True, True), (True, False)]:
        await terminal_case(cancel, failure)
    for boundary in ['requeue', 'save', 'legal', 'broadcast']:
        await queue_case(boundary)
        await queue_case(boundary, True)
    await restore()
    priority = await recheck_existing_priority()
    assert len(priority) == 3
    result = {'passed': len(rows), 'scenarios': rows,
              'existing_priority_scenarios_rechecked': priority,
              'limitations': ['final filesystem writer injected; no OS disk fault',
                              'provider lifecycle body injected; no native restart',
                              'no HTTP/WS/browser/service or test suite execution']}
    Path(__file__).with_name('exception-results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))

asyncio.run(main())
