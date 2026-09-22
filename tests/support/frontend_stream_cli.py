"""Deterministic provider boundary; real Pan Worker owns queue/history/WS.

Accepts the actual CBC or Codex adapter stdin contract. Codex events use the
app-server wrapper's wire shapes, including UI-only deltas and durable finals.
No network/model calls are made.
"""
import json
import os
import sys
import time
from pathlib import Path


def emit(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def main():
    codex = '--codex' in sys.argv
    emit({'type': 'thread.started', 'thread_id': f'fixture-{os.getpid()}'} if codex
         else {'type': 'system', 'subtype': 'init', 'session_id': f'fixture-{os.getpid()}'})
    for line in sys.stdin:
        data = json.loads(line)
        text = data.get('text') if codex else data.get('message', {}).get('content', [{}])[0].get('text')
        if not text:
            continue
        label = text.strip()
        if label == 'hold-queue':
            emit({'type': 'thinking', 'content': 'queue gate waiting', 'item_id': 'queue-gate', 'final': True})
            gate = Path(os.environ['PAN_E2E_RUNTIME']) / 'release-queue'
            while not gate.exists():
                time.sleep(.02)
        if not codex:
            time.sleep(.15)
            emit({'type': 'assistant', 'message': {'id': label, 'content': [
                {'type': 'thinking', 'thinking': f'think:{label}'},
                {'type': 'tool_use', 'id': f'tool:{label}', 'name': 'Bash', 'input': {'command': label}},
                {'type': 'text', 'text': f'answer:{label}'},
            ]}})
            emit({'type': 'result', 'result': f'answer:{label}', 'is_error': False})
            continue
        turn = f'turn:{label}'
        emit({'type': 'thinking', 'content': f'think:{label}', 'item_id': f'think:{label}', 'turn_id': turn, 'final': True})
        # One tool starts as text and finalizes with a different role, just as
        # app-server command output can do. The native item id stays stable.
        emit({'type': 'content.part', 'role': 'assistant', 'content': 'running command',
              'item_id': f'tool:{label}', 'turn_id': turn, 'delta': True})
        emit({'type': 'assistant', 'item_id': f'tool:{label}', 'turn_id': turn, 'final': True,
              'message': {'content': [{'type': 'tool_use', 'name': 'Command', 'input': {'command': label}}]}})
        chunks = [f'answer:{label}\n'] + [f'line {i:03d} streaming text\n' for i in range(80)]
        cumulative = ''
        for chunk in chunks:
            cumulative += chunk
            emit({'type': 'content.part', 'role': 'assistant', 'content': chunk,
                  'stream_text': cumulative, 'item_id': f'answer:{label}', 'turn_id': turn, 'delta': True})
            time.sleep(.018)
        emit({'type': 'assistant', 'item_id': f'answer:{label}', 'turn_id': turn, 'final': True,
              'message': {'content': [{'type': 'text', 'text': cumulative}]}})
        emit({'type': 'result', 'result': cumulative, 'is_error': False})


if __name__ == '__main__':
    main()
