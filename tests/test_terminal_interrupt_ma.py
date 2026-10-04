"""Ctrl-C production gate: real Windows event, not releasing a blocked read."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.core.terminal import launcher


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Console API")
def test_launcher_enables_processing_before_spawning_any_child(monkeypatch):
    order = []
    monkeypatch.setattr(launcher, "_enable_child_console_interrupts", lambda: order.append("enable"))
    monkeypatch.setattr(launcher, "TerminalLauncher", lambda *_a, **_k: SimpleNamespace(
        run=lambda: order.append("run") or 0))
    assert launcher.main(["--terminal-id", "term_fixture", "--secret-file", "fixture.secret"],
                         initialize_console=True) == 0
    assert order == ["enable", "run"]


def test_interrupt_setup_failure_does_not_spawn(monkeypatch):
    calls = []
    def refuse():
        raise OSError("test-only interrupt setup failure")
    monkeypatch.setattr(launcher, "_enable_child_console_interrupts", refuse)
    monkeypatch.setattr(launcher, "TerminalLauncher", lambda *_a, **_k: SimpleNamespace(
        run=lambda: calls.append(1) or 0))
    assert launcher.main(["--terminal-id", "term_fixture", "--secret-file", "fixture.secret"],
                         initialize_console=True) == 5
    assert calls == []


def test_programmatic_main_does_not_mutate_borrowed_host_console(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "_enable_child_console_interrupts", lambda: calls.append("enable"))
    monkeypatch.setattr(launcher, "TerminalLauncher", lambda *_a, **_k: SimpleNamespace(run=lambda: 0))
    assert launcher.main(["--terminal-id", "term_fixture", "--secret-file", "fixture.secret"]) == 0
    assert calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="real ConPTY")
def test_real_production_ctrl_c_interrupts_foreground_and_preserves_shell():
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless/package.json").exists():
        pytest.skip("sidecar dependencies unavailable")
    path = repo / "audit/terminal/implementation/interrupt-ma/production_probe.py"
    spec = importlib.util.spec_from_file_location("terminal_interrupt_production_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run_probe()
    assert result["explicit_child_reset"] is False
    assert result["ctrl_c_delivered"] is True
    assert result["shell_still_usable"] is True
    assert result["same_runner_identity"] is True
    assert result["cleanup_status"] == "exited"
    assert result["secret_removed"] is True
