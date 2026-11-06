"""Read-only canonical release projection for the existing plugin catalog."""
import json
from pathlib import Path
import sqlite3
import sys


def canonical_path():
    from packages.core import session
    return session.SESSION_DIR.parent / 'job-final' / 'event-creators' / 'creators.sqlite3'


def owns(identity):
    path = canonical_path()
    if not path.exists():
        return False
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='final_documents'").fetchone():
            return False
        return bool(db.execute("SELECT 1 FROM final_documents WHERE kind IN ('action_group','action_group_handoff') AND id=?", (identity,)).fetchone())


def extend(catalog, service=None):
    from packages.plugins.catalog import Plugin
    if service is not None:
        path = service.creators.path
    else:
        path = canonical_path()
    if not path.exists():
        return
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='final_documents'").fetchone():
            return
        rows = db.execute("SELECT r.document FROM final_documents g JOIN final_documents r "
            "ON r.kind='action_group_release' AND r.id=json_extract(g.document,'$.release') "
            "WHERE g.kind='action_group'").fetchall()
    for row in rows:
        release = json.loads(row[0])
        pid = 'action-group-' + release['groupId'].replace('_', '-')
        if pid in catalog.plugins:
            if catalog.plugins[pid].version == release['version']:
                continue
            raise ValueError('Canonical Group plugin conflicts with an existing plugin')
        directory = Path(release['directory'])
        catalog.servers[pid + '-provider'] = {'name': pid + '-provider', 'command': sys.executable,
            'args': ['-m', 'packages.actions.provider', '--snapshot', str(directory / 'snapshot.json')],
            'cwd': str(Path(__file__).resolve().parents[2])}
        catalog._add(Plugin(pid, release['name'], {'kind': 'pan_api',
            'capabilities_file': str(directory / 'capabilities.json'),
            'action_group_release': release['version']}, version=release['version']))


async def invoke(runtime, descriptor, arguments, *, transport_request_key=None):
    """Resolve only the trusted bound descriptor, never caller-supplied refs."""
    from packages.jobs import final_contracts as fc
    from .groups import Groups
    service = runtime.group_service() if getattr(runtime, 'group_service', None) else None
    if service is None:
        raise fc.ContractError('provider_unavailable', 'Canonical Group service unavailable')
    state = service.auth.permissions_status()
    actor = {'type': 'agent', 'id': 'agent', 'revision': state['revision']}
    release = Groups(service).release(descriptor['action_group_release'], actor=actor)
    expected = 'action-group-' + release['groupId'].replace('_', '-')
    if descriptor['plugin'] != expected:
        raise fc.ContractError('reference_conflict', 'Group release binding mismatch')
    alias = descriptor['source_name'].rsplit('.', 1)[-1]
    if release.get('compatibility'):
        if not transport_request_key:
            raise fc.ContractError('invalid_schema', 'Flat compatibility calls require an outer transport identity')
        try:
            result = await Groups(service).invoke(release['version'], alias, arguments, transport_request_key, actor=actor)
        except fc.ContractError:
            raise
        except Exception:
            # A provider/response failure can follow an effect. Return the
            # existing receipt identity; this query never restarts the command.
            result = service.operation_for_key('invoke', transport_request_key, actor=actor)
        return legacy_result(release, alias, result)
    result = await Groups(service).invoke(release['version'], alias,
        arguments['arguments'], arguments['requestKey'], actor=actor)
    return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
            'structuredContent': result, 'isError': result.get('result', {}).get('status') in {'failed', 'uncertain'}}


def legacy_result(release, alias, receipt):
    """Project the original JSON/text/error contract from the same receipt."""
    tool = next(t for t in release['tools'] if t['alias'] == alias)
    value, error = None, None
    result = receipt['result']
    commands = result.get('commands', [])
    if result.get('status') != 'completed' or len(commands) != 1:
        code = commands[-1].get('exitCode') if commands else None
        error = ('TimeoutError' if commands and commands[-1].get('code') == 'timeout' else
                 f'Command exited {code}' if code is not None else 'Action result unconfirmed')
    elif commands[0].get('stdoutTruncated') or commands[0].get('stderrTruncated'):
        error = 'Command output exceeds 512 KiB'
    else:
        try:
            text = commands[0]['stdout']
            value = text if tool['outputFormat'] == 'text' else json.loads(text)
        except (ValueError, KeyError) as exc:
            error = str(exc)
    meta = {'panOperation': {'operationId': receipt['operationId'], 'requestKey': receipt['requestKey'],
                             'status': result.get('status', receipt['status']), 'receiptStatus': receipt['status'],
                             'actionRef': tool['actionRef'], 'groupRelease': release['version']}}
    if not error:
        reply = {'content': [{'type': 'text', 'text': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}],
                 **({'structuredContent': value} if isinstance(value, dict) else {}), 'isError': False, '_meta': meta,
                 'processTreeEnded': commands[0].get('cleanupProof', {}).get('confirmed') is True}
        from .provider import RESULT_LIMIT
        if len(json.dumps(reply, ensure_ascii=False).encode('utf-8')) > RESULT_LIMIT:
            error = 'Action output exceeds 1 MiB encoded result limit'
    if error:
        return {'content': [{'type': 'text', 'text': json.dumps({'error': error,
            'message': 'Action failed; do not automatically replay side effects.'})}], 'isError': True, '_meta': meta}
    return reply
