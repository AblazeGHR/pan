"""Bounded whole-file rotation for the session error samples (``session-errors.jsonl``).

The rotation must reuse the existing cross-process lock and FIFO writer: no
sample may be cut, no line may be truncated, and a rotation failure must not
stop the task that is reporting an error.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from packages.core import session_error_log
from packages.jobs.window import command_window_options

ROOT = Path(__file__).resolve().parent.parent
WRITER = ROOT / "tests" / "support" / "session_error_log_writer.py"

_HOLDER = """
import sys, time
handle = open(sys.argv[1], "rb")
print("ready", flush=True)
time.sleep(120)
"""


def _ordered_files(path: Path) -> list[Path]:
    numbered = []
    for candidate in path.parent.glob(path.name + ".*"):
        suffix = candidate.name[len(path.name) + 1:]
        if suffix.isdigit():
            numbered.append((int(suffix), candidate))
    files = [candidate for _index, candidate in sorted(numbered, reverse=True)]
    if path.exists():
        files.append(path)
    return files


def _records(path: Path) -> list[dict]:
    rows: list[dict] = []
    for file in _ordered_files(path):
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _record(index: int, tag: str = "w", chars: int = 120) -> str | None:
    return session_error_log.record_session_error(
        kind="terminal_error",
        error_text=f"{tag}-{index}-" + "z" * chars,
        error_text_source="pan.runtime",
        terminal=True,
        session_id=f"ses_{tag}",
        worker_id=f"w_{tag}",
        generation=1,
        task_seq=index,
        dedupe_token=f"{tag}-{index}",
    )


def test_bounded_rotation_keeps_every_sample_when_capacity_allows(tmp_path):
    path = tmp_path / "logs" / "session-errors.jsonl"
    path.parent.mkdir(parents=True)
    # 8 backups gives ~36 KiB of capacity for ~12 KiB of samples: rotation must
    # happen, and nothing may be pruned inside this window.
    session_error_log.configure(path, max_bytes=4096, backup_count=8)

    ids = [_record(index) for index in range(20)]

    assert all(ids), "every sample must be appended"
    assert (path.parent / (path.name + ".1")).exists(), "rotation never happened"
    rows = _records(path)
    assert [row["eventId"] for row in rows] == ids  # same order, once each
    assert Counter(row["eventId"] for row in rows) == Counter(ids)
    stats = session_error_log.stats()
    assert stats["rotated"] >= 1
    assert stats["rotationFailures"] == 0
    assert stats["written"] == 20
    assert all(file.stat().st_size <= 4096 + 2048 for file in _ordered_files(path))


@pytest.mark.skipif(os.name != "nt", reason="Windows rename-while-open semantics")
def test_rotation_failure_keeps_appending_without_dropping_samples(tmp_path):
    path = tmp_path / "logs" / "session-errors.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("x" * 4096 + "\n", encoding="utf-8")
    session_error_log.configure(path, max_bytes=2048, backup_count=2)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **command_window_options())
    try:
        assert holder.stdout.readline().strip() == "ready"
        first = _record(0, "held")
        second = _record(1, "held")
        assert first and second
        assert not (path.parent / (path.name + ".1")).exists()
        stats = session_error_log.stats()
        assert stats["rotationFailures"] >= 1
        assert stats["written"] >= 2
        text = path.read_text(encoding="utf-8")
        assert first in text and second in text  # appended, never truncated
    finally:
        holder.kill()
        holder.wait(timeout=15)


def test_two_processes_rotate_without_losing_or_duplicating_samples(tmp_path):
    path = tmp_path / "logs" / "session-errors.jsonl"
    path.parent.mkdir(parents=True)
    processes = []
    for index, tag in enumerate(("pa", "pb")):
        processes.append(subprocess.Popen(
            [sys.executable, str(WRITER), str(path), "write", tag, "40", "300",
             "", "8192", "6"],
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            **command_window_options(),
        ))
    for process in processes:
        stdout, stderr = process.communicate(timeout=180)
        assert process.returncode == 0, f"writer failed: {stdout}\n{stderr}"

    assert (path.parent / (path.name + ".1")).exists(), "rotation never happened"
    rows = _records(path)
    ids = [row["eventId"] for row in rows]
    assert len(ids) == len(set(ids)), "a sample was duplicated across files"
    assert len(ids) <= 80
    newest = {tag: max((row["taskSeq"] for row in rows if row["sessionId"] == f"ses_{tag}"),
                       default=-1) for tag in ("pa", "pb")}
    assert newest == {"pa": 39, "pb": 39}, newest
