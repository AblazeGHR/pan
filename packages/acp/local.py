"""Dedicated Windows-only ACP pipe, with per-profile protected credentials.

The existing terminal pipe supplies a local-only owner DACL and bounded I/O.
Neither this pipe nor these credentials grant generic Pan API access.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading

from . import policy

MAX_FRAME = 256 * 1024


def _windows():
    if os.name != 'nt':
        raise OSError('ACP local transport is not supported on this platform')
    from packages.core.terminal import win_pipe, secret_store
    return win_pipe, secret_store


def _directory(root):
    return Path(root) / 'acp'


def _path(root, name):
    if not isinstance(name, str) or not policy.NAME.fullmatch(name):
        raise ValueError('invalid ACP profile name')
    return _directory(root) / (name + '.profile')


def _read_profile(root, name):
    win, secret = _windows()
    directory, path = _directory(root), _path(root, name)
    verifier = secret.SecretStore(directory)
    if win.is_reparse_point(str(directory)) or win.is_reparse_point(str(path)):
        raise PermissionError('ACP profile path is not private')
    verifier.verify_owner_only(directory)
    verifier.verify_owner_only(path)
    raw = secret.DpapiProtector(entropy=b'pan-acp-v1').unprotect(path.read_bytes())
    record = json.loads(raw)
    if not isinstance(record, dict) or set(record) != {'config', 'credential'}:
        raise ValueError('invalid ACP profile')
    credential = record['credential']
    if not isinstance(credential, str) or len(credential) != 64:
        raise ValueError('invalid ACP credential')
    return policy.validate(name, record['config']), credential


def initialize(root, name, template, workspace, mcp_names=()):
    win, secret = _windows()
    config = {'enabled': True, 'template': template, 'workspaceRoots': [str(workspace)]}
    if mcp_names:
        from packages.core.config import DEFAULT_PLUGIN_MANIFESTS
        from packages.core.manifest_loader import load_manifests
        from packages.plugins.catalog import Catalog
        config_path = Path(root) / 'config.json'
        host_config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        manifest = load_manifests(host_config.get('plugin_manifests', DEFAULT_PLUGIN_MANIFESTS))
        catalog = Catalog(manifest)
        config['stdioMcp'] = [{'name':item, 'sha256':policy.physical_mcp_pin(catalog, [item])}
                              for item in mcp_names]
    policy.validate(name, config)
    directory, path = _directory(root), _path(root, name)
    win.create_owner_only_directory(str(directory))
    verifier = secret.SecretStore(directory)
    if win.is_reparse_point(str(directory)):
        raise PermissionError('ACP profile directory is not private')
    verifier.verify_owner_only(directory)
    raw = json.dumps({'config': config, 'credential': secrets.token_hex(32)}).encode()
    blob = secret.DpapiProtector(entropy=b'pan-acp-v1').protect(raw)
    win.create_file_exclusive_owner_only(str(path), blob)
    verifier.verify_owner_only(path)


def _pipe_name(root):
    normalized = os.path.normcase(str(Path(root).resolve()))
    return 'acp-' + hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]


class StateStore:
    """Protected intent identities; never persists prompt or stream content."""
    def __init__(self, root):
        win, secret = _windows()
        self.win, self.secret = win, secret
        self.directory = _directory(root)
        self.verifier = secret.SecretStore(self.directory)
        if win.is_reparse_point(str(self.directory)):
            raise PermissionError('ACP state directory is not private')
        self.verifier.verify_owner_only(self.directory)
        self.protector = secret.DpapiProtector(entropy=b'pan-acp-state-v1')

    def path(self, handle):
        if not isinstance(handle, str) or not policy.NAME.fullmatch(handle) or len(handle) < 32:
            raise ValueError('invalid ACP handle')
        return self.directory / ('state-' + handle + '.bin')

    def save(self, handle, ctx):
        path = self.path(handle)
        if path.exists():
            if self.win.is_reparse_point(str(path)):
                raise PermissionError('ACP state is not private')
            self.verifier.verify_owner_only(path)
        turn = ctx.turn
        data = {'principal':ctx.principal, 'template':ctx.profile.template,
                'roots':[str(root) for root in ctx.profile.roots], 'cwd':ctx.cwd,
                'profileMcp':list(zip(ctx.profile.mcp_names, ctx.profile.mcp_pins)),
                'mcpNames':list(ctx.mcp_names), 'mcpDigest':ctx.mcp_digest,
                'sessionId':ctx.session_id,
                'turnId':turn.turn_id if turn else None,
                'queueId':turn.queue_id if turn else None,
                'cancelledBeforeAdmission':bool(turn and turn.cancelled and turn.queue_id is None),
                'lastTurnId':ctx.last_turn.turn_id if ctx.last_turn else None,
                'lastQueueId':ctx.last_turn.queue_id if ctx.last_turn else None,
                'lastCancelledBeforeAdmission':bool(ctx.last_turn and ctx.last_turn.cancelled and ctx.last_turn.queue_id is None),
                'completedTurnIds':ctx.completed_turns}
        blob = self.protector.protect(json.dumps(data).encode('utf-8'))
        self.verifier._write_blob_atomically(path, blob)

    def remove(self, handle):
        path = self.path(handle)
        if self.win.is_reparse_point(str(path)):
            raise PermissionError('ACP state is not private')
        self.verifier.verify_owner_only(path)
        path.unlink()

    def load(self, service, handle, principal):
        from .service import Context, Turn
        try:
            path = self.path(handle)
            if self.win.is_reparse_point(str(path)):
                return None
            self.verifier.verify_owner_only(path)
            data = json.loads(self.protector.unprotect(path.read_bytes()))
            if data['principal'] != principal:
                return None
            profile = service.profile_lookup(principal)
            if (profile is None or profile.template != data['template']
                    or [str(root) for root in profile.roots] != data['roots']
                    or [list(pair) for pair in zip(profile.mcp_names, profile.mcp_pins)] != data.get('profileMcp', [])):
                return None
            cwd = str(policy.resolve_workspace(profile, data['cwd']))
            sid, turn_id, qid = data['sessionId'], data['turnId'], data['queueId']
            if sid is not None and (not isinstance(sid, str) or not sid): return None
            if turn_id is not None and (not isinstance(turn_id, str) or not turn_id): return None
            if qid is not None and (not isinstance(qid, str) or not qid): return None
            if len(service.contexts) >= 128 or sid in service.by_session: return None
            ctx = Context(principal, profile, cwd, sid)
            ctx.restored = True
            names, digest = data.get('mcpNames', []), data.get('mcpDigest', '')
            if (not isinstance(names, list) or any(name not in profile.mcp_names for name in names)
                    or len(names) != len(set(names)) or not isinstance(digest, str)
                    or (names and (len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)))
                    or (not names and digest)):
                return None
            ctx.mcp_names, ctx.mcp_digest = tuple(names), digest
            completed = data.get('completedTurnIds', [])
            if (not isinstance(completed, list) or len(completed) > 1024
                    or any(not isinstance(item, str) or not item for item in completed)
                    or len(set(completed)) != len(completed)):
                return None
            ctx.completed_turns = completed
            if turn_id is not None:
                cancelled = data.get('cancelledBeforeAdmission') is True and qid is None
                ctx.turn = Turn(turn_id, queue_id=qid, error=not cancelled, cancelled=cancelled)
            last_id, last_queue = data.get('lastTurnId'), data.get('lastQueueId')
            if last_id is not None:
                if (not isinstance(last_id, str) or not last_id
                        or (last_queue is not None and (not isinstance(last_queue, str) or not last_queue))): return None
                cancelled = data.get('lastCancelledBeforeAdmission') is True and last_queue is None
                ctx.last_turn = Turn(last_id, queue_id=last_queue, error=not cancelled, cancelled=cancelled)
            service.contexts[handle] = ctx
            if sid: service.by_session[sid] = ctx
            return ctx
        except Exception:
            return None


def _read_frame(conn):
    data = bytearray()
    while len(data) <= MAX_FRAME:
        chunk = conn.recv_bytes(min(65536, MAX_FRAME + 1 - len(data)), timeout=10)
        if not chunk:
            raise OSError('incomplete ACP frame')
        data.extend(chunk)
        if b'\n' in chunk:
            if data.count(b'\n') != 1 or not data.endswith(b'\n'):
                raise ValueError('invalid ACP frame')
            message = json.loads(data.decode('utf-8'))
            if not isinstance(message, dict):
                raise ValueError('invalid ACP frame')
            return message
    raise ValueError('ACP frame too large')


def _write_frame(conn, message):
    data = (json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    if len(data) > MAX_FRAME:
        raise ValueError('ACP frame too large')
    conn.send_raw(data, timeout=10)


class Listener:
    def __init__(self, root, server):
        win, _ = _windows()
        self.root, self.server = Path(root), server
        self.pipe = win.PipeServer(_pipe_name(root), max_instances=2, max_active_connections=2)
        self.stop_event = threading.Event()
        self.loop = asyncio.get_running_loop()
        from .pan_service import bind
        self.store = StateStore(root)
        self.service = bind(server, self.lookup, self.store.save, self.store.remove, self.store.load)
        self.thread = None

    def lookup(self, name):
        try:
            return _read_profile(self.root, name)[0]
        except (OSError, ValueError, PermissionError):
            return None

    async def dispatch(self, message):
        try:
            name, token = message.get('profile'), message.get('credential')
            profile, expected = _read_profile(self.root, name)
            if not isinstance(token, str) or not hmac.compare_digest(token, expected):
                raise PermissionError('authentication failed')
            operation, args = message.get('operation'), message.get('args')
            if not isinstance(args, dict):
                raise ValueError('invalid operation')
            s = self.service
            if operation == 'prepare' and set(args) == {'cwd'}:
                result = {'handle': s.prepare(name, args['cwd'])}
            elif operation == 'prepare' and set(args) == {'cwd', 'servers'}:
                result = {'handle': s.prepare(name, args['cwd'], args['servers'])}
            elif operation == 'submit' and set(args) == {'handle', 'turnId', 'text'}:
                await s.submit(name, args['handle'], args['turnId'], args['text'])
                result = {}
            elif operation == 'submit' and set(args) == {'handle', 'turnId', 'blocks'}:
                await s.submit(name, args['handle'], args['turnId'], args['blocks'])
                result = {}
            elif operation == 'poll' and set(args) == {'handle', 'turnId', 'cursor'}:
                result = s.poll(name, args['handle'], args['turnId'], args['cursor'])
            elif operation == 'finish' and set(args) == {'handle', 'turnId', 'cursor'}:
                s.finish(name, args['handle'], args['turnId'], args['cursor'])
                result = {}
            elif operation == 'cancel' and set(args) == {'handle', 'turnId'}:
                await s.cancel(name, args['handle'], args['turnId'])
                result = {}
            elif operation == 'permission' and set(args) == {'handle', 'turnId', 'permissionId', 'decision'}:
                await s.respond_permission(name, args['handle'], args['turnId'], args['permissionId'], args['decision'])
                result = {}
            elif operation == 'release' and set(args) == {'handle'}:
                s.release(name, args['handle'])
                result = {}
            elif operation == 'abandon' and set(args) == {'handle'}:
                s.abandon(name, args['handle'])
                result = {}
            elif operation == 'recover' and set(args) == {'handle', 'turnId'}:
                result = s.recover(name, args['handle'], args['turnId'])
            else:
                raise ValueError('invalid operation')
            return {'ok': True, 'result': result}
        except Exception:
            return {'ok': False, 'error': 'ACP request rejected or outcome unknown'}

    def start(self):
        self.pipe.create()
        self.server.app.state.acp_service = self.service
        self.thread = threading.Thread(target=self._serve, name='pan-acp-ipc', daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop_event.is_set():
            try:
                conn = self.pipe.accept(timeout=.2)
                if conn is None:
                    continue
                try:
                    message = _read_frame(conn)
                    future = asyncio.run_coroutine_threadsafe(self.dispatch(message), self.loop)
                    _write_frame(conn, future.result(timeout=30))
                finally:
                    conn.close()
            except Exception:
                # Do not log raw frames, credentials, or user prompts.
                continue

    def close(self):
        self.stop_event.set()
        self.pipe.close()
        if self.thread:
            self.thread.join(timeout=3)
        self.server.app.state.acp_service = None


def request(root, name, operation, args):
    win, _ = _windows()
    _, token = _read_profile(root, name)
    try:
        conn = win.PipeClient(_pipe_name(root)).connect()
        try:
            _write_frame(conn, {'profile': name, 'credential': token, 'operation': operation, 'args': args})
            response = _read_frame(conn)
        finally:
            conn.close()
    except win.PipeError as exc:
        raise OSError('ACP transport unavailable') from exc
    if response.get('ok') is not True or not isinstance(response.get('result'), dict):
        raise RuntimeError('ACP request rejected or outcome unknown')
    return response['result']
