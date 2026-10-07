"""Deterministic direct interleavings against real bridge and queue persistence."""
import asyncio
import copy
import json
import sys
import threading
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import session, worker
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.adapters.codex import app_server_wrapper as bridge

area = ROOT / 'audit/queue-optimistic-steer-layout'
session.SESSION_DIR = area / 'runtime/rework'
evidence = []

def bridge_missing():
    app = bridge.AppServer('node', 'fixture', str(ROOT), [])
    app.thread_id = 'fixture'
    controls = Queue(); sent = []; events = []
    original = bridge._write_stdout
    bridge._write_stdout = lambda event: events.append(copy.deepcopy(event))
    def request(method, params):
        sent.append({'id':len(sent)+1,'method':method})
        return len(sent)
    app._request = request
    sequence = Queue()
    sequence.put({'id':1,'result':{'turn':{'id':'turn-1'}}})
    sequence.put({'method':'turn/completed','params':{'threadId':'fixture','turn':{'id':'turn-1','status':'completed','items':[]}}})
    original_next=app._next
    def peer_next(timeout=300):
        message=original_next(timeout)
        if message.get('id')==1:
            controls.put({'type':'steer','text':'full steer','requestId':'lost'})
        return message
    app._next=peer_next
    app.incoming=sequence
    thread=threading.Thread(target=lambda:app.run_turn('first',control_queue=controls),daemon=True)
    thread.start();thread.join(.35)
    result={'input':'bridge-missing-response','returned':not thread.is_alive(),
            'active':app._turn_active.is_set(),'events':copy.deepcopy(events),'sent':copy.deepcopy(sent)}
    if not thread.is_alive():
        # Exercise the real reader's late-RPC path, then a future native turn.
        import io
        late={'id':2,'result':{}}
        app.process=SimpleNamespace(stdout=io.BytesIO((json.dumps(late)+'\n').encode()))
        app._read_stdout()
        app.incoming=Queue()
        app.incoming.put({'id':3,'result':{'turn':{'id':'turn-2'}}})
        app.incoming.put({'method':'turn/completed','params':{'threadId':'fixture','turn':{'id':'turn-2','status':'completed','items':[]}}})
        app.run_turn('next',control_queue=controls)
        result['afterLateAndNext']=copy.deepcopy(events)
    else:
        # Retire the deliberately hung baseline thread without native I/O.
        sequence.put({'id':2,'error':{'message':'fixture cleanup'}})
        thread.join(1)
    bridge._write_stdout=original
    return result

