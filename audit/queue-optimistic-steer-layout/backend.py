"""Isolated direct experiments. No pytest/unittest suite or native provider."""
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import session, worker
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.web import server

session.SESSION_DIR = ROOT / "audit/queue-optimistic-steer-layout/runtime/experiments"
events = []
async def broadcast(data):
    events.append(copy.deepcopy(data))
worker._broadcast = broadcast

async def experiment(name, change=None, *, reject=False, duplicate=False, delay=False):
    s = session.create(name, adapter="codex")
    full = "original body " + "长正文" * 120
    item = {"id":"q_"+name,"queueItemId":"q_"+name,"type":"task","kind":"task",
            "schemaVersion":1,"revision":3,"text":full,"source":"agent",
            "sourceSessionId":"ses_1234567890abcdef","taskId":"T-identity","taskIdSource":"active",
            "clientMessageId":"client-"+name,"eventId":"event-"+name,"channel":"pan",
            "envelope":{"from":"MA","report":{"event":"stable"}},
            "deliveryState":"queued","parts":[{"type":"text","text":full}]}
    s.queue_pending = [item]
    session.save(s)
    controls=[]
    stdout=asyncio.StreamReader()
    gate=asyncio.Event()
    class Stdin:
        def write(self, data):
            controls.append(json.loads(data))
        async def drain(self):
            if not delay:
                gate.set()
            async def receipt():
                await gate.wait()
                stdout.feed_data((json.dumps({"type":"pan.steer_receipt","requestId":controls[-1]["requestId"],
                                             "ok":not reject,"error":"provider rejected"})+"\n").encode())
            asyncio.create_task(receipt())
    w=worker.Worker("w_"+name,s.id,CodexAdapter(),status="running",
                    process=SimpleNamespace(returncode=None,stdin=Stdin(),stdout=stdout))
    worker.workers[w.worker_id]=w
    worker._register_worker(w)
    reader=asyncio.create_task(worker._read_stdout(w))
    if change:
        change(s,item,w)
    start=len(events)
    result=asyncio.create_task(server.api_session_queue_steer(s.id,item["id"],{"expectedRevision":3}))
    during={}
    if delay:
        for _ in range(100):
            if controls: break
            await asyncio.sleep(.01)
        during={"snapshot":server._session_queue_items(s),"fifo":worker._select_queue_unit(s),
                "duplicate":await worker.steer_queue_item(s.id,item["id"],3),
                "edit":await server.api_session_queue_edit_lock(s.id,item["id"],{"editToken":"mine","expectedRevision":3}),
                "delete":await server.api_session_queue_delete(s.id,item["id"])}
        gate.set()
    answer=await result
    second=await server.api_session_queue_steer(s.id,item["id"],{"expectedRevision":3}) if duplicate else None
    reader.cancel()
    await asyncio.gather(reader,return_exceptions=True)
    session.save(s)
    disk=json.loads((session.SESSION_DIR/(s.id+".json")).read_text(encoding="utf-8"))
    outcome={"input":name,"response":answer,"repeat":second,"controls":controls,
             "queue":copy.deepcopy(s.queue_pending),"history":copy.deepcopy(s.history),
             "events":events[start:],"during":during,"persistedQueue":disk.get("queuePending"),
             "original":item}
    worker.workers.pop(w.worker_id,None)
    worker._unregister_worker(w)
    return outcome

async def main():
    results=[]
    fixture = session.SESSION_DIR / "attachment.txt"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("isolated attachment", encoding="utf-8")
    cases=[("success",None,dict(duplicate=True)),("reject",None,dict(reject=True)),
           ("pause",lambda s,i,w:setattr(s,"queue_paused",True),{}),
           ("manual-lock",lambda s,i,w:i.update(queueLockManual=True),{}),
           ("reports-lock",lambda s,i,w:i.update(queueLockAutoReport=True),{}),
           ("revision",lambda s,i,w:i.update(revision=4),{}),
           ("lease",lambda s,i,w:worker.acquire_queue_edit_lock(s,i["id"],"other"),{}),
           ("readonly",lambda s,i,w:setattr(s,"readonly_session",True),{}),
           ("idle",lambda s,i,w:setattr(w,"status","idle"),{}),
           ("unsupported",lambda s,i,w:setattr(w,"adapter",CbcAdapter()),{}),
           ("report",lambda s,i,w:i.update(type="report",source="report",parts=[],result={"full":"报告"*150}),{}),
           ("system",lambda s,i,w:i.update(type="notice",source="automation",parts=[],noticeKind="background_job_terminal",result="system"),{}),
           ("channel",lambda s,i,w:i.update(type="qq",source="qq",parts=[],channelTarget="group:1",nickname="fixture"),{}),
           ("unknown",lambda s,i,w:i.update(type="unknown"),{}),
           ("attachment",lambda s,i,w:i["parts"].append({"type":"attachment","attachmentId":"fixture","displayName":"attachment.txt","__serverPath":str(fixture)}),{}),
           ("stale-attachment",lambda s,i,w:i["parts"].append({"type":"attachment","__serverPath":str(fixture)+".missing"}),{}),
           ("unknown-part",lambda s,i,w:i["parts"].append({"type":"unknown"}),{}),
           ("report-attachment",lambda s,i,w:i.update(type="report",result="full report"),{}),
           ("pending",None,dict(delay=True))]
    for name,change,options in cases:
        results.append(await experiment(name,change,**options))
    path=ROOT/"audit/queue-optimistic-steer-layout/backend-results.json"
    path.write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps([{"input":r["input"],"ok":r["response"].get("ok"),"controls":len(r["controls"]),
                       "queue":len(r["queue"]),"history":len(r["history"])} for r in results]))

asyncio.run(main())
