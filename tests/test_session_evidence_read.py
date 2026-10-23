"""Disk evidence reads must survive only brief Windows replacement sharing locks."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import test_delayed_restart_locked_queue_http as delayed


@pytest.fixture(autouse=True)
def windows_reader(monkeypatch):
    monkeypatch.setattr(delayed, "os", SimpleNamespace(name="nt"))


def denied(winerror):
    error = PermissionError(13, "sharing violation")
    error.winerror = winerror
    return error


@pytest.mark.parametrize("winerror", [None, 5, 32, 33])
def test_session_evidence_read_survives_short_replace_lock(monkeypatch, winerror):
    path = Mock()
    path.read_text.side_effect = [denied(winerror), '{"deliveryState":"sent_to_cli"}']
    waits = []
    monkeypatch.setattr(delayed.time, "sleep", waits.append)
    assert delayed._read_session_metadata(path) == {"deliveryState": "sent_to_cli"}
    assert path.read_text.call_count == 2
    assert waits == [.01]


@pytest.mark.parametrize("winerror", [123])
def test_session_evidence_read_preserves_other_permission_errors(monkeypatch, winerror):
    path = Mock()
    error = denied(winerror)
    path.read_text.side_effect = error
    with pytest.raises(PermissionError) as caught:
        delayed._read_session_metadata(path)
    assert caught.value is error
    assert path.read_text.call_count == 1


def test_session_evidence_read_preserves_invalid_json():
    path = Mock()
    path.read_text.return_value = "{"
    with pytest.raises(json.JSONDecodeError):
        delayed._read_session_metadata(path)
    assert path.read_text.call_count == 1


def test_session_evidence_read_retains_persistent_lock_failure(monkeypatch):
    path = Mock()
    error = denied(32)
    path.read_text.side_effect = error
    waits = []
    monkeypatch.setattr(delayed.time, "sleep", waits.append)
    with pytest.raises(PermissionError) as caught:
        delayed._read_session_metadata(path)
    assert caught.value is error
    assert path.read_text.call_count == 6
    assert waits == [.01, .02, .03, .04, .05]


def test_session_evidence_read_does_not_retry_on_other_platforms(monkeypatch):
    monkeypatch.setattr(delayed, "os", SimpleNamespace(name="posix"))
    path = Mock()
    error = denied(32)
    path.read_text.side_effect = error
    with pytest.raises(PermissionError) as caught:
        delayed._read_session_metadata(path)
    assert caught.value is error
    assert path.read_text.call_count == 1
