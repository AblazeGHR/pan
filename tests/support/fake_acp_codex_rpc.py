"""Synthetic native app-server used only by the isolated ACP integration test."""
import json
import sys
from pathlib import Path


def send(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)


counter = 0
current = None
awaiting_permission = False
permission_file = False
for line in sys.stdin:
    msg = json.loads(line)
    method, request_id = msg.get('method'), msg.get('id')
    with Path('synthetic-native-rpc.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'method':method, 'params':msg.get('params')}) + '\n')
    if method is None and request_id == 987 and awaiting_permission:
        awaiting_permission = False
        with Path('synthetic-approval.json').open('w', encoding='utf-8') as result:
            json.dump(msg.get('result'), result)
        if permission_file and msg.get('result', {}).get('decision') == 'accept':
            Path('tck-permission-probe.txt').write_text('hello', encoding='utf-8')
        send({'method':'turn/completed', 'params':{'threadId':'synthetic-thread',
              'turn':{'id':current, 'status':'completed', 'items':[]}}})
    elif method == 'initialize':
        send({'id':request_id, 'result':{'userAgent':'synthetic'}})
    elif method in ('thread/start', 'thread/resume'):
        send({'id':request_id, 'result':{'thread':{'id':'synthetic-thread'}}})
    elif method == 'turn/start':
        counter += 1
        current = 'native-' + str(counter)
        send({'id':request_id, 'result':{'turn':{'id':current}}})
        send({'method':'turn/started', 'params':{'threadId':'synthetic-thread', 'turn':{'id':current}}})
        content = (msg.get('params', {}).get('input') or [{}])[0].get('text', '')
        if content == 'permission-probe' or 'tck-permission-probe.txt' in content:
            awaiting_permission = True
            permission_file = 'tck-permission-probe.txt' in content
            send({'id':987, 'method':'item/commandExecution/requestApproval',
                  'params':{'threadId':'synthetic-thread', 'turnId':current,
                            'itemId':'synthetic-command', 'command':'synthetic'}})
        elif content != 'hold':
            send({'method':'turn/completed', 'params':{'threadId':'synthetic-thread',
                  'turn':{'id':current, 'status':'completed', 'items':[]}}})
    elif method == 'turn/interrupt':
        target = msg['params']['turnId']
        send({'id':request_id, 'result':{}})
        if target == current:
            send({'method':'turn/completed', 'params':{'threadId':'synthetic-thread',
                  'turn':{'id':target, 'status':'interrupted', 'items':[]}}})
