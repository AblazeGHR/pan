"""File IPC faults must delay observation, never manufacture lifecycle proof."""
import json
from pathlib import Path

import pytest

from tests.helpers.canonical_creation_edge import read_gate


@pytest.mark.parametrize('not_published', [False, True])
def test_gate_retries_transient_visibility_and_read_denial(tmp_path, monkeypatch, not_published):
    gate = tmp_path / 'gate.json'
    expected = {'runnerPid': 123, 'runnerFiletime': 456}
    gate.write_text(json.dumps(expected))
    original = Path.read_text
    errors = ([FileNotFoundError('not published')] if not_published else []) + [PermissionError(13, 'publication read denied')]
    expected_attempts = len(errors) + 1
    attempts = []

    def read(path, *args, **kwargs):
        if path == gate:
            attempts.append(path)
            if errors:
                raise errors.pop(0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    assert read_gate(gate) == expected
    assert len(attempts) == expected_attempts


def test_gate_permanent_denial_fails_at_deadline(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(13, 'permanent read denied')
    monkeypatch.setattr(Path, 'read_text', denied)
    with pytest.raises(PermissionError, match='permanent read denied'):
        read_gate(tmp_path / 'gate.json', timeout=.05)


def test_gate_does_not_accept_invalid_json(tmp_path):
    gate = tmp_path / 'gate.json'
    gate.write_text('{')
    with pytest.raises(json.JSONDecodeError):
        read_gate(gate)
