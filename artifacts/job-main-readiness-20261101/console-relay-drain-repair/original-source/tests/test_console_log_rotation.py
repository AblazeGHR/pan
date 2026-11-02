"""Bounded whole-file rotation for pan-console.out.log / pan-console.err.log.

These are real-Windows tests: rotation on Windows fails while another process
holds the active file without delete sharing, and that is exactly the boundary
the writer must degrade on instead of truncating live output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from packages.core import config as pan_config
from packages.core import console_logs
from packages.core import launcher
from packages.jobs.window import command_window_options

ROOT = Path(__file__).resolve().parent.parent
WRITER = ROOT / "tests" / "support" / "console_log_writer.py"
HOST = ROOT / "tests" / "support" / "console_log_host.py"

_HOLDER = """
import sys, time
handle = open(sys.argv[1], "rb")
print("ready", flush=True)
time.sleep(120)
"""


def _ordered_files(path: Path) -> list[Path]:
    """Backups oldest → newest, then the active file."""
    numbered = []
    for candidate in path.parent.glob(path.name + ".*"):
        suffix = candidate.name[len(path.name) + 1:]
        if suffix.isdigit():
            numbered.append((int(suffix), candidate))
    files = [candidate for _index, candidate in sorted(numbered, reverse=True)]
    if path.exists():
        files.append(path)
    return files


def _all_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for file in _ordered_files(path):
        lines.extend(file.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line]


def _env(**extra: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.update({key: str(value) for key, value in extra.items()})
    return env


def _writer_process(path: Path, tag: str, count: int, payload: int,
                    max_bytes: int, backups: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(WRITER), str(path), tag, str(count),
         str(payload), str(max_bytes), str(backups)],
        cwd=str(ROOT), env=_env(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **command_window_options(),
    )


def test_rotation_bounds_files_and_keeps_retained_lines_in_order(tmp_path):
    path = tmp_path / "data" / "logs" / "pan-console.out.log"
    writer = console_logs.RotatingByteLog(path, max_bytes=2048, backup_count=3)
    expected = [f"line-{index:04d}-{'y' * 40}" for index in range(200)]

    for line in expected:
        writer.write((line + "\n").encode("utf-8"))

    files = _ordered_files(path)
    assert len(files) == 4, [file.name for file in files]  # active + 3 backups
    assert all(file.stat().st_size <= 2048 + 64 for file in files)
    lines = _all_lines(path)
    assert lines == expected[-len(lines):]
    assert len(lines) == len(set(lines))
    status = writer.status()
    assert status["rotations"] >= 1
    assert status["degraded"] is False
    assert status["overLimit"] is False


def test_append_failure_is_observable_and_never_raises(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    writer = console_logs.RotatingByteLog(
        blocker / "pan-console.out.log", max_bytes=1024, backup_count=2)

    writer.write(b"hello\n")  # must not raise into the service

    status = writer.status()
    assert status["rotationFailures"] >= 1
    assert status["degraded"] is True
    assert status["lastError"]
    assert status["sizeBytes"] == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows rename-while-open semantics")
def test_file_held_elsewhere_degrades_then_rotates_after_release(tmp_path):
    path = tmp_path / "data" / "logs" / "pan-console.out.log"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"a" * 4096)
    writer = console_logs.RotatingByteLog(path, max_bytes=2048, backup_count=2)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **command_window_options())
    try:
        assert holder.stdout.readline().strip() == "ready"

        writer.write(b"during-hold\n")
        status = writer.status()
        assert status["degraded"] is True
        assert status["rotationFailures"] >= 1
        assert status["overLimit"] is True
        assert not (path.parent / (path.name + ".1")).exists()
        # The line was appended; the active file was never truncated.
        assert path.read_bytes().endswith(b"during-hold\n")
    finally:
        holder.kill()
        holder.wait(timeout=15)

    writer._retry_after = 0.0  # skip the degraded-retry throttle after release
    writer.write(b"after-release\n")

    assert (path.parent / (path.name + ".1")).exists()
    assert writer.status()["degraded"] is False
    assert writer.status()["rotations"] == 1
    assert path.read_bytes() == b"after-release\n"


def test_two_processes_share_rotation_without_lost_or_duplicated_lines(tmp_path):
    path = tmp_path / "data" / "logs" / "pan-console.out.log"
    path.parent.mkdir(parents=True)
    expected: list[str] = []
    processes = []
    for tag in ("pa", "pb"):
        processes.append(_writer_process(path, tag, 200, 60, 6144, 8))
        expected.extend(f"{tag}-{index:05d}-{'y' * 60}" for index in range(200))
    for process in processes:
        stdout, stderr = process.communicate(timeout=180)
        assert process.returncode == 0, f"writer failed: {stdout}\n{stderr}"

    assert (path.parent / (path.name + ".1")).exists(), "rotation never happened"
    lines = _all_lines(path)
    counts = Counter(lines)
    assert counts == Counter(expected)  # exactly once, no loss


def test_relay_forwards_own_and_child_output_and_rotates(tmp_path):
    out_path = tmp_path / "data" / "logs" / "pan-console.out.log"
    err_path = tmp_path / "data" / "logs" / "pan-console.err.log"
    out_path.parent.mkdir(parents=True)
    completed = subprocess.run(
        [sys.executable, str(HOST)],
        cwd=str(ROOT),
        env=_env(PAN_CONSOLE_OUT_LOG=str(out_path),
                 PAN_CONSOLE_ERR_LOG=str(err_path),
                 PAN_TEST_CONSOLE_MAX_BYTES="2048",
                 PAN_TEST_CONSOLE_BACKUPS="3",
                 PAN_TEST_CONSOLE_LINES="90",
                 PAN_TEST_CONSOLE_PAYLOAD="40"),
        capture_output=True, text=True, timeout=180, **command_window_options())

    assert completed.returncode == 0, completed.stdout + completed.stderr
    out_lines = _all_lines(out_path)
    status = _relay_status(out_lines)
    assert status["active"] is True
    assert status["logs"]["pan-console.out.log"]["rotations"] >= 1
    assert status["logs"]["pan-console.out.log"]["degraded"] is False
    assert any(line.startswith("host-00089-") for line in out_lines)
    assert "child-stdout-line" in out_lines
    assert (out_path.parent / (out_path.name + ".1")).exists()
    assert 2 <= len(_ordered_files(out_path)) <= 4
    assert all(file.stat().st_size <= 2048 + console_logs.RELAY_CHUNK_BYTES
               for file in _ordered_files(out_path))

    err_lines = _all_lines(err_path)
    assert "host-stderr-line" in err_lines
    # Nothing leaked past the relay into the test's pipes.
    assert "host-00000-" not in completed.stdout


def test_install_without_launcher_environment_is_a_noop():
    assert console_logs.install(environ={}) is False
    assert console_logs.status()["active"] is False


def test_default_config_bounds_console_and_session_logs():
    logging_cfg = pan_config.DEFAULT_CONFIG["logging"]
    assert logging_cfg["console_max_bytes"] == 20 * 1024 * 1024
    assert logging_cfg["console_backup_count"] == 3
    assert logging_cfg["session_error_max_bytes"] == 20 * 1024 * 1024
    assert logging_cfg["session_error_backup_count"] == 3


def _relay_status(lines: list[str]) -> dict:
    for line in reversed(lines):
        if line.startswith("relay-status="):
            return json.loads(line[len("relay-status="):])
    raise AssertionError(f"no relay-status line in {lines[-3:]}")


@pytest.fixture(scope="module")
def venv_redirector(tmp_path_factory) -> Path:
    """A real Windows venv ``python.exe`` (redirector stub), like production."""
    if os.name != "nt":
        pytest.skip("Windows venv redirector shape")
    target = tmp_path_factory.mktemp("venv") / "redirector"
    completed = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(target)],
        capture_output=True, text=True, timeout=180, **command_window_options())
    if completed.returncode != 0:
        pytest.skip(f"cannot create a venv redirector: {completed.stderr[-400:]}")
    python = target / "Scripts" / "python.exe"
    assert python.is_file(), python
    return python


def _write_oversized_console_log(path: Path, size: int) -> None:
    """Pre-fill the destination exactly like a long-running profile."""
    with path.open("ab", buffering=0) as handle:
        handle.write(b"a" * size)


@pytest.mark.parametrize("interpreter_kind", ["direct", "venv-redirector"])
def test_launcher_hands_console_logs_over_and_child_exits_without_residue(
        tmp_path, interpreter_kind, venv_redirector):
    """The production launch shape must rotate, not just the direct interpreter.

    A venv ``python.exe`` is a redirector stub that keeps every inherited handle
    for the child's whole lifetime, so the launcher must not hand it the console
    log files at all.  Both interpreters go through the same shim.
    """
    import psutil

    interpreter = sys.executable if interpreter_kind == "direct" else str(venv_redirector)
    root = tmp_path / "pan"
    logs = root / "data" / "logs"
    (root / "scripts").mkdir(parents=True)
    logs.mkdir(parents=True)
    (root / "main.py").write_text(HOST.read_text(encoding="utf-8"), encoding="utf-8")
    limit = pan_config.DEFAULT_CONFIG["logging"]["console_max_bytes"]
    _write_oversized_console_log(logs / console_logs.OUT_NAME, limit + 4096)

    old_env = {key: os.environ.get(key) for key in (
        "PYTHONPATH", "PAN_TEST_CONSOLE_LINES", "PAN_TEST_CONSOLE_PAYLOAD",
        "PAN_TEST_CONSOLE_SLEEP", "PAN_CONSOLE_OUT_LOG", "PAN_CONSOLE_ERR_LOG")}
    os.environ["PYTHONPATH"] = str(ROOT)
    os.environ["PAN_TEST_CONSOLE_LINES"] = "90"
    os.environ["PAN_TEST_CONSOLE_PAYLOAD"] = "40"
    os.environ["PAN_TEST_CONSOLE_SLEEP"] = "1.5"
    os.environ.pop("PAN_CONSOLE_OUT_LOG", None)
    os.environ.pop("PAN_CONSOLE_ERR_LOG", None)
    try:
        record = launcher._start_main(
            root, 19999, [interpreter], hidden=True,
            log_path=logs / "pan-launcher.log")
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    pid = int(record["pid"])
    assert pid > 0
    try:
        process = psutil.Process(pid)
        process.wait(timeout=60)
    except psutil.NoSuchProcess:
        pass
    assert not psutil.pid_exists(pid)

    out_path = logs / console_logs.OUT_NAME
    err_path = logs / console_logs.ERR_NAME
    rotated = logs / (out_path.name + ".1")
    assert out_path.exists() and err_path.exists()
    assert rotated.exists(), "the active console log was never rotated"
    assert rotated.stat().st_size >= limit, "the rotated file was truncated"
    active_lines = out_path.read_text(encoding="utf-8").splitlines()
    assert active_lines and active_lines[0].startswith("host-00000-")
    assert "child-stdout-line" in active_lines
    status = _relay_status(active_lines)
    assert status["active"] is True
    assert status["logs"]["pan-console.out.log"]["rotations"] >= 1
    assert status["logs"]["pan-console.out.log"]["degraded"] is False
    assert out_path.stat().st_size < 64 * 1024, "the active file is not bounded"
    assert len(_ordered_files(out_path)) <= 4
    assert "host-stderr-line" in _all_lines(err_path)
