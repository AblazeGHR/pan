"""Lifecycle-only client of the existing same-user plugin IPC bridge."""
import json
import os
from pathlib import Path


def request(root, method, path, body=None, instance=None):
    from packages.plugins.ipc import request as ipc_request
    if root is None:
        address = os.environ.get('PAN_PLUGIN_IPC')
        if not address:
            raise OSError('Lifecycle IPC unavailable')
    else:
        state = json.loads((Path(root) / 'data' / 'core-ipc.json').read_text(encoding='utf-8'))
        if instance and state.get('pid') != instance.get('pid'):
            raise OSError('Lifecycle IPC instance mismatch')
        address = state['address']
    return ipc_request({'operation': 'cli_http', 'session_id': '', 'method': method,
                        'path': path, 'body': body, 'instance': instance}, address=address)
