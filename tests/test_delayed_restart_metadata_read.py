"""The Windows CRT can expose a sharing conflict as errno EACCES only."""
import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_delayed_restart_locked_queue_http as target
from tests import test_legal_offline_http as legal
from tests import test_source_aware_auto_report_real as source_report


def test_legal_snapshot_retries_transient_open_without_changing_state(monkeypatch, tmp_path):
    monkeypatch.setattr(target, 'os', SimpleNamespace(name='nt'))
    path = tmp_path / 'sessions' / 'original-id.json'
    path.parent.mkdir()
    path.write_text('{"illegal_batches":{"run-1":{"recovered":false}},"legal_offline":false}')
    original = Path.read_text
    calls = []

    def read(self, *args, **kwargs):
        calls.append(self)
        if len(calls) == 1:
            raise PermissionError(errno.EACCES, 'sharing conflict', str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    assert legal.durable(SimpleNamespace(data_root=tmp_path), 'original-id') == {
        'illegal_batches': {'run-1': {'recovered': False}}, 'legal_offline': False}
    assert calls == [path, path]


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


def test_auto_report_snapshot_retries_without_changing_admission(monkeypatch, tmp_path):
    monkeypatch.setattr(target, 'os', SimpleNamespace(name='nt'))
    path = tmp_path / 'sessions' / 'original-id.json'
    path.parent.mkdir()
    expected = {'terminal_results': [{'autoReportAdmission': {'enabled': False}}], 'queue_pending': []}
    import json
    path.write_text(json.dumps(expected))
    original = Path.read_text
    calls = []
    def read(self, *args, **kwargs):
        calls.append(self)
        if len(calls) == 1:
            raise PermissionError(errno.EACCES, 'sharing conflict', str(self))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    assert source_report._stored(tmp_path, 'original-id') == expected
    assert calls == [path, path]


def test_auto_report_snapshot_persistent_denial_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(target, 'os', SimpleNamespace(name='nt'))
    error = PermissionError(errno.EACCES, 'persistent sharing denial')
    def read(*args, **kwargs):
        raise error
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(PermissionError) as caught:
        source_report._stored(tmp_path, 'original-id')
    assert caught.value is error
