"""Immutable Action release provider on the generic capability stdio protocol."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
from urllib.parse import urlsplit

import httpx
import jsonschema

from .store import validate_action

OUTPUT_LIMIT = 512 * 1024
RESULT_LIMIT = 1024 * 1024  # Leave JSON-RPC framing headroom below the generic 2 MiB reader.


async def execute(action, arguments):
    action = await asyncio.to_thread(validate_action, action, automation=False)
    if not action['enabled'] or action['effect'] == 'high_risk':
        raise ValueError('Action is disabled or requires an approval policy')
    jsonschema.Draft202012Validator(action['input_schema']).validate(arguments)
    values = dict(arguments)
    for key, schema in action['input_schema'].get('properties', {}).items():
        if key not in values and isinstance(schema, dict) and 'default' in schema:
            values[key] = schema['default']
    ex = action['execution']
    if ex['kind'] == 'api':
        base = os.environ.get('PAN_API_URL', '')
        url = urlsplit(base)
        if url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'} or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment:
            raise ValueError('Internal API requires an explicit local Pan HTTP endpoint')
        async with asyncio.timeout(30), httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            async with client.stream('GET', base.rstrip('/') + ex['path'], params=values) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes(65536):
                    if len(content) + len(chunk) > OUTPUT_LIMIT:
                        raise ValueError('API output exceeds 512 KiB')
                    content.extend(chunk)
                result = json.loads(content)
    else:
        argv = [str(values[a[1:-1]]) if a.startswith('{') and a.endswith('}') else a for a in ex['args']]
        from packages.jobs.window import command_window_options
        windows = os.name == 'nt'
        process = await asyncio.create_subprocess_exec(
            *([sys.executable, '-m', 'packages.actions.command_runner'] if windows else [ex['executable'], *argv]),
            cwd=str(Path(__file__).resolve().parents[2]) if windows else ex['cwd'],
            stdin=asyncio.subprocess.PIPE if windows else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **(command_window_options() if windows else {'start_new_session': True}))
        # Drain bounded streams concurrently; never retain unbounded child output.
        async def bounded(stream):
            chunks, size = [], 0
            while chunk := await stream.read(65536):
                size += len(chunk)
                if size > OUTPUT_LIMIT:
                    raise ValueError('Command output exceeds 512 KiB')
                chunks.append(chunk)
            return b''.join(chunks).decode('utf-8', errors='replace')
        try:
            if windows:
                process.stdin.write((json.dumps({'argv': [ex['executable'], *argv], 'cwd': ex['cwd'],
                    'timeout_sec': ex.get('timeout_sec', 30)}) + '\n').encode())
                await process.stdin.drain()
                process.stdin.close()
            out, err, _ = await asyncio.wait_for(asyncio.gather(bounded(process.stdout), bounded(process.stderr), process.wait()), ex.get('timeout_sec', 30))
        except BaseException:
            if windows:
                if process.returncode is None:
                    process.kill()  # Closing the helper's Job handle kills its entire command tree.
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(process.wait(), 5)
            raise
        if process.returncode:
            raise RuntimeError(f'Command exited {process.returncode}: {err[:2000]}')
        result = out if action['output_format'] == 'text' else json.loads(out)
    reply = {'content': [{'type': 'text', 'text': result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)}],
            **({'structuredContent': result} if isinstance(result, dict) else {}), 'isError': False}
    if ex['kind']=='command':
        tree_ended=windows
        if not windows:
            try:os.killpg(process.pid,0)
            except ProcessLookupError:tree_ended=True
        reply['processTreeEnded']=tree_ended
    if len(json.dumps(reply, ensure_ascii=False).encode('utf-8')) > RESULT_LIMIT:
        raise ValueError('Action output exceeds 1 MiB encoded result limit')
    return reply


async def run(snapshot):
    document = json.loads(Path(snapshot).read_text(encoding='utf-8'))
    actions = {a['id']: a for a in document['actions']}
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if request['method'] == 'provider.initialize':
                result = {'protocol_version': 'pan-capability/v1'}
            elif request['method'] == 'capability.invoke':
                params = request['params']
                try:
                    result = await execute(actions[params['operation']], params['arguments'])
                except Exception as exc:
                    # A confirmed tool failure is a tool result, not a broken
                    # JSON-RPC connection. Subsequent explicit calls stay usable.
                    result = {'content': [{'type': 'text', 'text': json.dumps({
                        'error': str(exc) or type(exc).__name__,
                        'message': 'Action failed; do not automatically replay side effects.'})}], 'isError': True}
            else:
                raise ValueError('Unknown provider method')
            reply = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except Exception as exc:
            reply = {'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32602, 'message': str(exc)}}
        print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True)
    asyncio.run(run(parser.parse_args().snapshot))
