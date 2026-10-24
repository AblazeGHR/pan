"""Local key -> process-memory 12-hour Bearer sessions (not an OS sandbox)."""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time
import threading
import json
from functools import wraps


def locked(fn):
    @wraps(fn)
    def call(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return call


class UserAuth:
    lifetime = 12 * 60 * 60

    def __init__(self, root):
        self.path = Path(root) / 'user.key'
        self.permissions_path = Path(root) / 'authorization.json'
        self.sessions = {}
        self.lock = threading.RLock()

    # Profile-wide capability matrix.  It is deliberately explicit and
    # fail-closed: a missing/unknown bit never grants a write or effect.
    PERMISSION_SCHEMA = {
        'trigger': ('read', 'create', 'updateConfig', 'lifecycle', 'delete'),
        'action': ('read', 'create', 'updateConfig', 'lifecycle', 'delete'),
        'jobTemplate': ('read', 'create', 'updateConfig', 'use', 'delete'),
        'job': ('read', 'create', 'updateConfig', 'lifecycle', 'execute', 'delete'),
    }
    DEFAULT_PERMISSIONS = {kind: {name: False for name in names}
                           for kind, names in PERMISSION_SCHEMA.items()}
    DEFAULT_PERMISSIONS['action']['manageSystemActions'] = False

    @classmethod
    def _default_permissions(cls):
        return json.loads(json.dumps(cls.DEFAULT_PERMISSIONS))

    @classmethod
    def _validate_permissions(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.PERMISSION_SCHEMA):
            raise ValueError('Authorization permissions must contain trigger, action, jobTemplate and job')
        result = {}
        for kind, names in cls.PERMISSION_SCHEMA.items():
            item = value[kind]
            expected = set(names) | ({'manageSystemActions'} if kind == 'action' else set())
            if not isinstance(item, dict) or set(item) != expected or any(type(item[k]) is not bool for k in expected):
                raise ValueError('Invalid authorization permission fields for ' + kind)
            result[kind] = {key: item[key] for key in sorted(expected)}
        return result

    @locked
    def permissions_status(self):
        if not self.permissions_path.exists():
            return {'revision': 0, 'permissions': self._default_permissions()}
        try:
            state = json.loads(self.permissions_path.read_text(encoding='utf-8'))
            if not isinstance(state, dict):
                raise ValueError('Authorization state must be an object')
            revision = state.get('revision', 0)
            permissions = self._validate_permissions(state.get('permissions'))
            if type(revision) is not int or revision < 0:
                raise ValueError
            return {'revision': revision, 'permissions': permissions}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise OSError('Authorization state is invalid or unreadable') from exc

    @locked
    def change_permissions(self, permissions, expected_revision):
        current = self.permissions_status()
        if type(expected_revision) is not int or expected_revision != current['revision']:
            raise RuntimeError('Authorization permission revision conflict')
        value = self._validate_permissions(permissions)
        state = {'revision': current['revision'] + 1, 'permissions': value}
        temporary = self.permissions_path.with_name(self.permissions_path.name + '.tmp-' + secrets.token_hex(8))
        try:
            self.permissions_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(state, stream, ensure_ascii=False, separators=(',', ':'))
                stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.permissions_path)
        except (OSError, UnicodeError) as exc:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass
            raise OSError('Authorization state storage failed') from exc
        return {'revision': state['revision'], 'permissions': value}

    @locked
    def permission_allowed(self, kind, capability):
        state = self.permissions_status()
        return bool(state['permissions'].get(kind, {}).get(capability, False)), state['revision']

    @locked
    def key(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(secrets.token_urlsafe(32))
                stream.flush()
                os.fsync(stream.fileno())
        key = self.path.read_text(encoding='ascii').strip()
        if len(key) < 16:
            raise ValueError('Invalid local user key')
        return key

    @staticmethod
    def _revision(key):
        return hashlib.sha256(key.encode('utf-8')).hexdigest()

    @staticmethod
    def validate_password(password):
        if not isinstance(password, str) or not (16 <= len(password) <= 128):
            raise ValueError('Password must be 16-128 characters')
        if any(ord(ch) < 33 or ord(ch) > 126 for ch in password):
            raise ValueError('Password must use printable ASCII characters without spaces')
        classes = sum(bool(group) for group in (
            any(ch.islower() for ch in password),
            any(ch.isupper() for ch in password),
            any(ch.isdigit() for ch in password),
            any(not ch.isalnum() for ch in password),
        ))
        if classes < 3:
            raise ValueError('Password must include at least 3 of lowercase, uppercase, digit, and symbol')

    @locked
    def authorization_status(self):
        key = self.key()
        permissions = self.permissions_status()
        return {'configured': True, 'revision': self._revision(key),
                'authorizationRevision': permissions['revision'],
                'permissions': permissions['permissions']}

    @locked
    def change_password(self, current_password, new_password, confirm_password, expected_revision=None):
        self.validate_password(new_password)
        if new_password != confirm_password:
            raise ValueError('New password confirmation does not match')
        current = self.key()
        revision = self._revision(current)
        if expected_revision is not None and expected_revision != revision:
            raise RuntimeError('Authorization revision conflict')
        if not isinstance(current_password, str) or not hmac.compare_digest(current_password.encode(), current.encode()):
            raise PermissionError('Current password is incorrect')
        if hmac.compare_digest(current.encode(), new_password.encode()):
            raise ValueError('New password must differ from current password')
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = parent / (self.path.name + '.tmp-' + secrets.token_hex(8))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(new_password)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            self.sessions.clear()
        except (OSError, UnicodeError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise OSError('Authorization password storage failed') from exc
        return {'configured': True, 'revision': self._revision(new_password), 'revoked': True}

    @locked
    def login(self, key):
        if not isinstance(key, str) or not hmac.compare_digest(key.encode(), self.key().encode()):
            raise PermissionError('Invalid user key')
        current = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if v > current}
        if len(self.sessions) >= 256:
            raise ValueError('Too many user sessions')
        token = secrets.token_urlsafe(32)
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = current + self.lifetime
        return token

    @locked
    def authorized(self, header):
        scheme, _, token = header.partition(' ')
        return scheme.lower() == 'bearer' and self.sessions.get(hashlib.sha256(token.encode()).hexdigest(), 0) > time.monotonic()

    @locked
    def logout(self, header):
        self.sessions.pop(hashlib.sha256(header.partition(' ')[2].encode()).hexdigest(), None)
