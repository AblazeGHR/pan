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


async def execute(*args, **kwargs):
    raise PermissionError('Legacy Action execution retired; use canonical invocation')


async def run(snapshot):
    # The generic capability transport sends UTF-8 JSON lines. Windows pipe
    # locale encoding can silently turn valid UTF-8 characters into other text.
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if request['method'] == 'provider.initialize':
                result = {'protocol_version': 'pan-capability/v1'}
            elif request['method'] == 'capability.invoke':
                params = request['params']
                try:
                    result = await execute()
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
