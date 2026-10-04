"""CBC menu automation on a restricted public AutomationContext.

No process, output queue, PTY ownership or teardown primitives live here.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from packages.core.rewind.filetools import compute_add_dirs
from packages.core.terminal.driver import AutomationContext


class AutomationSession:
    """Compatibility-shaped menu surface, not a process owner."""

    def __init__(self, context: AutomationContext):
        self.context = context

    def text(self) -> str:
        return self.context.screen_text()

    def alive(self) -> bool:
        return not self.context.runtime.exit.process_exit_seen

    def send(self, value: str) -> None:
        sent = self.context.send_text(value)
        if sent != len(value.encode("utf-8", "surrogatepass")):
            raise RuntimeError("terminal input was only partially accepted")

    def wait_for(self, predicate: Callable[[str], bool], deadline: float) -> tuple[bool, str]:
        # The old consumer drained until a new chunk or deadline even when
        # the predicate already matched. Keep that settling interval without
        # owning a queue or spinning on a continuously available snapshot.
        current = self.text()
        if predicate(current):
            initial = current
            before = getattr(self.context.observer, "observed_bytes", None)
            while time.monotonic() < deadline:
                current = self.text()
                after = getattr(self.context.observer, "observed_bytes", None)
                if after != before or (before is None and current != initial):
                    return predicate(current), current
                if not self.alive():
                    break
                time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
            current = self.text()
            return predicate(current), current
        outcome = self.context.wait_for(predicate, max(0.0, deadline - time.monotonic()))
        current = self.text()
        return bool(outcome.matched), current


class RewindStage(str, Enum):
    STARTING = "starting"
    RESUMING = "resuming"
    REWIND_MENU = "rewind-menu"
    RESTORING = "restoring"
    COMPLETED = "completed"
    FAILED = "failed"


# cbc rewind confirmation page options (menu highlights item 1 by default).
# 1 = restore code and conversation, 2 = conversation only, 3 = code only.
REWIND_SCOPE_LABELS = {
    1: "Restore code and conversation",
    2: "Restore conversation",
    3: "Restore code",
}


def coerce_rewind_scope(scope: Any) -> int:
    """Validate a rewind scope value; returns 1/2/3 or raises ValueError."""
    if isinstance(scope, bool):
        raise ValueError(f"invalid rewind scope: {scope!r} (expected 1, 2 or 3)")
    try:
        value = int(str(scope).strip())
    except (TypeError, ValueError):
        raise ValueError(f"invalid rewind scope: {scope!r} (expected 1, 2 or 3)") from None
    if value not in REWIND_SCOPE_LABELS:
        raise ValueError(f"invalid rewind scope: {scope!r} (expected 1, 2 or 3)")
    return value


def _selected_line(screen: str) -> str | None:
    for line in screen.splitlines():
        if chr(0x276F) in line:
            return _normalise(line)
    return None


def selected_option_matches_scope(screen: str, scope: int) -> bool:
    """Match the highlighted confirmation option without substring collisions.

    "Restore code and conversation" contains neither the contiguous phrase
    "Restore conversation" nor a bare "Restore code" line, so these three
    predicates are mutually exclusive.
    """
    line = _selected_line(screen)
    if line is None:
        return False
    both = _normalise(REWIND_SCOPE_LABELS[1])
    if scope == 1:
        return both in line
    if scope == 2:
        return _normalise(REWIND_SCOPE_LABELS[2]) in line
    return _normalise(REWIND_SCOPE_LABELS[3]) in line and both not in line


def _selected_scope(screen: str) -> int | None:
    """Which rewind-scope option (1/2/3) is currently highlighted, if any."""
    for scope in (1, 2, 3):
        if selected_option_matches_scope(screen, scope):
            return scope
    return None


@dataclass(frozen=True)
class AnchorSpec:
    message_text: str
    message_id: str | None = None
    absolute_index: int | None = None
    #: 0-based occurrence index among checkpoints whose preview matches the
    #: anchor terms, counting from the oldest. Worker-report messages share
    #: an identical first-line header, so text matching alone is ambiguous;
    #: the server computes this ordinal from history (see
    #: compute_match_ordinal) and the driver selects the (ordinal+1)-th
    #: matching checkpoint instead of the first.
    match_ordinal: int | None = None
    epoch: str | None = None


@dataclass
class ForkResult:
    parent_session_id: str
    new_session_id: str | None = None
    transcript_path: str | None = None
    original_unchanged: bool | None = None
    exit_code: int | None = None
    seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


@dataclass
class RewindResult:
    stage: str = RewindStage.STARTING.value
    success: bool = False
    session_id: str = ""
    anchor_text: str = ""
    scope: int = 1
    scope_label: str = REWIND_SCOPE_LABELS[1]
    add_dirs: list[str] = field(default_factory=list)
    add_dirs_notes: list[str] = field(default_factory=list)
    navigation_steps: int | None = None
    elapsed_seconds: float = 0.0
    file_checks: dict[str, Any] = field(default_factory=dict)
    restore_verification: str | None = None
    screen_digest: str | None = None
    screen_tail: str | None = None
    cleanup: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


StatusCallback = Callable[[str, Mapping[str, Any]], None]


class AnchorOutOfRangeError(LookupError):
    """The anchor message is not among the rewindable checkpoints (too old
    or otherwise absent from the checkpoint list) — a business limit, not a
    text-matching failure."""


class NoCodeChangesAtCheckpointError(RuntimeError):
    """The selected checkpoint has no code changes after it, so cbc only
    offers "Restore conversation" / "Never Mind" — code-bearing scopes
    (1 and 3) cannot be satisfied there."""


_CRASH_SIGNATURES = (
    'CheckpointRestoreValidationError',
    'UnhandledPromiseRejection',
    '<rejected>',
)


def _crash_error(screen: str) -> str | None:
    """Friendly error if the screen shows a cbc crash mid-restore.

    cbc 2.160.0 crashes with an unhandled CheckpointRestoreValidationError
    when a checkpoint's tracked file is outside the workspace (e.g. the
    Desktop) — the confirmation page stays up and the process hangs, so
    without this detection the driver would wait out the whole timeout.
    """
    if not any(sig in screen for sig in _CRASH_SIGNATURES):
        return None
    if 'CheckpointRestoreValidationError' in screen or 'outside the workspace' in screen:
        return ('cbc 拒绝恢复：该检查点涉及的文件不在工作区内（如桌面等外部路径），'
                'cbc 2.160 起禁止恢复工作区外的文件；可重试（确认已附加 --add-dir）'
                '或改用「仅对话」')
    return 'cbc 在恢复文件时崩溃（界面出现未处理异常），已中止'


def _post_restore_screen(screen: str) -> bool:
    """The restore finished and the TUI is back at the interactive view:
    the rewind UI is gone and the normal prompt status line is visible."""
    if 'Never Mind' in screen or 'Restore and fork the conversation' in screen:
        return False
    return 'for agents' in screen or 'bypass permissions' in screen


def _await_restore(session: AutomationSession, *, timeout: float,
                   watch_baseline: Mapping[str, tuple[bool, int, int]] | None = None,
                   expected_files: Mapping[str | Path, Any] | None = None,
                   ) -> tuple[str, dict[str, Any]]:
    """Multi-signal restore completion gate (any one wins):

    1. a watched/expected file actually changed on disk (strongest);
    2. a cbc crash signature or process exit (fail FAST, never wait out);
    3. the screen settled into the post-restore interactive view.

    On timeout the actual state is verified before deciding: a crash or a
    still-open confirmation page proves the restore did NOT happen and
    fails; a post-restore screen without an observed file change (content
    no-op restore) completes with an explicit unverified mark. The PTY is
    never killed on a mere assumption.
    """
    checks: dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        screen = session.text()
        crash = _crash_error(screen)
        if crash and not _post_restore_screen(screen):
            raise RuntimeError(crash)
        if not session.alive():
            raise RuntimeError(crash or 'cbc 进程在恢复期间意外退出')
        if watch_baseline is not None:
            ok, checks = _files_changed(watch_baseline)
            if ok:
                return 'file-change', checks
        elif expected_files is not None:
            ok, checks = _files_match(expected_files)
            if ok:
                return 'expected-files', checks
        elif _post_restore_screen(screen):
            return 'screen-settle', checks
        session.wait_for(lambda value: True, min(deadline, time.monotonic() + 0.1))
    # Timeout: verify the actual state before deciding what happened.
    screen = session.text()
    crash = _crash_error(screen)
    if crash:
        raise RuntimeError(crash)
    if not session.alive():
        raise RuntimeError('cbc 进程在恢复期间意外退出')
    if watch_baseline is not None:
        ok, checks = _files_changed(watch_baseline)
        if ok:
            return 'file-change-late', checks
    if 'Never Mind' in screen or 'Restore and fork the conversation' in screen:
        raise RuntimeError('恢复确认页在限时内未关闭（确认键可能被吞或 cbc 无响应），已中止')
    if _post_restore_screen(screen):
        return 'unverified-timeout-screen', checks
    return 'unverified-timeout-ambiguous', checks


def _snapshot_files(paths: Sequence[str | Path]) -> dict[str, tuple[bool, int, int]]:
    """(exists, size, mtime_ns) per path, for change detection."""
    snap: dict[str, tuple[bool, int, int]] = {}
    for raw in paths:
        path = Path(raw)
        try:
            stat = path.stat()
            snap[str(path)] = (True, stat.st_size, stat.st_mtime_ns)
        except OSError:
            snap[str(path)] = (False, 0, 0)
    return snap


def _files_changed(baseline: Mapping[str, tuple[bool, int, int]]) -> tuple[bool, dict[str, Any]]:
    """Compare current state against a baseline snapshot."""
    checks: dict[str, Any] = {}
    changed = False
    for name, before in baseline.items():
        now = _snapshot_files([name]).get(name, (False, 0, 0))
        did_change = now != before
        checks[name] = {'before': before, 'after': now, 'changed': did_change}
        changed = changed or did_change
    return changed, checks


def _format_exc(exc: BaseException) -> str:
    """Prefix the exception type, but never stack a duplicate prefix
    (RuntimeError: RuntimeError: ...) when re-wrapping a formatted error."""
    message = str(exc)
    prefix = f"{type(exc).__name__}:"
    return message if message.startswith(prefix) else f"{prefix} {message}"


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _normalise(value: str) -> str:
    return " ".join(value.replace(chr(13), " ").replace(chr(10), " ").split()).casefold()


def screen_contains(screen: str, needle: str) -> bool:
    target = _normalise(needle)
    return bool(target) and target in _normalise(screen)


def _anchor_terms(message_text: str) -> list[str]:
    normalized = _normalise(message_text)
    if not normalized:
        return []
    terms = []
    if len(normalized) <= 64:
        terms.append(normalized)
    else:
        for candidate in (normalized[:64], normalized[-64:]):
            if len(candidate) >= 24 and candidate not in terms:
                terms.append(candidate)
    # The cbc checkpoint preview shows only the FIRST LINE of a message,
    # hard-truncated to one screen row (verified 2026-09-30, see
    # evidence/rewind_bugfix_anchor_probe.json). Whole-message terms merge
    # newlines into spaces and can never match a multi-line preview, so the
    # normalized first line is a dedicated fallback term.
    lines = [line for line in message_text.splitlines() if line.strip()]
    if lines:
        first_line = _normalise(lines[0])[:64]
        if len(first_line) >= 8 and first_line not in terms:
            terms.append(first_line)
    return terms


def screen_matches_anchor(screen: str, message_text: str) -> bool:
    normalized_screen = _normalise(screen)
    return any(term in normalized_screen for term in _anchor_terms(message_text))


def _selected_checkpoint_row(screen: str) -> str | None:
    for line in screen.splitlines():
        if chr(0x276F) in line:
            return line
    return None


_RELATIVE_TIME_RE = re.compile(r'\d+[smhd]\s+ago')


def _selected_checkpoint_signature(screen: str) -> tuple[str, str, str] | None:
    """Movement signature of the selected checkpoint entry: the ❯ preview
    row, its metadata row, and the NEXT preview row, with relative
    timestamps scrubbed. The ❯ row alone cannot distinguish two adjacent
    checkpoints whose previews are identical (e.g. repeated messages) —
    walking onto such an entry looks like "no movement" and used to end
    navigation one entry early (observed 2026-09-30 with duplicate trailing
    turns). The metadata rows tick every second, so their relative-time
    fragment must be scrubbed or the signature never goes quiet at the
    bottom of the list."""
    lines = screen.splitlines()
    for index, line in enumerate(lines):
        if chr(0x276F) not in line:
            continue

        def scrub(i: int) -> str:
            if i >= len(lines):
                return ''
            return _RELATIVE_TIME_RE.sub('', _normalise(lines[i]))

        # Include the following three rows (metadata, blank, next preview):
        # any scroll or selection move changes at least one of them, while
        # scrubbed timestamps keep the signature quiet when nothing moved.
        return (scrub(index), scrub(index + 1), scrub(index + 2), scrub(index + 3))
    return None


def selected_checkpoint_matches_anchor(screen: str, message_text: str) -> bool:
    row = _selected_checkpoint_row(screen)
    if row is None:
        return False
    normalized_row = _normalise(row)
    return any(term in normalized_row for term in _anchor_terms(message_text))


def compute_match_ordinal(history: Sequence[Any], anchor_index: int, anchor_text: str) -> int:
    """0-based occurrence index of the anchor among checkpoints whose preview
    can match the anchor terms, counting PRIOR user messages only.

    The checkpoint preview shows the message's first line (truncated), so a
    prior user message collides iff the anchor's normalized first-line term
    appears in the prior message's normalized first line. Worker reports all
    share the `////by agent : {sid} | {name}` header, making this
    disambiguation load-bearing in real sessions.
    """
    lines = [line for line in str(anchor_text).splitlines() if line.strip()]
    if not lines:
        return 0
    first_line_term = _normalise(lines[0])[:64]
    if not first_line_term:
        return 0
    ordinal = 0
    for message in list(history)[:anchor_index]:
        if not isinstance(message, dict) or message.get('role') != 'user':
            continue
        content = message.get('content')
        if not isinstance(content, str):
            continue
        prior_lines = [line for line in content.splitlines() if line.strip()]
        if not prior_lines:
            continue
        if first_line_term in _normalise(prior_lines[0]):
            ordinal += 1
    return ordinal


def _files_match(expected_files: Mapping[str | Path, Any] | None) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    if not expected_files:
        return True, checks
    ok = True
    for raw_path, expected in expected_files.items():
        path = Path(raw_path)
        try:
            exists = path.exists()
            actual = path.read_text(encoding="utf-8", errors="replace") if exists and path.is_file() else None
            read_error = None
        except OSError as exc:
            exists = True
            actual = None
            read_error = f"{type(exc).__name__}: {exc}"
        match = ((expected is None and not exists) or (expected is not None and exists and actual == expected))
        checks[str(path)] = {"expected": expected, "exists": exists, "content": actual, "match": match, "read_error": read_error}
        ok = ok and match
    return ok, checks


def _emit(callback: StatusCallback | None, stage: RewindStage, **details: Any) -> None:
    if callback:
        callback(stage.value, details)


class RewindAutomation:
    def __init__(self, *, timeout: float = 30.0, rows: int = 36, cols: int = 120,
                 on_stage: StatusCallback | None = None):
        self.timeout = timeout
        self.rows = rows
        self.cols = cols
        self.on_stage = on_stage

    def rewind(self, cli_session_id: str, workdir: str | Path, anchor: AnchorSpec | str,
               *, session: AutomationSession,
               expected_files: Mapping[str | Path, Any] | None = None,
               watched_files: Sequence[str | Path] | None = None,
               add_dirs: Sequence[str | Path] | None = None,
               scope: int | str = 1) -> RewindResult:
        started = time.monotonic()
        scope = coerce_rewind_scope(scope)
        spec = anchor if isinstance(anchor, AnchorSpec) else AnchorSpec(str(anchor))
        result = RewindResult(session_id=cli_session_id, anchor_text=spec.message_text)
        if add_dirs is not None:
            result.add_dirs = [str(d) for d in add_dirs]
        elif watched_files:
            dirs, notes = compute_add_dirs(watched_files, workdir)
            result.add_dirs = dirs
            result.add_dirs_notes = notes
        else:
            result.add_dirs_notes = ['无受影响文件路径，未附加 --add-dir']
        try:
            _emit(self.on_stage, RewindStage.STARTING, session_id=cli_session_id)
            _emit(self.on_stage, RewindStage.RESUMING, session_id=cli_session_id)
            ready, _ = session.wait_for(
                lambda value: ("CodeBuddy Code" in value and ("\n>" in value or value.rstrip().endswith(">"))),
                time.monotonic() + self.timeout,
            )
            if not ready:
                raise RuntimeError("TUI did not become interactive")
            _emit(self.on_stage, RewindStage.REWIND_MENU)
            # Double-Esc opens the rewind menu, but on a slow resume the
            # first pair can land before the TUI is really interactive.
            # Retry with bounded pairs; between sends, check before pressing
            # again so an already-open menu is never toggled shut.
            menu_ok = False
            for _attempt in range(3):
                session.send(chr(27))
                time.sleep(0.3)
                if "Restore and fork the conversation" in session.text():
                    menu_ok = True
                    break
                session.send(chr(27))
                menu_ok, _ = session.wait_for(
                    lambda value: "Restore and fork the conversation" in value,
                    time.monotonic() + min(self.timeout, 6.0),
                )
                if menu_ok:
                    break
            if not menu_ok:
                raise RuntimeError("rewind menu did not appear")
            steps, _ = navigate_to_anchor(
                session, spec.message_text, timeout=self.timeout,
                match_ordinal=spec.match_ordinal or 0)
            result.navigation_steps = steps
            session.send(chr(13))
            # The confirmation page is built per checkpoint: a checkpoint
            # without code changes only offers "Restore conversation" /
            # "Never Mind". Wait for "Never Mind" (present in both layouts)
            # instead of the scope-1 label, so the no-code-changes case
            # fails fast instead of burning the whole timeout.
            confirm_ok, _ = session.wait_for(
                lambda value: "Never Mind" in value,
                time.monotonic() + self.timeout,
            )
            if not confirm_ok:
                raise RuntimeError("rewind confirmation page did not appear")
            if scope != 2 and "Restore code and conversation" not in session.text():
                raise NoCodeChangesAtCheckpointError(
                    "该检查点之后没有代码变更，无法回滚代码；可改用「仅对话」")
            # Scope selection: default highlight is item 1; move down for 2/3.
            # Verified key sequence: ArrowDown x (scope-1), then Enter — but
            # VERIFY the highlighted row after every press, because whole-
            # screen diffs false-positive on repaints and a keypress can be
            # swallowed during re-render (observed: 2 presses, moved once).
            scope_ok = False
            moves = 0
            max_moves = scope + 2
            while moves <= max_moves:
                current = _selected_scope(session.text())
                if current == scope:
                    scope_ok = True
                    break
                if current is not None and current > scope:
                    break  # overshot; ArrowUp recovery is not worth it
                if moves >= max_moves:
                    break
                session.send(chr(27) + "[B")
                moves += 1
                session.wait_for(
                    lambda value, prev=current: _selected_scope(value) not in (prev, None),
                    time.monotonic() + 1.5,
                )
            result.scope = scope
            result.scope_label = REWIND_SCOPE_LABELS[scope]
            if not scope_ok:
                raise RuntimeError(
                    f"scope option {scope} ({REWIND_SCOPE_LABELS[scope]}) could not be selected")
            _emit(self.on_stage, RewindStage.RESTORING,
                  navigation_steps=steps, scope=scope, scope_label=REWIND_SCOPE_LABELS[scope],
                  add_dirs=result.add_dirs or None,
                  add_dirs_notes=result.add_dirs_notes or None)
            # Baseline BEFORE confirming: the completion gate below watches
            # for cbc to actually rewrite these files. Without a gate the
            # driver used to declare success the moment the confirmation
            # page closed and kill the PTY mid-restore (the files kept their
            # modified state while the job reported "completed").
            watch_baseline = (
                _snapshot_files(watched_files)
                if (scope != 2 and watched_files and expected_files is None)
                else None
            )
            if scope != 2 and watch_baseline is None and expected_files is None:
                # Explicit degrade: no file paths could be extracted, so the
                # completion gate falls back to screen signals. Recorded in
                # the result (restore_verification) and the stage event.
                _emit(self.on_stage, RewindStage.RESTORING,
                      note='watched_files 为空，恢复完成判定降级为屏幕信号')
            session.send(chr(13))
            verification, checks = _await_restore(
                session,
                timeout=self.timeout,
                watch_baseline=watch_baseline,
                expected_files=expected_files if scope != 2 else None,
            )
            result.restore_verification = verification
            result.file_checks = checks
            # Brief settle so cbc can finish its transcript write before the
            # PTY is closed — the file-change signal can fire a frame before
            # cbc is fully done (observed: file lands ~0.08s after Enter).
            settle_deadline = time.monotonic() + 2.0
            settled: str | None = None
            while time.monotonic() < settle_deadline:
                session.wait_for(lambda value: True, time.monotonic() + 0.25)
                digest = _digest(session.text())
                if digest == settled:
                    break
                settled = digest
            if verification.startswith('unverified'):
                _emit(self.on_stage, RewindStage.RESTORING,
                      note=f'恢复完成未经文件变化证实（{verification}），按完成处理')
            result.success = True
            result.stage = RewindStage.COMPLETED.value
        except Exception as exc:
            result.stage = RewindStage.FAILED.value
            result.error = _format_exc(exc)
            _emit(self.on_stage, RewindStage.FAILED, error=result.error)
        finally:
            result.elapsed_seconds = round(time.monotonic() - started, 3)
            if session is not None:
                try:
                    screen = session.text()
                    result.screen_digest = _digest(screen)
                    result.screen_tail = screen[-4000:]
                except Exception:
                    pass
        return result


def _settled_selected_row(session: AutomationSession, deadline: float,
                          *, settle_window: float = 0.15) -> tuple[str | None, str]:
    """Selected checkpoint row once the screen settles (same row observed
    twice `settle_window` apart). The menu's initial highlight is transient:
    it opens on "(current)" and then settles on the OLDEST checkpoint, so a
    single read races the re-render (verified 2026-09-30,
    evidence/rewind_bugfix2_keymap.json)."""
    settled: str | None = None
    screen = session.text()
    while time.monotonic() < deadline:
        session.wait_for(lambda value: True, min(deadline, time.monotonic() + settle_window))
        screen = session.text()
        row = _selected_checkpoint_row(screen)
        if row == settled:
            return row, screen
        settled = row
    return settled, screen


def navigate_to_anchor(session: AutomationSession, message_text: str, *, timeout: float = 10.0,
                       max_steps: int = 64, match_ordinal: int = 0) -> tuple[int, str]:
    """Select the checkpoint whose preview matches the anchor message.

    Key semantics (verified 2026-09-30, evidence/rewind_bugfix2_keymap.json):
    the settled selection starts on the TOP (oldest) checkpoint, ArrowDown
    walks one step toward newer entries, ArrowUp one step toward older ones,
    and keypresses sent without waiting for the re-render are swallowed.
    Strategy: normalise to the top with ArrowUp, then walk DOWN with a
    settle-verified step loop, selecting the (match_ordinal+1)-th matching
    checkpoint — identical first-line previews (worker reports) are common,
    so the first match is not necessarily the right one.
    """
    if not _anchor_terms(message_text):
        raise ValueError("anchor message text is too short")
    deadline = time.monotonic() + timeout
    selected, last = _settled_selected_row(session, deadline)
    if selected is None:
        # The ❯ marker should always exist on the checkpoint list; missing
        # means the screen is not what we parsed for (implementation issue).
        raise LookupError("无法识别检查点列表：屏幕上没有选中项，无法匹配锚点文本")
    steps = 0
    signature = _selected_checkpoint_signature(last)
    # Phase 1: walk UP until the selected row stops moving (list top). No
    # matching here: the walk must reach the top first so phase 2 can count
    # match occurrences deterministically from the oldest checkpoint.
    while steps < max_steps and time.monotonic() < deadline:
        session.send(chr(27) + "[A")
        moved, last = session.wait_for(
            lambda value, prev=signature: (
                (_selected_checkpoint_signature(value) or prev) != prev),
            min(deadline, time.monotonic() + 1.5),
        )
        if not moved:
            break
        steps += 1
        row, last = _settled_selected_row(session, deadline)
        selected = row or selected
        signature = _selected_checkpoint_signature(last) or signature
    # Phase 2: walk DOWN (toward newer checkpoints) one verified step at a
    # time, counting anchor matches; select occurrence #match_ordinal. A
    # press that does not move the settled row means the bottom of the list
    # ("(current)") was reached first.
    matches_seen = 0
    if selected_checkpoint_matches_anchor(last, message_text):
        if match_ordinal == 0:
            return steps, last
        matches_seen = 1
    while steps < max_steps and time.monotonic() < deadline:
        session.send(chr(27) + "[B")
        moved, last = session.wait_for(
            lambda value, prev=signature: (
                (_selected_checkpoint_signature(value) or prev) != prev),
            min(deadline, time.monotonic() + 1.5),
        )
        if not moved:
            break
        steps += 1
        row, last = _settled_selected_row(session, deadline)
        selected = row or selected
        signature = _selected_checkpoint_signature(last) or signature
        if selected_checkpoint_matches_anchor(last, message_text):
            if matches_seen == match_ordinal:
                return steps, last
            matches_seen += 1
    if matches_seen and match_ordinal >= matches_seen:
        raise AnchorOutOfRangeError(
            f"锚点匹配序号超出范围：检查点列表中只有 {matches_seen} 个可匹配项，"
            f"但目标消息是第 {match_ordinal + 1} 个匹配（历史与检查点列表不一致）")
    raise AnchorOutOfRangeError(
        "该消息不在可回滚的检查点范围内：已翻检查点列表到头仍未找到"
        "（锚点可能太旧，或预览文本无法匹配）")

