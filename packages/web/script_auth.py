"""Owner-local, separately authenticated script profiles for the Core IPC."""
from __future__ import annotations

import re
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
RULES = {
    ('GET', '/api/automation/events'),
    ('GET', '/api/list'),
    ('GET', '/api/sessions'),
    ('GET', '/api/sessions/{sessionId}'),
    ('GET', '/api/sessions/{sessionId}/worker/delayed-restart'),
    ('POST', '/api/sessions/{sessionId}/worker/delayed-restart'),
    ('POST', '/api/sessions/wake'),
    ('GET', '/api/sessions/{sessionId}/queue/state-token'),
    ('GET', '/api/sessions/{sessionId}/queue/wake-receipt'),
    ('POST', '/api/sessions/{sessionId}/queue/conditional-wake'),
}
PROFILE = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


def _read_json(path):
    raw = Path(path).read_bytes()
    if len(raw) > 131072:
        raise ValueError('profile file too large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate profile key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid number')))


def _paths(root, name):
    if not isinstance(name, str) or not PROFILE.fullmatch(name):
        raise ValueError('invalid profile name')
    directory = Path(root) / 'script-api' / name
    return directory, directory / 'profile.json', directory / 'client.secret'


def _private(path):
    if os.name != 'nt':
        raise OSError('script credentials are unsupported on this platform')
    from packages.core.terminal import win_pipe
    from packages.core.terminal.secret_store import SecretStore
    if win_pipe.is_reparse_point(str(path)):
        raise OSError('reparse path refused')
    SecretStore(root=path.parent).verify_owner_only(path)


def initialize(root, name, policy):
    """Create one private profile and encrypted client secret; never print it."""
    validate_profile(policy)
    if os.name != 'nt':
        raise OSError('script credentials are unsupported on this platform')
    from packages.core.terminal import win_pipe
    from packages.core.terminal.secret_store import DpapiProtector
    directory, config, client = _paths(root, name)
    parent = directory.parent
    if win_pipe.is_reparse_point(str(parent)) or win_pipe.is_reparse_point(str(directory)):
        raise OSError('reparse path refused')
    win_pipe.create_owner_only_directory(str(parent))
    _private(parent)
    win_pipe.create_owner_only_directory(str(directory))
    _private(directory)
    credential = secrets.token_hex(32)
    digest = hashlib.sha256(credential.encode('ascii')).hexdigest()
    encrypted = DpapiProtector(entropy=('pan-script-api|' + name).encode()).protect(credential.encode('ascii'))
    # Exclusive creation prevents accidental rotation or overwrite of an
    # active identity. An interrupted init must be resolved explicitly.
    win_pipe.create_file_exclusive_owner_only(str(client), encrypted)
    win_pipe.create_file_exclusive_owner_only(str(config), json.dumps({'policy': policy, 'digest': digest}).encode())
    _private(client)
    _private(config)


def load_client(root, name):
    from packages.core.terminal.secret_store import DpapiProtector
    directory, _, client = _paths(root, name)
    _private(directory.parent)
    _private(directory)
    _private(client)
    result = DpapiProtector(entropy=('pan-script-api|' + name).encode()).unprotect(client.read_bytes()).decode('ascii')
    if not re.fullmatch(r'[0-9a-f]{64}', result):
        raise ValueError('invalid script credential')
    return result


def authenticate(root, name, credential):
    directory, config, _ = _paths(root, name)
    if not isinstance(credential, str) or not re.fullmatch(r'[0-9a-f]{64}', credential):
        raise ValueError('credential refused')
    _private(directory.parent)
    _private(directory)
    _private(config)
    value = _read_json(config)
    if not isinstance(value, dict) or set(value) != {'policy', 'digest'}:
        raise ValueError('invalid profile')
    digest = value['digest']
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid profile')
    if not hmac.compare_digest(hashlib.sha256(credential.encode('ascii')).hexdigest(), digest):
        raise ValueError('credential refused')
    profile = validate_profile(value['policy'])
    if not profile['enabled']:
        raise ValueError('profile disabled')
    return profile


