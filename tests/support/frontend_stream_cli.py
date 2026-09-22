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
        # The stress label deliberately creates one openable multi-tool group
        # and a much taller answer so the browser test exercises the exact
        # variable-height/scrolling path that is easy to miss with one tool.
        stress_label = label in {'visual-order-stress', 'switch-delta'}
        # Codex can deliver the first assistant text delta before the command
        # item is completed.  Keep this inverse arrival order in the fixture:
        # the durable adapter records [tool, assistant], while the live UI
        # initially observes [assistant, tool].  This is the runtime ordering
        # that a refresh can hide and that the browser regression must catch.
        text_before_tool = label == 'switch-delta'
        tool_count = 8 if stress_label else 1
        chunk_count = 220 if stress_label else 80
        chunks = [f'answer:{label}\n'] + [f'line {i:03d} streaming text\n' for i in range(chunk_count)]
        cumulative = ''
        if text_before_tool:
            first_chunk = chunks.pop(0)
            cumulative += first_chunk
            emit({'type': 'content.part', 'role': 'assistant', 'content': first_chunk,
                  'stream_text': cumulative, 'item_id': f'answer:{label}', 'turn_id': turn, 'delta': True})
            time.sleep(.008)
        for tool_index in range(tool_count):
            tool_id = f'tool:{label}:{tool_index}'
            emit({'type': 'content.part', 'role': 'assistant', 'content': 'running command',
                  'item_id': tool_id, 'turn_id': turn, 'delta': True})
            emit({'type': 'assistant', 'item_id': tool_id, 'turn_id': turn, 'final': True,
                  'message': {'content': [{'type': 'tool_use', 'name': 'Command',
                                            'input': {'command': f'{label}:{tool_index}'}}]}})
        for chunk in chunks:
            cumulative += chunk
            emit({'type': 'content.part', 'role': 'assistant', 'content': chunk,
                  'stream_text': cumulative, 'item_id': f'answer:{label}', 'turn_id': turn, 'delta': True})
            time.sleep(.008 if stress_label else .018)
        emit({'type': 'assistant', 'item_id': f'answer:{label}', 'turn_id': turn, 'final': True,
              'message': {'content': [{'type': 'text', 'text': cumulative}]}})
        emit({'type': 'result', 'result': cumulative, 'is_error': False})


if __name__ == '__main__':
    main()
