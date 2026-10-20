"""Real multi-process coverage for the append-only error sample log."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from packages.jobs.window import command_window_options


ROOT = Path(__file__).resolve().parent.parent
WRITER = Path(__file__).resolve().parent / "support" / "session_error_log_writer.py"


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


def _run(args, *, timeout=90):
    return subprocess.run(
        [sys.executable, str(WRITER), *args],
        cwd=str(ROOT), env=_env(), capture_output=True, text=True,
        timeout=timeout, **command_window_options(),
    )


def _wait_for(predicate, *, timeout=20.0, label="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def test_concurrent_processes_write_valid_large_records(tmp_path):
    log_path = tmp_path / "logs" / "session-errors.jsonl"
    log_path.parent.mkdir(parents=True)
    processes = []
    for index in range(4):
        processes.append(subprocess.Popen(
            [sys.executable, str(WRITER), str(log_path), "write",
             f"p{index}", "8", "40000"],
            cwd=str(ROOT), env=_env(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, **command_window_options(),
        ))
    stderrs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        stderrs.append(stderr)
        assert process.returncode == 0, f"writer failed: {stdout}\n{stderr}"

    raw = log_path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == 32, f"writer stats: {stderrs}"
    for stderr in stderrs:
        stats = json.loads([line for line in stderr.splitlines() if line.strip()][-1])
        assert stats["written"] == 8, stats
        assert stats["failures"] == 0, stats
        assert stats["lockFailures"] == 0, stats
        assert stats["pending"] == 0, stats
    assert len({row["eventId"] for row in rows}) == 32
    assert {row["sessionName"] for row in rows} == {"p0", "p1", "p2", "p3"}
    for row in rows:
        text = row["errorText"]
        assert len(text) == 40000 + len("|p0|0|W")
        assert text.startswith("中" * 100)
        assert text.endswith("|W")
    # Every line is a standalone JSON object: no interleaved bytes.
    for line in raw.splitlines():
        assert line.startswith('{"schemaVersion":1,"eventId":"se_')


def test_process_cache_cannot_hide_another_process_half_line(tmp_path):
    """A long-lived writer must re-check the tail under the lock.

    Process A writes a complete record (and, in the old implementation, cached
    "file is newline terminated").  Process B then crashes after a half line.
    A's second write must repair the tail instead of appending onto it.
    """
    log_path = tmp_path / "logs" / "session-errors.jsonl"
    log_path.parent.mkdir(parents=True)
    gate = tmp_path / "release-a"
    writer_a = subprocess.Popen(
        [sys.executable, str(WRITER), str(log_path), "gated",
         "longlived", "1", "0", str(gate)],
        cwd=str(ROOT), env=_env(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **command_window_options(),
    )
    try:
        _wait_for(
            lambda: log_path.exists() and "A1" in log_path.read_text(
                encoding="utf-8", errors="replace"),
            label="first gated record",
        )
        partial = _run([str(log_path), "partial", "crasher"])
        assert partial.returncode == 0, partial.stderr
        assert log_path.read_text(encoding="utf-8").endswith('{"partial":')

        gate.touch()
        stdout, stderr = writer_a.communicate(timeout=30)
        assert writer_a.returncode == 0, f"{stdout}\n{stderr}"
    finally:
        if writer_a.poll() is None:
            writer_a.kill()
            writer_a.wait(timeout=5)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, lines
    assert json.loads(lines[0])["errorText"].endswith("|A1")
    assert lines[1] == '{"partial":'
    second = json.loads(lines[2])
    assert second["errorText"].endswith("|A2")
    assert second["schemaVersion"] == 1