def revoke(root, name):
    """Atomically disable a profile; retained ciphertext is no authority."""
    from packages.core.terminal import win_pipe
    directory, config, _ = _paths(root, name)
    _private(directory.parent)
    _private(directory)
    _private(config)
    value = _read_json(config)
    policy = validate_profile(value['policy'])
    value['policy']['enabled'] = False
    temporary = directory / ('.revoke-' + secrets.token_hex(12))
    win_pipe.create_file_exclusive_owner_only(str(temporary), json.dumps(value).encode())
    _private(temporary)
    win_pipe.replace_file_atomic(str(temporary), str(config))
    if policy.get('mode') == 'full-api':
        from packages.automation.auth import UserAuth
        from .script_full_api import _user_token
        token = _user_token(root, name)
        UserAuth(Path(root) / 'automation').logout('Bearer ' + token)


def validate_profile(profile):
    """Fail closed on unsupported operations or malformed target lists."""
    if isinstance(profile, dict) and set(profile) == {'enabled', 'mode'}:
        if type(profile['enabled']) is not bool or profile['mode'] != 'full-api':
            raise ValueError('invalid full API profile')
        return {'enabled': profile['enabled'], 'mode': 'full-api'}
    if not isinstance(profile, dict) or set(profile) not in ({'enabled', 'sessions', 'rules'}, {'enabled', 'sessions', 'rules', 'targetMode'}):
        raise ValueError('invalid profile')
    mode = profile.get('targetMode', 'fixed')
    if mode not in ('fixed', 'codex'):
        raise ValueError('invalid target mode')
    if type(profile['enabled']) is not bool or not isinstance(profile['sessions'], list):
        raise ValueError('invalid profile')
    sessions = profile['sessions']
    if mode == 'codex' and sessions:
        raise ValueError('dynamic targets cannot include fixed sessions')
    if len(sessions) > 256 or any(not isinstance(s, str) or not SESSION.fullmatch(s) for s in sessions) or len(set(sessions)) != len(sessions):
        raise ValueError('invalid sessions')
    rules = profile['rules']
    if not isinstance(rules, list) or len(rules) > len(RULES):
        raise ValueError('invalid rules')
    result = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {'method', 'path'}:
            raise ValueError('invalid rule')
        pair = (rule['method'], rule['path'])
        if not all(isinstance(v, str) for v in pair) or pair not in RULES or pair in result:
            raise ValueError('invalid rule')
        result.add(pair)
    return {'enabled': profile['enabled'], 'sessions': frozenset(sessions), 'rules': frozenset(result), 'targetMode': mode}


def _correlation(value, sid):
    # Reject unexpected fields rather than dropping identity differences.  The
    # terminal and event correlations must remain exactly comparable.
    from packages.core.system_fields import SYSTEM_FIELD_NAMES
    keys = set(SYSTEM_FIELD_NAMES) | {'generation', 'accountId', 'taskSeq', 'executionSeq', 'runtimeEpoch'}
    if not isinstance(value, dict) or set(value) - keys or value.get('sessionId') != sid:
        return None
    source = value.get('sourceSessionId')
    if source is not None and (not isinstance(source, str) or not 0 < len(source) <= 256):
        return None
    if any(not ((isinstance(v, str) and 0 < len(v) <= 256) or type(v) is int)
           for v in value.values()):
        return None
    return {key: val for key, val in value.items() if key != 'sourceSessionId'}


def permit(profile, method, path, body):
    """Validate an operation; collection responses still require projection."""
    if not profile['enabled'] or not isinstance(path, str) or not isinstance(body, dict):
        return False
    try:
        parsed = urlsplit(path)
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc or parsed.fragment or '%' in parsed.path or '\\' in parsed.path:
        return False
    route = parsed.path
    parts = route.split('/')
    sid = None
    if len(parts) >= 4 and parts[:3] == ['', 'api', 'sessions'] and parts[3] != 'wake':
        sid = parts[3]
        if not SESSION.fullmatch(sid) or sid not in profile['sessions']:
            return False
        parts[3] = '{sessionId}'
        route = '/'.join(parts)
    if (method, route) not in profile['rules']:
        return False
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
    except ValueError:
        return False
    if len({key for key, _ in query}) != len(query):
        return False
    q = dict(query)
    if route == '/api/automation/events':
        if set(q) - {'after', 'limit'} or not all(v.isascii() and v.isdecimal() and len(v) <= 18 for v in q.values()):
            return False
        return int(q.get('after', '0')) >= 0 and 1 <= int(q.get('limit', '20')) <= 100 and not body
    if route == '/api/sessions':
        return q == {'summary': '1'} and not body
    if route == '/api/sessions/{sessionId}':
        return q == {'historyLimit': '1'} and not body
    if route.endswith('/queue/state-token'):
        return not q and not body
    if route.endswith('/queue/wake-receipt'):
        return set(q) == {'clientMessageId'} and 0 < len(q['clientMessageId']) <= 256 and not body
    if route.endswith('/queue/conditional-wake'):
        return (not q and set(body) == {'clientMessageId', 'expectedVersion', 'source'}
                and body.get('source') == 'automation'
                and isinstance(body.get('clientMessageId'), str) and 0 < len(body['clientMessageId']) <= 256
                and isinstance(body.get('expectedVersion'), str)
                and re.fullmatch(r'[0-9a-f]{64}', body['expectedVersion']) is not None)
    if route.endswith('/worker/delayed-restart'):
        return not body and (q == {'existing_worker_only': 'true'} if method == 'POST' else not q)
    if route == '/api/sessions/wake':
        ids = body.get('sessionIds')
        return (not q and set(body) == {'sessionIds', 'source', 'clientMessageId'}
                and isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str) and ids[0] in profile['sessions']
                and body['source'] == 'automation' and isinstance(body['clientMessageId'], str)
                and 0 < len(body['clientMessageId']) <= 256)
    return not q and not body


