"""One-off deterministic interleaving experiment. No test runner or native CLI.

Production consumer/reservation/handoff/publication/coordinator run unchanged;
provider spawn/kill, disk saves, notification dispatch and report transport are
in-memory boundaries. This is not live provider or durability acceptance.
"""
import asyncio
import copy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from packages.core import worker as m
from packages.core import session as sm

sessions = {}
events = []
saves = []
lifecycle = []
handoffs = asyncio.Queue()
completed = asyncio.Queue()
publish_gate = None
publish_entered = asyncio.Event()
spawn_error = None
kill_gate = None
kill_entered = asyncio.Event()
refuse_stop = False
reservation_gate = None
reservation_entered = asyncio.Event()

class Adapter:
    name = 'cbc'
    default_model = 'isolated'
    execution_modes = ['stream']
    def encode_user_message(self, text):
        return text.encode()

class Input:
    def __init__(self, wid):
        self.wid = wid
    def write(self, data):
        handoffs.put_nowait((self.wid, data.decode().strip()))
    async def drain(self):
        pass

async def bcast(event):
    events.append(copy.deepcopy(event))
    if event['type'] == 'worker.result' and publish_gate is not None:
        publish_entered.set()
        await publish_gate.wait()
    if event['type'] == 'worker.delayed_restart' and event['delayedRestart']['status'] in {'completed', 'failed', 'cancelled'}:
        completed.put_nowait(event['delayedRestart'])

async def save(s, **kwargs):
    saves.append(copy.deepcopy(s.queue_pending))
    if reservation_gate is not None and any(i.get('deliveryState') == 'reserved' for i in s.queue_pending):
        reservation_entered.set()
        await reservation_gate.wait()

async def legal(*args, **kwargs):
    pass

async def kill(wid, **kwargs):
    w = m.workers[wid]
    lifecycle.append(('kill', wid))
    if kill_gate is not None:
        kill_entered.set()
        await kill_gate.wait()
    await m._cancel_worker_task(w._consume_task)
    if w.process is not None and not refuse_stop:
        w.process.returncode = 0
    m.workers.pop(wid, None)
    m._unregister_worker(w)
    return None

async def create(sid):
    if spawn_error:
        return spawn_error
    alive = m.find_alive_worker_by_session(sid)
    if alive:
        return alive
    w = make_worker(sid, 'replacement-' + str(len(lifecycle)), 'idle', 2)
    lifecycle.append(('create', w.worker_id))
    w._consume_task = asyncio.create_task(m._consumer(w))
    m._recover_pending_signals(w, sessions[sid])
    return w

def make_worker(sid, wid='old', status='running', generation=1):
    w = m.Worker(worker_id=wid, session_id=sid, adapter=Adapter(), status=status,
                 process=SimpleNamespace(returncode=None, stdin=Input(wid)),
                 pending_signal=asyncio.Queue(), _task_done=asyncio.Event(), generation=generation)
    m.workers[wid] = w
    m._register_worker(w)
    return w

def row(i, kind='task', locked=False):
    return {'id': str(i), 'queueItemId': str(i), 'seq': i, 'type': kind,
            'text': 'task-' + str(i), 'source': 'user', 'deliveryState': 'queued',
            'dispatchState': 'queued', 'revision': 1, 'queueLockManual': locked}

def setup(sid='isolated'):
    s = sm.Session(id=sid, name=sid)
    sessions[sid] = s
    return s

async def settle():
    # Explicit event-loop scheduling barrier, never wall-clock polling.
    event = asyncio.Event()
    asyncio.get_running_loop().call_soon(event.set)
    await event.wait()

async def clear():
    for w in list(m.workers.values()):
        await m._cancel_worker_task(w._consume_task)
        await m._cancel_worker_task(w._stdout_task)
        await m._cancel_worker_task(w._watchdog_task)
    m.workers.clear()
    m._workers_by_session.clear()
    m._delayed_restarts.clear()
    sessions.clear()
    events.clear()
    saves.clear()
    lifecycle.clear()
    while not handoffs.empty():
        handoffs.get_nowait()
    while not completed.empty():
        completed.get_nowait()

