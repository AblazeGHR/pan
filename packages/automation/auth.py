"""Local key -> process-memory 12-hour Bearer sessions (not an OS sandbox)."""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time
import threading
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
        self.sessions = {}
        self.lock = threading.RLock()

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
        return {'configured': True, 'revision': self._revision(key)}

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
