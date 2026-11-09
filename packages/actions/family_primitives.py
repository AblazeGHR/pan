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


async def canonical_api_get(action, arguments, *, before_effect=None):
    action = await asyncio.to_thread(validate_action, action, automation=False)
    if not action['enabled'] or action['effect'] == 'high_risk':
        raise ValueError('Action is disabled or requires an approval policy')
    try:
        jsonschema.Draft202012Validator(action['input_schema']).validate(arguments)
    except jsonschema.ValidationError as exc:
        category = 'Additional properties' if exc.validator == 'additionalProperties' else str(exc.validator)
        raise ValueError('Action argument ' + category + ' validation failed') from None
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
            if before_effect:
                await before_effect()
            async with client.stream('GET', base.rstrip('/') + ex['path'], params=values) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes(65536):
                    if len(content) + len(chunk) > OUTPUT_LIMIT:
                        raise ValueError('API output exceeds 512 KiB')
                    content.extend(chunk)
                result = json.loads(content)
    else:
        raise PermissionError('Canonical API primitive only accepts api.get')
    reply = {'content': [{'type': 'text', 'text': result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)}],
            **({'structuredContent': result} if isinstance(result, dict) else {}), 'isError': False}
    if len(json.dumps(reply, ensure_ascii=False).encode('utf-8')) > RESULT_LIMIT:
        raise ValueError('Action output exceeds 1 MiB encoded result limit')
    return reply
