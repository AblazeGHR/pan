"""Observe reserve/write/receipt/failure boundaries directly in an isolated store."""
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from packages.core import session,worker
from packages.core.adapters.codex.adapter import CodexAdapter
session.SESSION_DIR=ROOT/'audit/queue-optimistic-steer-layout/runtime/fifo'
async def main():
    out=[]
    events=[]
    async def broadcast(event):events.append(copy.deepcopy(event))
    worker._broadcast=broadcast
    fixture=session.SESSION_DIR/'attachment.txt'
    fixture.parent.mkdir(parents=True,exist_ok=True)
    fixture.write_text('isolated attachment',encoding='utf-8')
    for outcome in ['write-failed','handed-off','receipt-save-failed']:
        s=session.create(outcome,adapter='codex')
        i={'id':'q_'+outcome,'queueItemId':'q_'+outcome,'type':'task','source':'agent','sourceSessionId':'ses_1234567890abcdef',
           'taskId':'T-fifo','taskIdSource':'active','text':'FIFO body','revision':1,'deliveryState':'queued'}
        s.queue_pending=[i]
        i['parts']=[{'type':'text','text':i['text']},{'type':'attachment','__serverPath':str(fixture),'displayName':'attachment.txt'}]
        event_start=len(events)
        session.save(s)
        w=worker.Worker('w',s.id,CodexAdapter(),process=SimpleNamespace(returncode=None),status='running')
        added=await worker._reserve_queue_unit(w,s,[i],i['text'],[1])
        reserved={'history':list(s.history),'queue':list(server_item.copy() for server_item in s.queue_pending)}
        if outcome=='write-failed':
            await worker._requeue_queue_unit(w,s,[i],'simulated stdin failure',added)
        else:
            original=worker._save_receipt
            calls=0
            async def transient(s, **kwargs):
                nonlocal calls
                calls+=1
                if calls==1: raise OSError('isolated transient receipt failure')
                return await original(s, **kwargs)
            if outcome=='receipt-save-failed': worker._save_receipt=transient
            try: await worker._commit_queue_handoff(w,s,[i])
            finally: worker._save_receipt=original
        out.append({'outcome':outcome,'reserved':reserved,'finalHistory':list(s.history),'finalQueue':list(s.queue_pending),
                    'acked':w._current_handoff_acked,'events':events[event_start:]})
    path=ROOT/'audit/queue-optimistic-steer-layout'/sys.argv[1]
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{'input':r['outcome'],'reservedHistory':len(r['reserved']['history']),'finalHistory':len(r['finalHistory']),
                       'finalQueue':len(r['finalQueue']),'acked':r['acked']} for r in out]))
asyncio.run(main())
