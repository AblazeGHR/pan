"""Unit tests for the multi-signal restore completion gate (bugfix3).

Regression cover for the 2026-09-30 incident: cbc 2.160.0 crashes with
CheckpointRestoreValidationError when a checkpoint file is outside the
workspace; the old single-signal gate waited out the whole timeout and
killed the PTY. The gate must fail fast on crashes, complete on file
change or the post-restore screen, and never blind-kill on timeout.
"""

from __future__ import annotations

import time

import pytest

from packages.core.rewind.driver import (
    _await_restore,
    _crash_error,
    _post_restore_screen,
    _snapshot_files,
)

CONFIRM_PAGE = 'Rewind\n Confirm you want to restore to this checkpoint:\n ❯ 1. Restore code and conversation\n   4. Never Mind'
POST_RESTORE = '> some earlier message\n> the anchor message text\n⏵⏵ bypass permissions on   ← for agents'
CRASH = ('Promise {<rejected> CheckpointRestoreValidationError: '
         'Checkpoint file destination is outside the workspace\n' + CONFIRM_PAGE)


class GateFakeSession:
    def __init__(self, screen: str, alive: bool = True):
        self._screen = screen
        self._alive = alive

    def alive(self) -> bool:
        return self._alive

    def text(self) -> str:
        return self._screen

    def wait_for(self, predicate, deadline):
        time.sleep(0.02)  # let the polling loop advance in real time
        return predicate(self._screen), self._screen


def test_crash_error_names_outside_workspace():
    message = _crash_error(CRASH)
    assert message is not None
    assert '工作区' in message


def test_crash_error_ignores_post_restore_conversation_text():
    # A restored conversation may legitimately contain crash-like strings.
    screen = POST_RESTORE + '\nwe discussed UnhandledPromiseRejection yesterday'
    assert _crash_error(screen) is not None  # signature is seen...
    session = GateFakeSession(screen)
    # ...but the post-restore screen means it is conversation content, not a
    # crash: the gate completes via the screen signal.
    verification, _ = _await_restore(session, timeout=1.0)
    assert verification == 'screen-settle'


def test_gate_fails_fast_on_crash():
    session = GateFakeSession(CRASH)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match='工作区'):
        _await_restore(session, timeout=30.0)
    assert time.monotonic() - started < 5.0  # fail fast, not after timeout


def test_gate_fails_fast_when_process_dies():
    session = GateFakeSession(CONFIRM_PAGE, alive=False)
    with pytest.raises(RuntimeError, match='意外退出'):
        _await_restore(session, timeout=30.0)


def test_gate_completes_on_watched_file_change(tmp_path):
    target = tmp_path / 'watched.txt'
    target.write_text('before', encoding='utf-8')
    baseline = _snapshot_files([target])
    target.write_text('after', encoding='utf-8')
    session = GateFakeSession(CONFIRM_PAGE)  # screen still on confirm page
    verification, checks = _await_restore(session, timeout=5.0, watch_baseline=baseline)
    assert verification == 'file-change'
    assert checks[str(target)]['changed'] is True


def test_gate_timeout_with_open_confirm_page_fails():
    session = GateFakeSession(CONFIRM_PAGE)
    with pytest.raises(RuntimeError, match='确认页'):
        _await_restore(session, timeout=0.3)


def test_gate_timeout_with_post_restore_screen_completes_unverified(tmp_path):
    target = tmp_path / 'same.txt'
    target.write_text('same', encoding='utf-8')
    baseline = _snapshot_files([target])  # content no-op restore: no change
    session = GateFakeSession(POST_RESTORE)
    verification, _ = _await_restore(session, timeout=0.3, watch_baseline=baseline)
    assert verification == 'unverified-timeout-screen'


def test_post_restore_screen_markers():
    assert _post_restore_screen(POST_RESTORE)
    assert not _post_restore_screen(CONFIRM_PAGE)
    assert not _post_restore_screen('Restore and fork the conversation to a checkpoint:')