def project_collection(profile, path, response):
    """Restrict list outputs without changing the upstream event cursor.

    This is not sufficient by itself to authorize a request. The caller must
    validate the transport, credential, profile, method, path and arguments.
    """
    if not isinstance(response, dict):
        raise ValueError('invalid collection response')
    route = urlsplit(path).path
    keys = {'/api/sessions': 'sessions', '/api/list': 'workers', '/api/automation/events': 'events'}
    key = keys.get(route)
    if key is None or not isinstance(response.get(key), list):
        raise ValueError('unsupported collection response')
    allowed = profile['sessions']
    rows = []
    for row in response[key]:
        if not isinstance(row, dict):
            raise ValueError('invalid collection row')
        if key == 'events':
            event = row.get('event')
            if not isinstance(event, dict):
                raise ValueError('invalid event')
            # The trusted event system is the authority. Source payload may
            # not override it or grant access to an unbound event.
            system = event.get('system')
            sid = system.get('sessionId') if isinstance(system, dict) else None
        else:
            sid = row.get('sessionId') if key == 'workers' else row.get('id')
        if isinstance(sid, str) and sid in allowed:
            if key == 'events':
                payload = event.get('payload')
                if (event.get('type') != 'runtime.issue.v1' or not isinstance(payload, dict)
                        or (payload.get('adapter'), payload.get('classification'), payload.get('confidence'))
                        != ('codex', 'codex_quota_exhausted', 'structured')):
                    continue
                correlation = _correlation(payload.get('correlation'), sid)
                issue_id = payload.get('issueId')
                if correlation is None or not isinstance(issue_id, str) or not 0 < len(issue_id) <= 256 or event.get('id') != issue_id:
                    continue
                if system.get('sourceSessionId') != payload['correlation'].get('sourceSessionId'):
                    continue
                if (type(row.get('seq')) is not int or row['seq'] < 0
                        or any(k in system and system[k] != correlation.get(k) for k in ('workerId', 'taskId'))):
                    continue
                projected = {'id': issue_id, 'type': 'runtime.issue.v1',
                    'payload': {'issueId': issue_id, 'adapter': 'codex', 'classification': 'codex_quota_exhausted',
                                'confidence': 'structured', 'correlation': correlation},
                    'system': {k: system[k] for k in ('sessionId', 'workerId', 'taskId') if k in system}}
                rows.append({'seq': row.get('seq'), 'event': projected})
            else:
                rows.append(row)
    # Return only deliberately supported fields. Global storage/count metadata
    # may leak activity outside the granted Session set.
    result = {key: rows}
    if key == 'events':
        cursor = response.get('nextAfter')
        if type(cursor) is not int or cursor < 0:
            raise ValueError('invalid event cursor')
        result['nextAfter'] = cursor
    return result


