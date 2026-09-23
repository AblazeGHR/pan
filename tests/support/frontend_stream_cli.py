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
    claude = '--claude' in sys.argv
    emit({'type': 'thread.started', 'thread_id': f'fixture-{os.getpid()}'} if codex
         else {'type': 'system', 'subtype': 'init', 'session_id': f'fixture-{os.getpid()}'})
    for line in sys.stdin:
        data = json.loads(line)
        if codex:
            text = data.get('text')
        elif claude:
            content = data.get('message', {}).get('content', [])
            text = content[0].get('text') if content and isinstance(content[0], dict) else None
        else:
            text = data.get('message', {}).get('content', [{}])[0].get('text')
        if not text:
            continue
        label = text.strip()
        if 'crash-recovery-history-idempotency' in label:
            receipt = Path(os.environ['PAN_E2E_RUNTIME']) / 'crash-recovery-cli-inputs.jsonl'
            with receipt.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({
                    'marker': 'crash-recovery-history-idempotency',
                    'pid': os.getpid(),
                }) + '\n')
        if label == 'handoff-context-before-crash':
            runtime = Path(os.environ['PAN_E2E_RUNTIME'])
            (runtime / 'handoff-context-ready').write_text('running', encoding='utf-8')
            release = runtime / 'release-handoff-context'
            while not release.exists():
                time.sleep(.01)
        if label == 'hold-queue':
            emit({'type': 'thinking', 'content': 'queue gate waiting', 'item_id': 'queue-gate', 'final': True})
            gate = Path(os.environ['PAN_E2E_RUNTIME']) / 'release-queue'
            while not gate.exists():
                time.sleep(.02)
        if claude:
            chunk_count = 220 if label == 'background-live-claude' else 40
            chunks = [f'answer:{label}\n'] + [f'line {i:03d} streaming text\n' for i in range(chunk_count)]
            cumulative = ''
            for chunk in chunks:
                cumulative += chunk
                # Claude stream-json text deltas carry neither native item IDs
                # nor a cumulative stream_text field. The real adapter normalizes
                # each frame to an id-less assistant delta.
                emit({'type': 'stream_event', 'event': {
                    'type': 'content_block_delta',
                    'index': 0,
                    'delta': {'type': 'text_delta', 'text': chunk},
                }})
                time.sleep(.008 if label == 'background-live-claude' else .015)
            emit({'type': 'assistant', 'message': {'content': [
                {'type': 'thinking', 'thinking': f'think:{label}'},
                {'type': 'tool_use', 'name': 'Bash', 'input': {'command': label}},
                {'type': 'text', 'text': cumulative},
            ]}})
            emit({'type': 'result', 'result': cumulative, 'is_error': False})
            continue
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
        if label == 'codex-five-story-items':
            runtime = Path(os.environ['PAN_E2E_RUNTIME'])
            for story in range(1, 6):
                if story > 1:
                    emit({'type': 'assistant', 'final': True,
                          'item_id': f'tool:{label}:{story}', 'turn_id': turn,
                          'message': {'content': [{'type': 'tool_use',
                              'name': 'Command', 'input': {'command': f'date-{story}'}}]}})
                item_id = f'story:{label}:{story}'
                prefix = f'story-{story}-first'
                full = f'{prefix}\nstory-{story}-final'
                emit({'type': 'content.part', 'role': 'assistant',
                      'delta': True, 'item_id': item_id, 'turn_id': turn,
                      'part': {'type': 'text', 'text': prefix},
                      'stream_text': prefix})
                if story > 1:
                    (runtime / f'five-story-ready-{story}').write_text('ready', encoding='utf-8')
                    release = runtime / f'five-story-release-{story}'
                    while not release.exists():
                        time.sleep(.01)
                emit({'type': 'content.part', 'role': 'assistant',
                      'delta': True, 'item_id': item_id, 'turn_id': turn,
                      'part': {'type': 'text', 'text': '\nstory-' + str(story) + '-final'},
                      'stream_text': full})
                emit({'type': 'assistant', 'final': True,
                      'item_id': item_id, 'turn_id': turn,
                      'message': {'content': [{'type': 'text', 'text': full}]}})
            emit({'type': 'result', 'result': full, 'is_error': False})
            continue
        if label == 'codex-final-first-background':
            full_text = f'answer:{label}\n' + ''.join(
                f'line {i:03d} streaming text\n' for i in range(160)
            )
            tool_id = f'tool:{label}'
            # Mirror app_server_wrapper.py: item/started emits a mutable tool
            # row, output/completion replaces it, and item/completed publishes
            # the final agentMessage before the late delta item notifications.
            emit({'type': 'thinking', 'content': f'think:{label}', 'item_id': f'think:{label}',
                  'turn_id': turn, 'final': True})
            tool_args = {'command': label}
            emit({'type': 'assistant', 'delta': True, 'replace': False,
                  'stream_text': f'Command({json.dumps(tool_args, ensure_ascii=True, separators=(",", ":"))})',
                  'message': {'content': [{'type': 'tool_use', 'name': 'Command', 'input': tool_args}]},
                  'item_id': tool_id, 'turn_id': turn})
            emit({'type': 'assistant', 'final': True, 'replace': True,
                  'message': {'content': [{'type': 'tool_use', 'name': 'Command', 'input': tool_args}]},
                  'item_id': tool_id, 'turn_id': turn})
            completed_item_id = f'agent-completed:{label}'
            delta_item_id = f'agent-delta:{label}'
            emit({'type': 'assistant', 'final': True,
                  'message': {'content': [{'type': 'text', 'text': full_text}]},
                  'item_id': completed_item_id, 'turn_id': turn})
            runtime = Path(os.environ['PAN_E2E_RUNTIME'])
            (runtime / 'codex-final-first-ready').write_text('ready', encoding='utf-8')
            release = runtime / 'codex-final-first-release'
            while not release.exists():
                time.sleep(.01)
            cumulative = ''
            chunks = [f'answer:{label}\n'] + [f'line {i:03d} streaming text\n' for i in range(160)]
            for chunk in chunks:
                cumulative += chunk
                emit({'type': 'content.part', 'role': 'assistant', 'content': chunk,
                      'part': {'type': 'text', 'text': chunk}, 'stream_text': cumulative,
                      'item_id': delta_item_id, 'turn_id': turn, 'delta': True})
                time.sleep(.008)
            emit({'type': 'result', 'result': cumulative, 'is_error': False})
            continue
        emit({'type': 'thinking', 'content': f'think:{label}', 'item_id': f'think:{label}', 'turn_id': turn, 'final': True})
        # One tool starts as text and finalizes with a different role, just as
        # app-server command output can do. The native item id stays stable.
        # The stress label deliberately creates one openable multi-tool group
        # and a much taller answer so the browser test exercises the exact
        # variable-height/scrolling path that is easy to miss with one tool.
        stress_label = label in {
            'visual-order-stress', 'switch-delta', 'background-resume',
            'background-live-select',
        }
        # Codex can deliver the first assistant text delta before the command
        # item is completed.  Keep this inverse arrival order in the fixture:
        # the durable adapter records [tool, assistant], while the live UI
        # initially observes [assistant, tool].  This is the runtime ordering
        # that a refresh can hide and that the browser regression must catch.
        text_before_tool = label in {'switch-delta', 'background-live-select'}
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
