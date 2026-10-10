"""Read-only source rollout copy; private home, no model turn or credentials.

Only protocol event types, timings and static errors are printed. All children
belong to an owned Job; nothing connects to the production kernel.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from queue import Queue, Empty
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from packages.core.adapters.codex.adapter import _resolve_codex_node, _resolve_codex_js
from packages.core.terminal.guard import JobObjectGuard
from packages.core import worker
from packages.core.adapters.codex import CodexAdapter


async def worker_frame(raw):
    stream = asyncio.StreamReader()
    stream.feed_data(raw)
    stream.feed_eof()
    w = SimpleNamespace(process=SimpleNamespace(stdout=stream), worker_id='probe')
    lines = [line async for line in worker._iter_stdout_lines(w)]
    return CodexAdapter().parse_event(lines[0].decode('utf-8', errors='replace'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rollout', type=Path, required=True)
    args = parser.parse_args()
    source_hash = hashlib.sha256(args.rollout.read_bytes()).hexdigest()
    with args.rollout.open(encoding='utf-8') as source:
        meta = json.loads(source.readline())
    thread_id = meta['payload']['id']
    spec = importlib.util.spec_from_file_location('native_probe', ROOT / 'audit/terminal/codex/probe_codex_remote_tui.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    with tempfile.TemporaryDirectory(prefix='pan-old-thread-probe-') as temporary:
        home = Path(temporary) / 'home'
        home.mkdir()
        helper.seed_placeholder_credentials(str(home), [])
        sessions = home / 'sessions' / '2026' / '10' / '07'
        sessions.mkdir(parents=True)
        shutil.copyfile(args.rollout, sessions / args.rollout.name)
        # Every inference endpoint points at an unused loopback port. No turn
        # is sent. Disable persisted MCP plugins through an isolated home.
        options = ['-c', 'model_provider="probe_bh"', '-c', 'model="probe-model"',
                   '-c', 'model_providers.probe_bh={name="probe_bh",base_url="http://127.0.0.1:1/v1",wire_api="responses"}']
        env = {k: v for k, v in os.environ.items() if not k.startswith('PAN_')}
        env['CODEX_HOME'] = str(home)
        argv = [sys.executable, '-u', str(ROOT / 'packages/core/adapters/codex/wrapper.py'),
                '--app-server', '--native-control', '--node-path', _resolve_codex_node(),
                '--codex-path', _resolve_codex_js(), '--codex-extra-args', json.dumps(options),
                '--thread-id', thread_id]
        guard = JobObjectGuard()
        process = None
        readers = []
        frames = Queue()
        result = {'source_bytes': args.rollout.stat().st_size, 'events': []}
        started = time.monotonic()
        try:
            process = subprocess.Popen(argv, cwd=temporary, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            guard.assign(int(process._handle))
            def read(stream, channel):
                for raw in stream:
                    frames.put((channel, raw))
            for stream, channel in [(process.stdout, 'stdout'), (process.stderr, 'stderr')]:
                thread = threading.Thread(target=read, args=(stream, channel), daemon=True)
                thread.start()
                readers.append(thread)
            issued = False
            deadline = started + 35
            while time.monotonic() < deadline:
                try:
                    channel, raw = frames.get(timeout=.1)
                except Empty:
                    if process.poll() is not None:
                        break
                    continue
                if channel == 'stderr':
                    result['stderr_lines'] = result.get('stderr_lines', 0) + 1
                    continue
                event = asyncio.run(worker_frame(raw))
                if event is None:
                    result['unparseable_frames'] = result.get('unparseable_frames', 0) + 1
                    continue
                record = {'type': event.get('type'), 'seconds': round(time.monotonic()-started, 3), 'wire_bytes': len(raw), 'worker_frame_parseable': True}
                if event.get('type') == 'thread.started' and not issued:
                    issued = True
                    process.stdin.write(b'{"type":"control_mode","requestId":"probe","mode":"native","generation":0}\n')
                    process.stdin.flush()
                if event.get('type') == 'pan.control_mode':
                    record.update({key: event.get(key) for key in ['ok', 'mode', 'generation', 'error']})
                    result['events'].append(record)
                    if event.get('ok') is True and event.get('mode') == 'native':
                        process.stdin.write((json.dumps({'type': 'control_mode',
                            'requestId': 'probe-back', 'mode': 'pan',
                            'generation': event['generation']}) + '\n').encode())
                        process.stdin.flush()
                        continue
                    break
                if event.get('type') != 'pan.native_output':
                    result['events'].append(record)
                else:
                    result['output_frames'] = result.get('output_frames', 0) + 1
            result['returncode_before_cleanup'] = process.poll()
            result['mode_issued'] = issued
        finally:
            if process is not None:
                _, remaining = guard.terminate_tree(process.pid, timeout=3)
                assert not remaining
                process.wait(3)
                for thread in readers:
                    thread.join(2)
                    assert not thread.is_alive()
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            assert guard.close()
    result['source_unchanged'] = hashlib.sha256(args.rollout.read_bytes()).hexdigest() == source_hash
    result['cleanup_converged'] = True
    print(json.dumps(result))
    assert result['source_unchanged']
    assert result['mode_issued']
    assert any(e.get('type') == 'pan.control_mode' and e.get('ok') is True
               and e.get('mode') == 'native' for e in result['events'])
    assert any(e.get('type') == 'pan.control_mode' and e.get('ok') is True
               and e.get('mode') == 'pan' and e.get('generation') == 2
               for e in result['events'])


if __name__ == '__main__':
    main()
