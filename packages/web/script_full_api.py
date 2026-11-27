"""Opt-in local full API access using an actual UserAuth session.

The dedicated pipe authenticates the script profile; the separate UserAuth
session is passed through the normal ASGI boundary and endpoint gates.
"""
from __future__ import annotations

import base64
import binascii
import getpass
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from packages.plugins.lifecycle import io
from . import script_auth

MAX_BODY = 1200000  # base64 plus JSON must fit the existing 2 MiB IPC frame
METHODS = {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}
FORBIDDEN = {'authorization', 'cookie', 'host', 'origin', 'content-length',
             'connection', 'transfer-encoding', 'proxy-authorization'}


def _session_path(root, name):
    directory, _, _ = script_auth._paths(root, name)
    return directory / 'user.session'


def _protector(name):
    from packages.core.terminal.secret_store import DpapiProtector
    return DpapiProtector(entropy=('pan-script-user-session|' + name).encode())


def provision(root, name, password, *, renew=False):
    """Owner-only setup; the password and UserAuth token never enter argv."""
    from packages.automation.auth import UserAuth
    from packages.core.terminal import win_pipe
    if renew:
        directory, config, _ = script_auth._paths(root, name)
        script_auth._private(directory.parent)
        script_auth._private(directory)
        script_auth._private(config)
        policy = script_auth.validate_profile(script_auth._read_json(config)['policy'])
        if policy.get('mode') != 'full-api' or not policy['enabled']:
            raise ValueError('full API profile unavailable')
    auth = UserAuth(Path(root) / 'automation')
    old_token = _user_token(root, name) if renew else None
    token = auth.login(password)
    path = _session_path(root, name)
    try:
        if not renew:
            script_auth.initialize(root, name, {'enabled': True, 'mode': 'full-api'})
        encrypted = _protector(name).protect(token.encode('ascii'))
        if renew:
            import secrets
            script_auth._private(path)
            temp = path.with_name('.user-session-' + secrets.token_hex(12))
            win_pipe.create_file_exclusive_owner_only(str(temp), encrypted)
            script_auth._private(temp)
            win_pipe.replace_file_atomic(str(temp), str(path))
        else:
            win_pipe.create_file_exclusive_owner_only(str(path), encrypted)
        script_auth._private(path)
    except Exception:
        auth.logout('Bearer ' + token)
        raise
    if old_token:
        auth.logout('Bearer ' + old_token)


def _user_token(root, name):
    path = _session_path(root, name)
    script_auth._private(path.parent.parent)
    script_auth._private(path.parent)
    script_auth._private(path)
    token = _protector(name).unprotect(path.read_bytes()).decode('ascii')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
        raise ValueError('invalid user session')
    return token


def _validate(method, path, body):
    if not isinstance(method, str) or method not in METHODS or not isinstance(path, str) or not isinstance(body, dict):
        raise ValueError('invalid request')
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise ValueError('invalid API path')
    parsed = urlsplit(path)
    decoded = unquote(parsed.path)
    if (parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith('/api/')
            or not decoded.startswith('/api/') or '\\' in decoded or '\x00' in decoded
            or any(part in ('.', '..') for part in decoded.split('/'))
            or re.search(r'%[0-9a-fA-F]{2}', decoded)
            or '%2f' in parsed.path.lower() or '%5c' in parsed.path.lower()):
        raise ValueError('invalid API path')
    if set(body) - {'headers', 'contentBase64'}:
        raise ValueError('invalid request envelope')
    headers = body.get('headers', {})
    if not isinstance(headers, dict) or len(headers) > 32:
        raise ValueError('invalid headers')
    for k, v in headers.items():
        if (not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}', k)
                or k.lower() in FORBIDDEN or k.lower().startswith('proxy-')
                or (k.lower().startswith('x-pan-') and k.lower() not in {'x-pan-expected-pid', 'x-pan-expected-created-at'})
                or not isinstance(v, str) or len(v) > 4096 or '\r' in v or '\n' in v):
            raise ValueError('invalid header')
    raw = body.get('contentBase64', '')
    if not isinstance(raw, str) or len(raw) > ((MAX_BODY + 2) // 3) * 4:
        raise ValueError('invalid body')
    try:
        content = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('invalid body') from None
    if len(content) > MAX_BODY:
        raise ValueError('invalid body')
    return headers, content


async def dispatch(root, name, method, path, body):
    headers, content = _validate(method, path, body)
    token = await io(_user_token, root, name)
    from . import server
    headers = {**headers, 'Authorization': 'Bearer ' + token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app, client=('127.0.0.1', 0), raise_app_exceptions=False),
                                 base_url='http://ipc', trust_env=False, follow_redirects=False) as client:
        response = await client.request(method, path, headers=headers, content=content)
    if len(response.content) > MAX_BODY:
        return {'ok': False, 'error': {'code': 413, 'message': 'Response exceeds IPC limit'}}
    return {'status': response.status_code,
            'headers': {k: v for k, v in response.headers.items() if k.lower() in ('content-type', 'location', 'etag')},
            'contentBase64': base64.b64encode(response.content).decode('ascii')}


def request(root, name, method, path, content=b'', headers=None):
    """Call from the same OS account; returns status, selected headers and bytes."""
    from packages.plugins.ipc import request as ipc_request
    root = Path(root)
    locator = json.loads((root / 'script-ipc.json').read_text(encoding='utf-8'))
    pid, address = locator['pid'], locator['address']
    if type(pid) is not int or pid <= 0 or not isinstance(address, str) or not re.fullmatch(
            r'\\\\\.\\pipe\\pan-script-' + str(pid) + r'-[0-9a-f]{32}', address):
        raise ValueError('invalid local IPC locator')
    body = {'headers': headers or {}, 'contentBase64': base64.b64encode(content).decode('ascii')}
    _validate(method, path, body)
    result = ipc_request({'session_id': '', 'operation': 'script_http', 'profile': name,
        'credential': script_auth.load_client(root, name), 'method': method, 'path': path, 'body': body}, address=address)
    if not isinstance(result, dict) or type(result.get('status')) is not int:
        raise RuntimeError('script request refused or unavailable')
    return {'status': result['status'], 'headers': result['headers'],
            'content': base64.b64decode(result['contentBase64'], validate=True)}


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Initialize or renew a full API script identity')
    parser.add_argument('operation', choices=('init', 'renew'))
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--profile', required=True)
    args = parser.parse_args()
    try:
        password = getpass.getpass('Pan administrator password: ')
        provision(args.data_dir, args.profile, password, renew=args.operation == 'renew')
    except Exception:
        print('Full API profile setup failed.', file=sys.stderr)
        return 1
    print('Full API profile setup completed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
