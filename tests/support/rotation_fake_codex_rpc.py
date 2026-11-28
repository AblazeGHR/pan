"""Owned deterministic app-server: one quota failure, then successful turns."""
import json
import os
import sys
from pathlib import Path

def emit(obj):
    print(json.dumps(obj, separators=(',', ':')), flush=True)

root = Path(os.getcwd())
log = root / 'rotation-native-rpc.jsonl'
marker = root / 'rotation-limit-emitted'
for line in sys.stdin:
    msg = json.loads(line)
    method, req = msg.get('method'), msg.get('id')
    if method == 'initialize':
        emit({'id':req, 'result':{'userAgent':'owned-fixture'}})
    elif method in ('thread/start', 'thread/resume'):
        emit({'id':req, 'result':{'thread':{'id':'owned-thread'}}})
    elif method == 'turn/start':
        text = (msg.get('params',{}).get('input') or [{}])[0].get('text','')
        with log.open('a', encoding='utf-8') as out:
            out.write(json.dumps({'pid':os.getpid(),'text':text})+'\n')
        emit({'id':req, 'result':{'turn':{'id':'owned-turn'}}})
        emit({'method':'turn/started','params':{'threadId':'owned-thread','turn':{'id':'owned-turn'}}})
        if not marker.exists():
            marker.write_text('limited', encoding='utf-8')
            turn = {'id':'owned-turn','status':'failed','items':[],
                    'error':{'message':'synthetic quota','codexErrorInfo':'usageLimitExceeded'}}
        else:
            turn = {'id':'owned-turn','status':'completed','items':[]}
        emit({'method':'turn/completed','params':{'threadId':'owned-thread','turn':turn}})
