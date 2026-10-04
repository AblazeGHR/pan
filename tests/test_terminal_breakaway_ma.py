"""Launcher requests independence without weakening the PTY tree guard."""
from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from packages.core.terminal.service import TerminalService

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows creation flags")


def _spawn(service):
    return service._spawn_production_launcher(
        "term_breakaway_ma", secret_file=service.root / "secret",
        rows=24, cols=80, cwd=None, shell_argv=None,
    )


def test_launcher_requests_breakaway_and_closes_parent_log(tmp_path, monkeypatch):
    service = TerminalService(tmp_path, python_argv=["python"])
    calls = []
    owner = SimpleNamespace(pid=42)

    def popen(argv, **kwargs):
        assert not kwargs["stderr"].closed
        calls.append((argv, dict(kwargs)))
        return owner

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert _spawn(service) is owner
    assert len(calls) == 1
    assert calls[0][1]["creationflags"] == 0x09000000
    assert calls[0][1]["stderr"].closed


def test_job_rejection_falls_back_without_claiming_durability(tmp_path, monkeypatch):
    service = TerminalService(tmp_path, python_argv=["python"])
    calls = []
    owner = SimpleNamespace(pid=42)

    def popen(argv, **kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            exc = OSError("local injected access denial")
            exc.winerror = 5
            raise exc
        return owner

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert _spawn(service) is owner
    assert [c["creationflags"] for c in calls] == [0x09000000, 0x08000000]
    assert calls[0]["stderr"] is calls[1]["stderr"]
    assert calls[1]["stderr"].closed
    assert ("launcher-breakaway-denied", "ambient-job-restricted") in service._events


@pytest.mark.parametrize("first_code,second_code", [(2, None), (5, 5)])
def test_unrelated_or_repeated_spawn_error_propagates_and_closes_log(
    tmp_path, monkeypatch, first_code, second_code,
):
    service = TerminalService(tmp_path, python_argv=["python"])
    streams = []
    codes = [first_code] + ([second_code] if second_code is not None else [])

    def popen(argv, **kwargs):
        streams.append(kwargs["stderr"])
        exc = OSError("injected")
        exc.winerror = codes[len(streams) - 1]
        raise exc

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(OSError):
        _spawn(service)
    assert len(streams) == len(codes)
    assert all(s.closed for s in streams)
