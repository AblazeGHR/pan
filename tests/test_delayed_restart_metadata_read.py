"""The Windows CRT can expose a sharing conflict as errno EACCES only."""
import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_delayed_restart_locked_queue_http as target


def test_transient_eacces_without_winerror_keeps_exact_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(target, 'os', SimpleNamespace(name='nt'))
    path = tmp_path / 'owned-session.json'
    path.write_text('{"queue_delivery_ledger":{"original-id":{"deliveryState":"sent_to_cli"}}}')
    original = Path.read_text
    attempts = []

    def read(self, *args, **kwargs):
        attempts.append(self)
        if len(attempts) == 1:
            raise PermissionError(errno.EACCES, 'sharing conflict', str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    value = target._read_session_metadata(path)
    assert value == {'queue_delivery_ledger': {'original-id': {'deliveryState': 'sent_to_cli'}}}
    assert attempts == [path, path]


def test_permanent_access_denial_is_not_hidden(monkeypatch, tmp_path):
    monkeypatch.setattr(target, 'os', SimpleNamespace(name='nt'))
    attempts = []

    def read(self, *args, **kwargs):
        attempts.append(self)
        raise PermissionError(errno.EACCES, 'denied', str(self))

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(PermissionError):
        target._read_session_metadata(tmp_path / 'owned-session.json')
    assert len(attempts) <= 6
