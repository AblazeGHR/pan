"""Synthetic native app-server used only by the isolated ACP integration test."""
import json
import sys
from pathlib import Path


def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)


counter = 0
current = None
for line in sys.stdin:
    msg = json.loads(line)
    method, request_id = msg.get('method'), msg.get('id')
    with Path('synthetic-native-rpc.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'method':method, 'params':msg.get('params')}) + '\n')
    if method == 'initialize':
        send({'id':request_id, 'result':{'userAgent':'synthetic'}})
    elif method in ('thread/start', 'thread/resume'):
        send({'id':request_id, 'result':{'thread':{'id':'synthetic-thread'}}})
    elif method == 'turn/start':
        counter += 1
        current = 'native-' + str(counter)
        send({'id':request_id, 'result':{'turn':{'id':current}}})
        send({'method':'turn/started', 'params':{'threadId':'synthetic-thread', 'turn':{'id':current}}})
        content = (msg.get('params', {}).get('input') or [{}])[0].get('text', '')
        if content != 'hold':
            send({'method':'turn/completed', 'params':{'threadId':'synthetic-thread',
                  'turn':{'id':current, 'status':'completed', 'items':[]}}})
    elif method == 'turn/interrupt':
        target = msg['params']['turnId']
        send({'id':request_id, 'result':{}})
        if target == current:
            send({'method':'turn/completed', 'params':{'threadId':'synthetic-thread',
                  'turn':{'id':target, 'status':'interrupted', 'items':[]}}})