async def queue_case(name):
    s=session.create(name,adapter='codex')
    item={'id':'q-'+name,'type':'task','text':'full '+name,'source':'agent','revision':1,
          'deliveryState':'queued','sourceSessionId':'ses_1234567890abcdef'}
    s.queue_pending=[item];session.save(s)
    controls=[];written=asyncio.Event();drain=asyncio.Event()
    class Stdin:
        def write(self,data):
            controls.append(json.loads(data));written.set()
            if name=='write-error-unknown':raise BrokenPipeError('fixture ambiguous write')
        async def drain(self): await drain.wait()
    process=SimpleNamespace(returncode=None,stdin=Stdin())
    w=worker.Worker('w-'+name,s.id,CodexAdapter(),status='running',process=process)
    worker.workers[w.worker_id]=w;worker._register_worker(w)
    original_save=session._save_body;original_bcast=worker._broadcast
    original_receipt=worker._save_receipt;outcomes=[]
    async def observe_receipt(target,**kwargs):
        try:return await original_receipt(target,**kwargs)
        finally:
            outcome=kwargs.get('outcome')
            if target is s and outcome is not None:
                outcomes.append({'succeeded':outcome.succeeded,'cancelled':outcome.cancelled,
                                 'error':type(outcome.error).__name__ if outcome.error else None})
    worker._save_receipt=observe_receipt
    committed=threading.Event();release=threading.Event()
    def gated_save(target,**kwargs):
        if name=='cancel-before-marker-commit' and target is s and item.get('queueSteerPending') and not committed.is_set():
            committed.set();release.wait(2)
            raise OSError('fixture writer failed before commit')
        value=original_save(target,**kwargs)
        if target is s and item.get('queueSteerPending') and not committed.is_set():
            committed.set();release.wait(2)
        return value
    async def bcast(event):
        if name=='broadcast-failure': raise RuntimeError('fixture broadcast failure')
    worker._broadcast=bcast
    if name in {'cancel-after-marker','cancel-before-marker-commit'}:session._save_body=gated_save
    task=asyncio.create_task(worker._steer_queue_item(s.id,item['id'],1))
    result={'input':name}
    try:
        if name in {'cancel-after-marker','cancel-before-marker-commit'}:
            await asyncio.to_thread(committed.wait,1)
            task.cancel();release.set()
        else:
            await asyncio.wait_for(written.wait(),1)
            lock=await worker._session_spawn_lock(s.id)
            try:
                await asyncio.wait_for(lock.acquire(),.08)
                result['lifecycleEntered']=True;lock.release()
            except asyncio.TimeoutError: result['lifecycleEntered']=False
            result['beforeReceipt']={'history':copy.deepcopy(s.history),'queue':copy.deepcopy(s.queue_pending)}
            drain.set()
            await asyncio.sleep(.03)
            if name=='cancel-after-write': task.cancel()
            if name=='settle-save-failure':
                fail_count=0
                def fail_once(target,**kwargs):
                    nonlocal fail_count
                    if target is s and not item.get('queueSteerPending') and fail_count==0:
                        fail_count+=1;raise OSError('fixture settle disk failure')
                    return original_save(target,**kwargs)
                session._save_body=fail_once
            if name=='replacement-before-receipt':
                new=worker.Worker(w.worker_id,s.id,CodexAdapter(),status='idle',generation=w.generation+1)
                worker.workers[w.worker_id]=new
                result['replacementGeneration']=new.generation
            if name=='item-replaced-before-receipt':
                replacement={'id':item['id'],'type':'task','text':'new identity body','revision':2,
                             'source':'user','deliveryState':'queued'}
                s.queue_pending=[replacement];session.save(s)
            if name in {'restart-boundary','kill-boundary','terminal-delayed-boundary'}:
                entered=[]
                original_kill=worker._kill_process_tree
                async def os_boundary(target):
                    entered.append({'workerId':target.worker_id,'generation':target.generation,'status':target.status})
                    raise RuntimeError('fixture OS boundary reached')
                worker._kill_process_tree=os_boundary
                try:
                    if name=='terminal-delayed-boundary':
                        from packages.core import qq_reports
                        original_kick=qq_reports.kick
                        original_notify=worker._notifications.dispatch_completion_nonblocking
                        qq_reports.kick=lambda *args:None
                        worker._notifications.dispatch_completion_nonblocking=lambda *args:None
                        try:
                            worker.request_delayed_restart(s.id)
                            terminal=await worker._persist_terminal_state(w,s,'done','normal terminal')
                            await worker._publish_terminal_events(w,terminal,s)
                            worker._finish_terminal_bookkeeping(w,terminal)
                            await asyncio.sleep(.04)
                            result['delayedRestart']=worker.delayed_restart_state(s.id)
                        finally:
                            qq_reports.kick=original_kick
                            worker._notifications.dispatch_completion_nonblocking=original_notify
                    else:
                        try:
                            call=worker.restart_worker(w.worker_id) if name=='restart-boundary' else worker.kill_worker(w.worker_id)
                            await asyncio.wait_for(call,.2)
                        except BaseException as exc:result['controlBoundaryReturn']=str(exc)
                    result['osBoundaryEntered']=entered
                finally:worker._kill_process_tree=original_kill
            if name=='missing-receipt-late':
                result['unknownReturn']=await asyncio.wait_for(asyncio.shield(task),31)
                result['unknownDisk']=json.loads((session.SESSION_DIR/(s.id+'.json')).read_text(encoding='utf-8'))
            for future in list(w._steer_receipts.values()):
                if not future.done():future.set_result(None)
        try: result['return']=await asyncio.wait_for(asyncio.shield(task),1)
        except BaseException as exc:result['exception']=type(exc).__name__
        await asyncio.sleep(.06)
        result.update(controls=len(controls),queue=copy.deepcopy(s.queue_pending),history=copy.deepcopy(s.history),
                      disk=json.loads((session.SESSION_DIR/(s.id+'.json')).read_text(encoding='utf-8')))
        result['finalWorker']={'registeredOriginal':worker.workers.get(w.worker_id) is w,
                               'generation':worker.workers[w.worker_id].generation if w.worker_id in worker.workers else None,
                               'status':worker.workers[w.worker_id].status if w.worker_id in worker.workers else None}
        result['writerOutcomes']=outcomes
    finally:
        release.set();drain.set();session._save_body=original_save;worker._broadcast=original_bcast
        worker._save_receipt=original_receipt
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        worker.workers.pop(w.worker_id,None);worker._unregister_worker(w)
    return result