def bind(runtime, root):
    """Bind only explicit script operations; no general ASGI trust marker."""
    from packages.plugins.lifecycle import io

    async def call(name, credential, method, path, body):
        try:
            profile = await io(authenticate, root, name, credential)
            body = {} if body is None else body
            if profile.get('mode') == 'full-api':
                from .script_full_api import dispatch
                return await dispatch(root, name, method, path, body)
            from packages.web import server
            if profile['targetMode'] == 'codex':
                # Shallow server-owned registry, evaluated for every request.
                sessions = await server._store_read(server.sess.list_all, load_history=False)
                profile = dict(profile, sessions=frozenset(s.id for s in sessions if s.adapter == 'codex'))
            if not permit(profile, method, path, body):
                return {'ok': False, 'error': {'code': 403, 'message': 'Script operation refused'}}
            parsed = urlsplit(path)
            route = parsed.path
            query = dict(parse_qsl(parsed.query))
            if route == '/api/automation/events':
                svc = getattr(server.app.state, 'automation', None)
                if svc is None:
                    raise RuntimeError('unavailable')
                page = await svc.engine.io(svc.events.page, int(query.get('after', '0')), int(query.get('limit', '20')))
                return project_collection(profile, path, page)
            if route == '/api/list':
                return project_collection(profile, path, await server.api_list())
            if route == '/api/sessions':
                rows = []
                for sid in sorted(profile['sessions']):
                    session = await server._store_read(server._summary_session_get, sid)
                    if session is not None:
                        rows.append({'id': session.id, 'adapter': session.adapter})
                return {'sessions': rows}
            if route == '/api/sessions/wake':
                return await server.api_sessions_wake(body)
            sid = route.split('/')[3]
            def conditional_scope(target):
                return (target.id in profile['sessions'] and
                        (profile['targetMode'] != 'codex' or target.adapter == 'codex'))
            if route.endswith('/queue/state-token'):
                return await server.worker.conditional_queue_snapshot(sid, scope_guard=conditional_scope)
            if route.endswith('/queue/wake-receipt'):
                return await server.worker.conditional_wake_receipt(sid, query['clientMessageId'], scope_guard=conditional_scope)
            if route.endswith('/queue/conditional-wake'):
                return await server.worker.conditional_wake_session(sid, client_message_id=body['clientMessageId'],
                    expected_version=body['expectedVersion'], source='automation', scope_guard=conditional_scope)
            if route.endswith('/worker/delayed-restart'):
                if method == 'POST':
                    return await server.api_delayed_restart(sid, existing_worker_only=True)
                return await server.api_delayed_restart_state(sid)
            detail = await server.api_get_session(sid, historyLimit=1)
            if not isinstance(detail, dict) or detail.get('id') != sid:
                return {'error': 'Session not found'}
            if profile['targetMode'] == 'codex' and detail.get('adapter') != 'codex':
                return {'ok': False, 'error': {'code': 403, 'message': 'Script operation refused'}}
            keys = ('id', 'adapter', 'workerId', 'workerStatus', 'historyEpoch',
                    'historyRevision', 'queuePaused', 'illegalOffline')
            result = {key: detail[key] for key in keys if key in detail}
            last = detail.get('lastResult')
            if isinstance(last, dict):
                keys = ('status', 'terminalKey', 'workerId', 'generation', 'taskSeq',
                        'executionSeq', 'taskId', 'historyEpoch', 'historyRevision')
                result['lastResult'] = {key: last[key] for key in keys if key in last}
                diagnostic = last.get('reportDiagnostic')
                if isinstance(diagnostic, dict) and isinstance(diagnostic.get('runtimeIssue'), dict):
                    issue = diagnostic['runtimeIssue']
                    corr = _correlation(issue.get('correlation'), sid)
                    issue_id = issue.get('issueId')
                    if (corr is not None and issue['correlation'].get('sourceSessionId') == last.get('sourceSessionId')
                            and isinstance(issue_id, str) and 0 < len(issue_id) <= 256):
                        result['lastResult']['reportDiagnostic'] = {'runtimeIssue': {'issueId': issue_id, 'correlation': corr}}
            else:
                result['lastResult'] = None
            return result
        except Exception:
            # Never relay a credential, profile path, body or exception text.
            return {'ok': False, 'error': {'code': 403, 'message': 'Script access unavailable'}}

    runtime.script_http = call


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Initialize or revoke a local script profile')
    parser.add_argument('operation', choices=('init', 'revoke'))
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--policy', type=Path, help='JSON policy for init')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'init':
            if args.policy is None:
                parser.error('init requires --policy')
            initialize(args.data_dir, args.profile, _read_json(args.policy))
        else:
            if args.policy is not None:
                parser.error('revoke does not accept --policy')
            revoke(args.data_dir, args.profile)
    except Exception:
        print('Script profile operation failed; no credential was displayed.', file=sys.stderr)
        return 1
    print('Script profile operation completed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
