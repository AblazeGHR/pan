"""Owned-window guard: hides only owned input-desktop windows.

The guard is the mitigation for the recorded escape counterexample (an owned
process selecting the input desktop and showing a window). These tests prove the
two halves of the contract with a real window: an owned window is hidden, and an
unowned window is left alone. Guard latency is measured, never assumed zero.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from packages.jobs.window_guard import OwnedWindowGuard, descendant_pids

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows window/desktop API")

PROBE = Path(__file__).parent / "helpers" / "owned_escape_probe.py"


def _spawn_probe(tmp_path, hold_ms=1200):
    facts = tmp_path / "probe.json"
    process = subprocess.Popen([sys.executable, str(PROBE.resolve()), "--out", str(facts),
                                "--force", "--hold-ms", str(hold_ms)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=0x08000000)
    return process, facts


def test_guard_hides_owned_probe_window(tmp_path):
    process, facts = _spawn_probe(tmp_path)
    # The guard is started while the probe interpreter is still starting up, i.e.
    # before its window exists, exactly as the runner starts it before resume.
    guard = OwnedWindowGuard(lambda: {process.pid})
    assert guard.start()
    try:
        assert process.wait(timeout=20) == 0
    finally:
        guard.stop()
    record = json.loads(facts.read_text())
    assert record["escaped"] is True
    assert record["visibleAfterShow"] is True
    assert record["hiddenByGuard"] is True, record
    assert guard.records and guard.records[0]["pid"] == process.pid
    print("OWNED_GUARD " + json.dumps({"record": guard.records[0], "probeTotalMs": record["totalMs"]}))


def test_guard_ignores_unowned_probe_window(tmp_path):
    process, facts = _spawn_probe(tmp_path, hold_ms=700)
    guard = OwnedWindowGuard(lambda: frozenset())
    assert guard.start()
    try:
        assert process.wait(timeout=20) == 0
    finally:
        guard.stop()
    record = json.loads(facts.read_text())
    assert record["escaped"] is True
    assert record["visibleAfterShow"] is True
    # No collateral hiding: the window survived its whole hold, so the guard
    # never touched a window it does not own.
    assert record["hiddenByGuard"] is False, record
    assert guard.records == []
    assert record["totalMs"] >= 650, record["totalMs"]
    print("UNOWNED_GUARD " + json.dumps({"probeTotalMs": record["totalMs"],
                                         "foregroundStolen": record["foregroundStolen"]}))


def test_guard_sets_noactivate_before_the_first_show(tmp_path):
    process, facts = _spawn_probe(tmp_path)
    guard = OwnedWindowGuard(lambda: {process.pid}, pre_show_noactivate=True)
    assert guard.start()
    try:
        assert process.wait(timeout=20) == 0
    finally:
        guard.stop()
    creates = [row for row in guard.records if row["event"] == 0x8000]
    probe_creates = [row for row in creates if row["class"] == "PanOwnedVisibleProbe"]
    assert probe_creates, guard.records
    # Historical test name: this proves style application at callback delivery,
    # not that out-of-context delivery preceded the owning thread's first SHOW.
    assert all(row["noactivateApplied"] for row in probe_creates), probe_creates
    record = json.loads(facts.read_text())
    assert record["hiddenByGuard"] is True
    print("NOACTIVATE " + json.dumps({"creates": len(creates),
                                      "classes": sorted({row["class"] for row in creates})}))


def test_guard_without_noactivate_leaves_the_style_untouched(tmp_path):
    process, facts = _spawn_probe(tmp_path)
    guard = OwnedWindowGuard(lambda: {process.pid}, pre_show_noactivate=False)
    assert guard.start()
    try:
        assert process.wait(timeout=20) == 0
    finally:
        guard.stop()
    assert guard.records
    assert not any(row["noactivateApplied"] for row in guard.records)
    assert json.loads(facts.read_text())["hiddenByGuard"] is True


def test_descendant_pids_reports_the_owned_tree():
    own = descendant_pids(os.getpid())
    assert os.getpid() in own
    other = descendant_pids(0)
    assert other == ()


def test_guard_stop_is_idempotent_and_removes_the_hook():
    guard = OwnedWindowGuard(lambda: frozenset())
    assert guard.start()
    guard.stop()
    guard.stop()
    assert guard._thread is None
