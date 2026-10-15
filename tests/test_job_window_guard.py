"""Owned-window guard: hides only owned input-desktop windows.

The guard is the mitigation for the recorded escape counterexample (an owned
process selecting the input desktop and showing a window). These tests use an
explicit test desktop for windows and separately prove default input selection,
including from a private-desktop runner. They prove the
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
from packages.jobs.desktop import HiddenDesktop

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows window/desktop API")

PROBE = Path(__file__).parent / "helpers" / "owned_escape_probe.py"


@pytest.fixture
def probe_desktop():
    # Real windows and hooks share this test-owned desktop. They cannot be
    # hidden by a surrounding Job's input-desktop guard or reach the user.
    desktop = HiddenDesktop()
    desktop.processes = []
    try:
        yield desktop
    finally:
        from packages.core.terminal.identity import read_creation_filetime
        try:
            for process, filetime in desktop.processes:
                assert read_creation_filetime(int(process._handle)) == filetime
                if process.poll() is None:
                    process.kill()  # retained process handle, never a recycled PID
                process.wait(timeout=5)
                process._handle.Close()
        finally:
            desktop.close()


def _spawn_probe(tmp_path, desktop, hold_ms=1200):
    facts = tmp_path / "probe.json"
    # The stdlib-only probe must be the retained process itself; a Windows venv
    # redirector gives Popen the wrapper PID rather than the window owner's PID.
    process = subprocess.Popen([sys._base_executable, str(PROBE.resolve()), "--out", str(facts),
                                "--desktop", desktop.name, "--hold-ms", str(hold_ms)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=0x08000000)
    from packages.core.terminal.identity import read_creation_filetime
    desktop.processes.append((process, read_creation_filetime(int(process._handle))))
    return process, facts


def test_guard_hides_owned_probe_window(tmp_path, probe_desktop):
    process, facts = _spawn_probe(tmp_path, probe_desktop)
    # The guard is started while the probe interpreter is still starting up, i.e.
    # before its window exists, exactly as the runner starts it before resume.
    guard = OwnedWindowGuard(lambda: {process.pid}, desktop_name=probe_desktop.name)
    try:
        assert guard.start()
        assert process.wait(timeout=20) == 0
    finally:
        assert guard.stop()
    record = json.loads(facts.read_text())
    assert record["escaped"] is True
    assert record["visibleAfterShow"] is True
    assert record["hiddenByGuard"] is True, record
    assert guard.records and guard.records[0]["pid"] == process.pid
    print("OWNED_GUARD " + json.dumps({"record": guard.records[0], "probeTotalMs": record["totalMs"]}))


def test_guard_ignores_unowned_probe_window(tmp_path, probe_desktop):
    process, facts = _spawn_probe(tmp_path, probe_desktop, hold_ms=700)
    guard = OwnedWindowGuard(lambda: frozenset(), desktop_name=probe_desktop.name)
    try:
        assert guard.start()
        assert process.wait(timeout=20) == 0
    finally:
        assert guard.stop()
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


def test_guard_sets_noactivate_before_the_first_show(tmp_path, probe_desktop):
    process, facts = _spawn_probe(tmp_path, probe_desktop)
    guard = OwnedWindowGuard(lambda: {process.pid}, pre_show_noactivate=True, desktop_name=probe_desktop.name)
    try:
        assert guard.start()
        assert process.wait(timeout=20) == 0
    finally:
        assert guard.stop()
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


def test_guard_without_noactivate_leaves_the_style_untouched(tmp_path, probe_desktop):
    process, facts = _spawn_probe(tmp_path, probe_desktop)
    guard = OwnedWindowGuard(lambda: {process.pid}, pre_show_noactivate=False, desktop_name=probe_desktop.name)
    try:
        assert guard.start()
        assert process.wait(timeout=20) == 0
    finally:
        assert guard.stop()
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
    from tests.helpers.owned_escape_probe import user32, desktop_name
    try:
        assert guard.start()
        handle = user32.OpenInputDesktop(0, False, 0x41)
        assert handle
        try:
            assert guard.desktop_name == desktop_name(handle)
        finally:
            user32.CloseDesktop(handle)
    finally:
        assert guard.stop()
    guard.stop()
    assert guard._thread is None


def test_guard_selects_input_desktop_from_private_runner(tmp_path):
    from packages.jobs.desktop import DesktopCommand
    helper = PROBE.with_name('guard_desktop_probe.py')
    source = Path(__file__).resolve().parents[1] / 'packages/jobs/window_guard.py'
    facts = tmp_path / 'desktop.json'
    with (tmp_path / 'desktop.log').open('wb') as log:
        with DesktopCommand([sys._base_executable, str(helper), '--source', str(source),
                             '--out', str(facts)], str(tmp_path), log) as process:
            try:
                assert process.wait(timeout=20) == 0
            finally:
                if process.kernel.WaitForSingleObject(process.handle, 0) == 0x102:
                    from ctypes import wintypes as w
                    process.kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
                    assert process.kernel.TerminateProcess(process.handle, 1)
                    process.wait(timeout=5)
    record = json.loads(facts.read_text())
    assert record['callerDesktop'].startswith('PanJob_'), record
    assert record['callerDesktop'] != record['inputDesktop'], record
    assert record['hookDesktop'] == record['inputDesktop'], record
