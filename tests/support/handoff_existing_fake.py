"""Local fake CLI, never loads provider configuration or credentials."""
import json
import os
from pathlib import Path
import sys
import time

root = Path(os.environ['HANDOFF_TEST_ROOT'])
def emit(value):
    print(json.dumps(value), flush=True)

with (root / 'cli-audit.jsonl').open('a', encoding='utf-8') as log:
    log.write(json.dumps({'pid': os.getpid(), 'argv': sys.argv}) + '\n')
emit({'type': 'system', 'subtype': 'init', 'session_id': 'fake-existing-B'})
for line in sys.stdin:
    payload = json.loads(line)
    text = payload.get('message', {}).get('content', [{}])[0].get('text', '')
    with (root / 'cli-audit.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'pid': os.getpid(), 'text': text}) + '\n')
    if 'hold-turn' in text:
        (root / 'running').write_text('running', encoding='utf-8')
        while not (root / 'release').exists():
            time.sleep(.02)
    emit({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'fake answer'}]}})
    emit({'type': 'result', 'result': 'fake answer', 'is_error': False})