async def fifo_cancel(fails):
    s=session.create('fifo-cancel',adapter='codex')
    item={'id':'fifo','type':'task','text':'accepted FIFO','source':'agent','revision':1,'deliveryState':'writing'}
    s.queue_pending=[item];session.save(s)
    w=worker.Worker('fifo-worker',s.id,CodexAdapter(),status='running')
    worker.workers[w.worker_id]=w;worker._register_worker(w)
    committed=threading.Event();release=threading.Event()
    original=session._save_body
    def gated(target,**kwargs):
        if target is s and not committed.is_set():
            if not fails:original(target,**kwargs)
            committed.set();release.wait(2)
            if fails:raise OSError('fixture first receipt writer failed')
            return
        return original(target,**kwargs)
    session._save_body=gated
    task=asyncio.create_task(worker._commit_queue_handoff(w,s,[item]))
    try:
        await asyncio.to_thread(committed.wait,1);task.cancel();release.set()
        try:await task
        except asyncio.CancelledError:pass
        return {'input':'fifo-cancel-writer-'+('failure' if fails else 'commit'),'acked':w._current_handoff_acked,
                'queue':copy.deepcopy(s.queue_pending),'history':copy.deepcopy(s.history),
                'disk':json.loads((session.SESSION_DIR/(s.id+'.json')).read_text(encoding='utf-8'))}
    finally:
        session._save_body=original;release.set()
        worker.workers.pop(w.worker_id,None);worker._unregister_worker(w)

async def main():
    evidence.append(bridge_missing())
    for name in ['cancel-after-marker','cancel-before-marker-commit','cancel-after-write','broadcast-failure',
                 'settle-save-failure','replacement-before-receipt','item-replaced-before-receipt','write-error-unknown','restart-boundary',
                 'kill-boundary','terminal-delayed-boundary','missing-receipt-late']:
        evidence.append(await queue_case(name))
    evidence.extend([await fifo_cancel(False),await fifo_cancel(True)])
    area.joinpath(sys.argv[1]).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{key:row[key] for key in ('input','returned','active','exception','controls','lifecycleEntered','acked','unknownReturn','osBoundaryEntered') if key in row} for row in evidence]))

if sys.argv[1]=='--bridge-baseline':
    import subprocess
    import types
    baseline=types.ModuleType('bridge_baseline')
    baseline.__file__=bridge.__file__
    source=subprocess.check_output(['git','show','c726d742:packages/core/adapters/codex/app_server_wrapper.py'],cwd=ROOT)
    exec(compile(source,baseline.__file__,'exec'),baseline.__dict__)
    bridge=baseline
    path=area/'rework-baseline.json'
    rows=json.loads(path.read_text(encoding='utf-8'))
    rows[0]=bridge_missing()
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows[0]))
else:
    asyncio.run(main())