async def main():
    global publish_gate, spawn_error, kill_gate, refuse_stop, reservation_gate
    original_create = m._create_worker
    m._sess.get = lambda sid: sessions.get(sid)
    m._get_session_shallow = lambda sid: sessions.get(sid)
    m._sess.save_async = save
    m._save_receipt = save
    m._record_legal_worker_state = legal
    m._bcast = bcast
    m._kill_worker_unlocked = kill
    m._create_worker = create
    m._schedule_queue_retry = lambda sid: None
    m._schedule_usage_enrichment = lambda sid: None
    m._has_pending_usage_enrichment = lambda s: False
    from packages.core import qq_reports
    qq_reports.kick = lambda sid: None
    m._notifications.dispatch_completion_nonblocking = lambda *args: None
    outcomes = []

    s = setup()
    w = make_worker(s.id)
    intent = m.request_delayed_restart(s.id)
    for _ in range(1000):
        assert m.request_delayed_restart(s.id)['revision'] == intent['revision']
    assert w.status == 'running' and not lifecycle
    outcomes.append('running request/settings merge/1000 duplicate requests: one pending intent, no interruption')
    w.status = 'idle'
    w._terminal_finishing = True
    m._maybe_restart_pending(w)
    assert w.status == 'idle' and not lifecycle
    w._terminal_finishing = False
    w._queue_unit_active = True
    w._current_handoff_acked = True
    m._maybe_restart_pending(w)
    assert w.status == 'idle'
    w._queue_unit_active = False
    m._maybe_restart_pending(w)
    assert w.status == 'restarting'
    await asyncio.wait_for(completed.get(), 3)
    assert len(lifecycle) == 2
    outcomes.append('temporary idle during terminal publication or queue cleanup: deferred until valid boundary')
    await clear()

    # Exercise actual production queue consumer, reservation, handoff and
    # terminal publisher. Gate publication to request a restart inside it.
    s = setup()
    s.queue_pending = [row(1), row(2), row(3, locked=True)]
    w = make_worker(s.id, status='idle')
    w._consume_task = asyncio.create_task(m._consumer(w))
    w.pending_signal.put_nowait({'type': 'queue_signal'})
    assert await asyncio.wait_for(handoffs.get(), 3) == ('old', 'task-1')
    publish_gate = asyncio.Event()
    w._terminal_finishing = True
    terminal = {'status': 'done', 'result': 'complete', 'taskSeq': 1,
                'taskId': None, 'taskIdempotent': False, 'resultCursor': 1,
                'terminalKey': 'isolated-1', 'sourceSessionId': None}
    publication = asyncio.create_task(m._publish_terminal_events(w, terminal, s))
    await publish_entered.wait()
    m.request_delayed_restart(s.id)
    assert not lifecycle and handoffs.empty()
    publish_gate.set()
    await publication
    # This represents report transport completion before actual bookkeeping.
    events.append({'type': 'report.transport.completed'})
    m._finish_terminal_bookkeeping(w, terminal)
    done = await asyncio.wait_for(completed.get(), 3)
    second = await asyncio.wait_for(handoffs.get(), 3)
    assert second == (done['replacementWorkerId'], 'task-2')
    assert [i['id'] for i in s.queue_pending] == ['3']
    assert len([e for e in events if e['type'] == 'worker.result']) == 1
    assert next(i for i,e in enumerate(events) if e['type'] == 'report.transport.completed') < next(i for i,e in enumerate(events) if e.get('delayedRestart', {}).get('status') == 'restarting')
    outcomes.append('actual FIFO task handoff + gated result/report + idle retirement: old writes task 1 only; new writes task 2 once; locked task 3 remains')
    publish_gate = None
    await clear()

    s = setup()
    s.queue_paused = True
    s.queue_pending = [row(1)]
    w = make_worker(s.id, status='idle')
    m.request_delayed_restart(s.id)
    assert w.status == 'restarting'
    await asyncio.wait_for(completed.get(), 3)
    await settle()
    assert handoffs.empty() and s.queue_pending[0]['id'] == '1' and s.queue_paused
    outcomes.append('already idle + paused queue: immediate restart, pause and FIFO retained')
    await clear()

    s = setup()
    s.queue_pending = [row(1)]
    w = make_worker(s.id, status='queued')
    m.request_delayed_restart(s.id)
    assert w.status == 'restarting'
    done = await asyncio.wait_for(completed.get(), 3)
    assert await asyncio.wait_for(handoffs.get(), 3) == (done['replacementWorkerId'], 'task-1')
    outcomes.append('queued but not handed off: retire before old consumer can take next item')
    await clear()

    s = setup()
    item = row(1)
    s.queue_pending = [item]
    w = make_worker(s.id, status='idle')
    reservation_gate = asyncio.Event()
    w._consume_task = asyncio.create_task(m._consumer(w))
    w.pending_signal.put_nowait({'type': 'queue_signal'})
    await reservation_entered.wait()
    m.request_delayed_restart(s.id)
    assert not lifecycle and handoffs.empty()
    reservation_gate.set()
    done = await asyncio.wait_for(completed.get(), 3)
    assert await asyncio.wait_for(handoffs.get(), 3) == (done['replacementWorkerId'], 'task-1')
    assert item['nextAttemptAt'] <= time.time()
    assert len([entry for entry in s.history if entry.get('role') == 'user']) == 1
    outcomes.append('request during reserved receipt save: retract old reservation, zero timer backoff, new handoff once and one history receipt')
    reservation_gate = None
    await clear()

    s = setup()
    s.queue_pending = [row(1)]
    w = make_worker(s.id, status='idle')
    preparation_entered = asyncio.Event()
    never_released = asyncio.Event()
    preparation_calls = 0
    original_projection = m._maybe_inject_memory
    async def slow_preparation(s, text):
        nonlocal preparation_calls
        preparation_calls += 1
        if preparation_calls == 1:
            preparation_entered.set()
            await never_released.wait()
        return text
    m._maybe_inject_memory = slow_preparation
    w._consume_task = asyncio.create_task(m._consumer(w))
    w.pending_signal.put_nowait({'type': 'queue_signal'})
    await preparation_entered.wait()
    m.request_delayed_restart(s.id)
    assert w.status == 'restarting'
    done = await asyncio.wait_for(completed.get(), 3)
    assert await asyncio.wait_for(handoffs.get(), 3) == (done['replacementWorkerId'], 'task-1')
    assert not never_released.is_set()
    outcomes.append('slow memory preparation before running: cancelled safely without waiting; FIFO item immediately handed off once by replacement')
    m._maybe_inject_memory = original_projection
    await clear()

    s = setup()
    s.queue_pending = [row(1, kind='report')]
    w = make_worker(s.id, status='idle')
    m.request_delayed_restart(s.id)
    done = await asyncio.wait_for(completed.get(), 3)
    sent = await asyncio.wait_for(handoffs.get(), 3)
    assert sent[0] == done['replacementWorkerId'] and '@@@@by agent' in sent[1]
    assert not s.queue_pending
    outcomes.append('report/notice FIFO uses the same fenced consumer; new Worker takes report once')
    await clear()

    s = setup()
    s.system_prompt = 'isolated introduction'
    prompt = row(1)
    prompt.update(source='system_prompt', text=s.system_prompt)
    s.queue_pending = [prompt]
    original_adapter, original_spawn = m.get_adapter, m._spawn_process
    original_stdout, original_watchdog = m._read_stdout, m._watchdog
    m.get_adapter = lambda name: Adapter()
    async def fake_spawn(*args, **kwargs):
        return SimpleNamespace(returncode=None, stdin=Input('new-start'))
    async def parked(*args):
        await asyncio.Event().wait()
    m._spawn_process = fake_spawn
    m._read_stdout = parked
    m._watchdog = parked
    m._DEFAULTS_INITIALIZED = True
    started_worker = await original_create(s.id)
    assert isinstance(started_worker, m.Worker)
    assert len(s.queue_pending) == 1 and s.queue_pending[0] is prompt
    assert await asyncio.wait_for(handoffs.get(), 3) == ('new-start', s.system_prompt)
    assert not s.queue_pending
    outcomes.append('actual create recovery: unhanded fallback system prompt is retained and handed off once, not reinjected')
    m.get_adapter, m._spawn_process = original_adapter, original_spawn
    m._read_stdout, m._watchdog = original_stdout, original_watchdog
    await clear()

    s = setup()
    w = make_worker(s.id, status='idle')
    w.process = None
    m.request_delayed_restart(s.id)
    await asyncio.wait_for(completed.get(), 3)
    assert len(lifecycle) == 2
    outcomes.append('one-shot idle (no resident process): coordinator still executes')
    await clear()

    s = setup()
    first = m.request_delayed_restart(s.id)
    assert first['action'] == 'start'
    assert m.request_delayed_restart(s.id)['revision'] == first['revision']
    await asyncio.wait_for(completed.get(), 3)
    assert len(lifecycle) == 1 and lifecycle[0][0] == 'create'
    outcomes.append('absent worker: start semantics, duplicate accepted request creates once')
    await clear()

    s = setup()
    w = make_worker(s.id, status='idle')
    lock = await m._session_spawn_lock(s.id)
    await lock.acquire()
    m.request_delayed_restart(s.id)
    w.generation += 1
    lock.release()
    state = await asyncio.wait_for(completed.get(), 3)
    assert state['status'] == 'cancelled' and not lifecycle
    outcomes.append('generation replaced before lock acquisition: old intent cannot kill new generation')
    await clear()

    s = setup()
    w = make_worker(s.id)
    m.request_delayed_restart(s.id)
    m._cancel_session_restart(s.id)
    w.status = 'idle'
    m._maybe_restart_pending(w)
    assert not w.pending_restart and not lifecycle
    outcomes.append('immediate kill/restart/force supersession: cancelled intent never restarts next idle')
    await clear()

    s = setup()
    w = make_worker(s.id, status='idle')
    kill_gate = asyncio.Event()
    m.request_delayed_restart(s.id)
    await kill_entered.wait()
    m._cancel_session_restart(s.id)
    kill_gate.set()
    await settle()
    await settle()
    assert not any(op == 'create' for op, _ in lifecycle)
    outcomes.append('immediate lifecycle request while old process exits: cancels replacement before spawn')
    kill_gate = None
    await clear()

    s = setup()
    w = make_worker(s.id, status='idle')
    spawn_error = 'isolated spawn refusal'
    m.request_delayed_restart(s.id)
    state = await asyncio.wait_for(completed.get(), 3)
    assert state['status'] == 'failed' and state['error'] == spawn_error
    assert m.delayed_restart_state(s.id)['status'] == 'failed'
    spawn_error = None
    m.request_delayed_restart(s.id)
    state = await asyncio.wait_for(completed.get(), 3)
    assert state['status'] == 'completed'
    outcomes.append('spawn failure: observable failed state survives old Worker removal; explicit retry succeeds')
    await clear()

    s = setup()
    w = make_worker(s.id, status='idle')
    refuse_stop = True
    m.request_delayed_restart(s.id)
    state = await asyncio.wait_for(completed.get(), 3)
    assert state['status'] == 'failed' and 'did not stop' in state['error']
    assert m.find_alive_worker_by_session(s.id) is w
    assert not any(op == 'create' for op, _ in lifecycle)
    assert isinstance(m.request_delayed_restart(s.id), str)
    outcomes.append('old runtime refuses exit: retained fenced registry; no replacement writer or unsafe automatic retry')
    refuse_stop = False
    await clear()

    s = setup()
    w = make_worker(s.id, status='idle')
    w.status = 'held'
    assert isinstance(m.request_delayed_restart(s.id), str)
    assert not lifecycle
    w.process = None
    w._consume_task = asyncio.create_task(settle())
    await w._consume_task
    assert m.find_alive_worker_by_session(s.id) is None
    assert isinstance(m.request_delayed_restart(s.id), str)
    w._consume_task = None
    w.process = SimpleNamespace(returncode=None, stdin=Input(w.worker_id))
    outcomes.append('held takeover: delayed request rejects; no replacement writer')
    w.status = 'running'
    m.request_delayed_restart(s.id)
    started = time.perf_counter_ns()
    for _ in range(100000):
        m._maybe_restart_pending(w)
    guard_ns = (time.perf_counter_ns() - started) / 100000
    started = time.perf_counter_ns()
    for _ in range(10000):
        m.request_delayed_restart(s.id)
    duplicate_ns = (time.perf_counter_ns() - started) / 10000
    await clear()
    result = {'passed_scenarios': outcomes, 'running_guard_mean_ns': guard_ns,
              'duplicate_request_mean_ns': duplicate_ns,
              'limitations': ['provider/process/disk/report boundaries replaced in memory',
                              'no live service/browser/native CLI', 'not a test suite']}
    Path(__file__).with_name('experiment-results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)

asyncio.run(main())
