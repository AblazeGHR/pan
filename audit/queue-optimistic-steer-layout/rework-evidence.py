"""Summarize direct experiment artifacts and reject inconsistent delivery claims."""
import json
from pathlib import Path

area=Path(__file__).resolve().parent
rows=json.loads(area.joinpath('rework-fixed.json').read_text(encoding='utf-8'))
out=[]
for row in rows:
    name=row['input']
    if name=='bridge-missing-response':
        if not row['returned'] or row['active'] or any(e['type']=='pan.steer_receipt' for e in row['events']):
            raise RuntimeError('Missing RPC blocked terminal or fabricated acceptance')
        later=row['afterLateAndNext']
        if len([e for e in later if e['type']=='result'])!=2 or len([e for e in later if e['type']=='pan.steer_receipt'])!=1:
            raise RuntimeError('Late receipt / future turn evidence incomplete')
        out.append({'input':name,'terminalWithoutReceipt':True,'futureTurnResult':True,'lateReceipt':True})
        continue
    queue=row['queue'];history=row['history'];disk=row['disk']
    entry={'input':name,'controls':row.get('controls'),'queue':len(queue),'history':len(history),
           'diskQueue':len(disk['queue_pending']),'diskHistory':len(disk.get('history',[])),
           'pending':bool(queue and queue[0].get('queueSteerPending'))}
    if name.startswith('cancel-') and row['controls']==0:
        if len(queue)!=1 or history or queue[0].get('queueSteerPending') or disk['queue_pending'][0].get('queueSteerPending'):
            raise RuntimeError('Confirmed pre-write cancellation left a reservation')
        entry['writerOutcomes']=row['writerOutcomes']
    elif name=='item-replaced-before-receipt':
        if queue[0]['text']!='new identity body' or history or disk['queue_pending'][0]['revision']!=2:
            raise RuntimeError('Old receipt mutated the new queue identity')
    else:
        expected=2 if name=='terminal-delayed-boundary' else 1
        if queue or len(history)!=expected or disk['queue_pending'] or len(disk.get('history',[]))!=expected:
            raise RuntimeError('Accepted original handoff not settled exactly once: '+name)
        if name=='terminal-delayed-boundary' and [h.get('content') for h in history] != ['normal terminal','full terminal-delayed-boundary']:
            raise RuntimeError('Normal terminal or original handoff history identity changed')
    if 'lifecycleEntered' in row and not row['lifecycleEntered']:
        raise RuntimeError('Lifecycle remains blocked by receipt wait')
    if 'beforeReceipt' in row:
        before=row['beforeReceipt']
        if before['history'] or not before['queue'][0].get('queueSteerPending'):
            raise RuntimeError('Attempted write was treated as safe failure or delivered without receipt')
    if name in {'restart-boundary','kill-boundary','terminal-delayed-boundary'} and not row['osBoundaryEntered']:
        raise RuntimeError('Real lifecycle wrapper never reached OS boundary')
    if name=='replacement-before-receipt' and row['finalWorker']['status']!='idle':
        raise RuntimeError('Old receipt affected replacement Worker status')
    if name=='missing-receipt-late':
        if not row['unknownDisk']['queue_pending'][0].get('queueSteerPending') or row['unknownDisk']['history']:
            raise RuntimeError('Unknown outcome was retried or represented as history')
        entry['unknownThenLateAccepted']=True
    out.append(entry)

backend=json.loads(area.joinpath('backend-results.json').read_text(encoding='utf-8'))
fifo=json.loads(area.joinpath('fifo-fixed.json').read_text(encoding='utf-8'))
for row in backend+fifo:
    for event in row['events']:
        if event.get('type')=='queue.item_delivered' and '"__serverPath"' in json.dumps(event):
            raise RuntimeError('Public delivery receipt leaked an internal attachment path')
out.append({'input':'public-receipt-attachment-path','internalKeys':0})
area.joinpath('rework-summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False))
