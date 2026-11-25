"""Profile-local persistent hashed sessions for Cookie and Bearer clients."""
import math
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time
import threading
import json
from functools import wraps
from contextlib import contextmanager
from packages.core.platform.auth_lock import auth_lock, require_private_file


def locked(fn):
    @wraps(fn)
    def call(self, *args, **kwargs):
        with self.lock:
            with auth_lock(self.path.with_name('auth.lock')):
                return fn(self, *args, **kwargs)
    return call


def permission_write(fn):
    @wraps(fn)
    def call(self, *args, **kwargs):
        # Wait for group commits without occupying the session-state lock.
        with self.permissions_writer():
            return fn(self, *args, **kwargs)
    return call


class UserAuth:
    PASSWORD_PREFIX = 'pan-auth:scrypt-v1\n'
    lifetime = 12 * 60 * 60

    def __init__(self, root):
        self.path = Path(root) / 'user.key'
        self.permissions_path = Path(root) / 'authorization.json'
        self.sessions_path = Path(root) / 'sessions.json'
        self.sessions = {}
        self.lock = threading.RLock()
        self.login_failures = []
        if self.sessions_path.exists():
            self.session('', False)

    @contextmanager
    def permissions_writer(self):
        """Cross-instance/process grant-write fence, separate from sessions."""
        with auth_lock(self.path.with_name('permissions-write.lock')):
            yield

    @staticmethod
    def _atomic_json(path, state, prefix=''):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(8))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(prefix)
                json.dump(state, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            require_private_file(temporary)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _save_sessions(self, sessions=None):
        value = self.sessions if sessions is None else sessions
        self._atomic_json(self.sessions_path, {
            'keyRevision': self._revision(self.key()), 'sessions': value})
        self.sessions = value

    def _prune(self):
        live = {k: v for k, v in self.sessions.items() if v > time.time()}
        if live != self.sessions:
            self._save_sessions(live)

    @property
    def session_days(self):
        if not self.permissions_path.exists():
            return 0
        state = json.loads(self.permissions_path.read_text(encoding='utf-8'))
        # A malformed Agent permission root has no login-duration setting. Keep
        # user login available so permission failures remain separately diagnosable.
        value = state.get('sessionDays', 0) if isinstance(state, dict) else 0
        if type(value) is not int or value not in (0, 7, 30, 90):
            raise ValueError('Invalid stored sessionDays')
        return value

    @property
    def session_lifetime(self):
        return self.session_days * 86400 or self.lifetime

    @locked
    def change_session_days(self, value):
        if type(value) is not int or value not in (0, 7, 30, 90):
            raise ValueError('sessionDays must be 0, 7, 30 or 90')
        self.session('', False)
        state = self.permissions_status()
        state['sessionDays'] = value
        self._atomic_json(self.permissions_path, state)
        # Global setting applies to existing sessions as well as future logins.
        now = time.time()
        if self.path.exists():
            self._save_sessions({k: now + self.session_lifetime for k, v in self.sessions.items() if v > now})
        return {'sessionDays': value}

    @locked
    def session(self, token, renew=True):
        # Read the durable authority before accepting cached state. This also
        # observes revocation/password changes by another owned process and
        # fails closed when a previously working store becomes unreadable.
        if self.sessions_path.exists():
            stored = json.loads(self.sessions_path.read_text(encoding='utf-8'))
            sessions = stored['sessions']
            if (not isinstance(sessions, dict) or len(sessions) > 256 or any(
                    not isinstance(k, str) or len(k) != 64 or any(c not in '0123456789abcdef' for c in k)
                    or type(v) not in (int, float) or not math.isfinite(v)
                    for k, v in sessions.items())):
                raise ValueError('Invalid persisted sessions')
            self.sessions = sessions if stored['keyRevision'] == self._revision(self.key()) else {}
        else:
            self.sessions = {}
        self._prune()
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = self.sessions.get(digest, 0)
        renewed = False
        if expires and renew and self.session_days and expires - time.time() < self.session_lifetime / 2:
            expires = time.time() + self.session_lifetime
            self._save_sessions({**self.sessions, digest: expires})
            renewed = True
        return {'authenticated': bool(expires), 'expiresAt': expires or None,
                'sessionDays': self.session_days, 'renewed': renewed}

    @locked
    def revoke(self):
        self._save_sessions({})

    # Profile-wide capability matrix.  It is deliberately explicit and
    # fail-closed: a missing/unknown bit never grants a write or effect.
    PERMISSION_SCHEMA = {
        'trigger': ('read', 'create', 'updateConfig', 'lifecycle', 'delete'),
        'action': ('read', 'create', 'updateConfig', 'lifecycle', 'delete', 'invoke'),
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
            # Existing files retain their grants and revision; new invoke is denied.
            if kind == 'action' and isinstance(item, dict) and 'invoke' not in item:
                item = {**item, 'invoke': False}
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

    @permission_write
    @locked
    def change_permissions(self, permissions, expected_revision):
        current = self.permissions_status()
        if type(expected_revision) is not int or expected_revision != current['revision']:
            raise RuntimeError('Authorization permission revision conflict')
        value = self._validate_permissions(permissions)
        state = {'revision': current['revision'] + 1, 'permissions': value, 'sessionDays': self.session_days}
        temporary = self.permissions_path.with_name(self.permissions_path.name + '.tmp-' + secrets.token_hex(8))
        try:
            self.permissions_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(state, stream, ensure_ascii=False, separators=(',', ':'))
                stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            require_private_file(temporary)
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
        """Read a verifier/legacy record; never provision from an HTTP read."""
        if not self.path.exists():
            raise PermissionError('Administrator is not configured; initialize locally')
        key = self.path.read_text(encoding='utf-8').strip()
        if not key:
            raise ValueError('Invalid local user key')
        return key

    @staticmethod
    def _revision(key):
        if key.startswith(UserAuth.PASSWORD_PREFIX):
            return UserAuth._password_record(key)['revision']
        return hashlib.sha256(key.encode('utf-8')).hexdigest()

    @staticmethod
    def _password_record(value):
        record = json.loads(value.removeprefix(UserAuth.PASSWORD_PREFIX))
        if (set(record) != {'algorithm', 'salt', 'digest', 'revision'}
                or record['algorithm'] != 'scrypt-v1'
                or any(not isinstance(record[k], str) for k in record)
                or len(bytes.fromhex(record['salt'])) != 16
                or len(bytes.fromhex(record['digest'])) != 32
                or len(bytes.fromhex(record['revision'])) != 32):
            raise ValueError('Invalid password verifier')
        return record

    @staticmethod
    def _hash_password(password, revision=None):
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=5, dklen=32)
        return {'algorithm': 'scrypt-v1', 'salt': salt.hex(), 'digest': digest.hex(),
                'revision': revision or secrets.token_hex(32)}

    @classmethod
    def _verify_password(cls, password, stored):
        if not isinstance(password, str):
            return False
        if stored.startswith(cls.PASSWORD_PREFIX):
            record = cls._password_record(stored)
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record['salt']),
                                    n=16384, r=8, p=5, dklen=32)
            return hmac.compare_digest(actual.hex(), record['digest'])
        return hmac.compare_digest(password.encode(), stored.encode())

    @locked
    def initialize(self, password):
        """Explicit local-only provisioning; there is deliberately no HTTP route."""
        self.validate_password(password)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = self._hash_password(password)
        # Exclusive creation prevents two initializers from replacing an admin.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(self.PASSWORD_PREFIX)
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            require_private_file(self.path)
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise
        return {'configured': True, 'revision': record['revision']}

    @staticmethod
    def validate_password(password):
        # Simplified local policy: no length, character-class or charset
        # requirement. Only the storage/comparison invariants are kept,
        # because violating them would produce a password that cannot log in.
        if not isinstance(password, str) or not password:
            raise ValueError('Password must not be empty')
        # user.key is read back with str.strip(), so a padded password would
        # never compare equal at login.
        if password != password.strip():
            raise ValueError('Password must not start or end with whitespace')
        # A text-mode user.key rewrites line breaks, so a password holding a
        # control character would not survive the round trip on every platform.
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
            raise ValueError('Password must not contain control characters')

    @locked
    def authorization_status(self):
        key = self.key() if self.path.exists() else None
        permissions = self.permissions_status()
        return {'configured': key is not None, 'revision': self._revision(key) if key else None,
                'authorizationRevision': permissions['revision'],
                'permissions': permissions['permissions'], 'sessionDays': self.session_days}

    @locked
    def change_password(self, current_password, new_password, confirm_password, expected_revision=None):
        self.validate_password(new_password)
        if new_password != confirm_password:
            raise ValueError('New password confirmation does not match')
        current = self.key()
        revision = self._revision(current)
        if expected_revision is not None and expected_revision != revision:
            raise RuntimeError('Authorization revision conflict')
        if not self._verify_password(current_password, current):
            raise PermissionError('Current password is incorrect')
        if self._verify_password(new_password, current):
            raise ValueError('New password must differ from current password')
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = parent / (self.path.name + '.tmp-' + secrets.token_hex(8))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(self.PASSWORD_PREFIX)
                json.dump(self._hash_password(new_password), stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            require_private_file(temporary)
            os.replace(temporary, self.path)
            self.sessions.clear()
            self.revoke()
        except (OSError, UnicodeError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise OSError('Authorization password storage failed') from exc
        return {'configured': True, 'revision': self._revision(self.key()), 'revoked': True}

    @locked
    def login(self, key):
        now = time.monotonic()
        self.login_failures = [v for v in self.login_failures if now - v < 60]
        if len(self.login_failures) >= 10:
            raise ValueError('Login rate limit; retry later')
        self.session('', False)
        stored = self.key() if self.path.exists() else None
        if stored is None or not self._verify_password(key, stored):
            self.login_failures.append(now)
            raise PermissionError('Invalid user key')
        if not stored.startswith(self.PASSWORD_PREFIX):
            # Preserve live token identities below, but replace the old fast
            # password-derived revision with random material as well as hashing
            # the password. Otherwise the revision would defeat scrypt offline.
            self._atomic_json(self.path, self._hash_password(key), prefix=self.PASSWORD_PREFIX)
        if len(self.sessions) >= 256:
            raise ValueError('Too many user sessions')
        token = secrets.token_urlsafe(32)
        self._save_sessions({**self.sessions, hashlib.sha256(token.encode()).hexdigest(): time.time() + self.session_lifetime})
        return token

    @locked
    def authorized(self, header):
        scheme, _, token = header.partition(' ')
        return scheme.lower() == 'bearer' and self.session(token)['authenticated']

    @locked
    def logout(self, header):
        self.session('', False)
        digest = hashlib.sha256(header.partition(' ')[2].encode()).hexdigest()
        if digest in self.sessions:
            self._save_sessions({k: v for k, v in self.sessions.items() if k != digest})
