"""Tests for user-facing external Agent CLI diagnostics."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import cli_diagnostics


class _FakeAdapter:
    name = "fake"

    def __init__(self, command):
        self._command = command

    def resolved_cli_argv(self):
        return self._command


def test_check_cli_adapter_reports_missing_component(monkeypatch):
    monkeypatch.setattr(cli_diagnostics, "get_adapter", lambda _name: _FakeAdapter(["missing-cli"]))
    monkeypatch.setattr(cli_diagnostics.shutil, "which", lambda _name: None)

    result = cli_diagnostics.check_cli_adapter("fake")

    assert result.available is False
    assert result.missing == ["missing-cli"]
    assert "PATH" in result.hint


def test_check_cli_adapter_accepts_existing_file(tmp_path, monkeypatch):
    executable = tmp_path / "fake-cli"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_diagnostics, "get_adapter", lambda _name: _FakeAdapter([str(executable)]))

    result = cli_diagnostics.check_cli_adapter("fake")

    assert result.available is True
    assert result.missing == []


def test_format_spawn_error_is_actionable(monkeypatch):
    monkeypatch.setattr(cli_diagnostics, "get_adapter", lambda _name: _FakeAdapter(["missing-cli"]))
    monkeypatch.setattr(cli_diagnostics.shutil, "which", lambda _name: None)

    message = cli_diagnostics.format_cli_spawn_error("fake", FileNotFoundError(2, "not found"))

    assert "无法启动 fake" in message
    assert "PATH" in message
    assert "重启 Pan" in message


def test_preflight_logs_missing_cli_without_raising(monkeypatch, caplog):
    monkeypatch.setattr(cli_diagnostics, "list_adapters", lambda: [_FakeAdapter(["missing-cli"])])
    monkeypatch.setattr(cli_diagnostics, "get_adapter", lambda _name: _FakeAdapter(["missing-cli"]))
    monkeypatch.setattr(cli_diagnostics.shutil, "which", lambda _name: None)

    with caplog.at_level(logging.WARNING):
        checks = cli_diagnostics.log_cli_preflight(logging.getLogger("test-cli-preflight"))

    assert len(checks) == 1
    assert checks[0].available is False
    assert "No supported Agent CLI" in caplog.text
