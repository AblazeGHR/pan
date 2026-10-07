"""Exercise the real Codex bridge control/RPC reducer with a local scripted peer."""
import io
import json
import sys
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from packages.core.adapters.codex import app_server_wrapper as bridge
from packages.core import session
out=[]
events=[]
bridge._write_stdout=lambda event:events.append(event)
for rejected in [False,True]:
    app=bridge.AppServer('node','fixture',str(ROOT),[])
    app.thread_id='thread-fixture'
    controls=Queue()
    sent=[]
    native=[]
    def request(method,params):
        sent.append({'id':len(sent)+1,'method':method,'params':params})
        return len(sent)
    app._request=request
    step=0
    def next_message(timeout=None):
        global step
        step+=1
        if step==1:
            controls.put({'type':'steer','text':'provider full body','requestId':'request'})
            return {'id':1,'result':{'turn':{'id':'turn-fixture'}}}
        if step==2:
            native.append({'beforeResponseReceipts':list(events)})
            return {'id':2,**({'error':{'message':'provider refusal'}} if rejected else {'result':{'turnId':'turn-fixture'}})}
        return {'method':'fixture.done'}
    app._next=next_message
    app._handle_server_message=lambda message,state:state.update(done=True)
    events=[]
    app.run_turn('initial turn',control_queue=controls)
    out.append({'input':'rejected' if rejected else 'accepted','native':sent,'responseBoundary':native,'events':events})
events=[]
saved=sys.stdin
try:
    sys.stdin=SimpleNamespace(buffer=io.BytesIO(b'{"type":"steer","text":"late","requestId":"idle"}\n'))
    tasks=Queue();controls=Queue()
    bridge._read_pan_stdin(tasks,controls,app._turn_active)
    out.append({'input':'idle','events':events,'controlsQueued':controls.qsize()})
finally:
    sys.stdin=saved
old={'role':'user','content':'provider full body','messageId':'pan:1234567890abcdef1234567890abcdef','source':'agent',
     'sourceSessionId':'ses_1234567890abcdef','taskId':'T','taskIdSource':'active','queueItemIds':['q'],
     'queueEnvelopes':[{'type':'task','source':'agent','envelope':{'report':{'full':True}}}],
     'parts':[{'type':'text','text':'provider full body'}]}
new=session.assign_pan_message_ids([{'role':'user','content':old['content'],'nativeItemId':'native-fixture'}],previous_history=[old])
out.append({'input':'native-reimport-match','old':old,'new':new})
(ROOT/'audit/queue-optimistic-steer-layout/bridge-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False))
