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
        if len(key) < 32:
            raise ValueError('Invalid local user key')
        return key

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
