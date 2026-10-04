"""Bounded CBC fork and lifecycle facade over the shared terminal core."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .automation import (
    AutomationSession,
    RewindStage,
    coerce_rewind_scope,
    _selected_line,
    selected_option_matches_scope,
    _selected_scope,
    AnchorSpec,
    ForkResult,
    RewindResult,
    AnchorOutOfRangeError,
    NoCodeChangesAtCheckpointError,
    _crash_error,
    _post_restore_screen,
    _await_restore,
    _snapshot_files,
    _files_changed,
    _format_exc,
    _digest,
    _normalise,
    screen_contains,
    _anchor_terms,
    screen_matches_anchor,
    _selected_checkpoint_row,
    _selected_checkpoint_signature,
    selected_checkpoint_matches_anchor,
    compute_match_ordinal,
    _files_match,
    _emit,
    _settled_selected_row,
    navigate_to_anchor,
    RewindAutomation,
    REWIND_SCOPE_LABELS,
    StatusCallback
)
from .pty_session import PtySession as _PtySession, retry_cleanup


def _find_cbc() -> list[str]:
    shim = shutil.which("cbc")
    if shim:
        return [shim]
    appdata = os.environ.get("APPDATA")
    node = shutil.which("node")
    entry = Path(appdata or "") / "npm" / "node_modules" / "@tencent-ai" / "codebuddy-code" / "bin" / "codebuddy"
    if node and entry.is_file():
        return [node, str(entry)]
    raise FileNotFoundError("cbc command is unavailable")


def _build_resume_argv(cli_session_id: str,
                       add_dirs: Sequence[str] | None = None) -> list[str]:
    """Interactive resume argv for the rewind PTY.

    ``--add-dir`` extends cbc's allowed roots (cbc 2.160.0 refuses to
    restore checkpoints whose tracked files live outside the workspace, see
    docs/REWIND_PTY_REPORT.md §16) — the rewind driver feeds the affected
    files' parent directories here so out-of-workspace restores are legal.
    """
    argv = [*_find_cbc(), "-r", cli_session_id,
            "--permission-mode", "bypassPermissions"]
    if add_dirs:
        argv += ["--add-dir", *add_dirs]
    return argv


def _session_jsonl(session_id: str) -> Path | None:
    root = Path.home() / ".codebuddy" / "projects"
    if not root.is_dir():
        return None
    hits = list(root.rglob(f"{session_id}.jsonl"))
    return hits[0] if hits else None


def _extract_session_ids(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r'"(?:session_id|sessionId)"\s*:\s*"([A-Za-z0-9_-]+)"', text):
        sid = match.group(1)
        if sid not in found:
            found.append(sid)
    return found


def fork_session(cli_session_id: str, workdir: str | Path, *, timeout: float = 180.0,
                 prompt: str = "Reply with exactly PAN_REWIND_FORK_READY.") -> ForkResult:
    started = time.monotonic()
    result = ForkResult(parent_session_id=cli_session_id)
    original = _session_jsonl(cli_session_id)
    before = original.read_bytes() if original and original.exists() else None
    before_paths = {p.name for p in original.parent.glob("*.jsonl")} if original and original.parent.exists() else set()
    try:
        argv = [*_find_cbc(), "-p", "--resume", cli_session_id, "--fork-session",
                "--permission-mode", "bypassPermissions", "--output-format", "json", prompt]
        proc = subprocess.run(
            argv, cwd=str(workdir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
        result.exit_code = proc.returncode
        result.stdout = proc.stdout[-12000:]
        result.stderr = proc.stderr[-4000:]
        result.original_unchanged = (before is None or not original or not original.exists()
                                     or original.read_bytes() == before)
        candidates = _extract_session_ids(result.stdout + "\n" + result.stderr)
        if original and original.parent.exists():
            new_paths = [
                path for path in original.parent.glob("*.jsonl")
                if path.name not in before_paths
            ]
            for path in sorted(new_paths, key=lambda p: p.stat().st_mtime_ns, reverse=True):
                candidates.insert(0, path.stem)
                result.transcript_path = str(path)
                break
        result.new_session_id = next((sid for sid in candidates if sid != cli_session_id), None)
        if result.new_session_id and not result.transcript_path:
            path = _session_jsonl(result.new_session_id)
            result.transcript_path = str(path) if path else None
        if proc.returncode != 0:
            result.error = f"cbc fork exited with {proc.returncode}"
        elif not result.new_session_id:
            result.error = "forked session id was not found"
        elif result.original_unchanged is False:
            result.error = "parent transcript changed during fork"
    except subprocess.TimeoutExpired:
        result.error = "cbc fork timed out"
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    result.seconds = round(time.monotonic() - started, 3)
    return result


class RewindDriver:
    """Lifecycle owner; menu decisions only use AutomationContext."""

    def __init__(self, *, timeout: float = 30.0, rows: int = 36, cols: int = 120,
                 on_stage: StatusCallback | None = None):
        self.timeout, self.rows, self.cols = timeout, rows, cols
        self.on_stage = on_stage

    def rewind(self, cli_session_id: str, workdir: str | Path, anchor: AnchorSpec | str,
               *, expected_files: Mapping[str | Path, Any] | None = None,
               watched_files: Sequence[str | Path] | None = None,
               add_dirs: Sequence[str | Path] | None = None,
               scope: int | str = 1) -> RewindResult:
        from .filetools import compute_add_dirs
        started = time.monotonic()
        scope = coerce_rewind_scope(scope)
        spec = anchor if isinstance(anchor, AnchorSpec) else AnchorSpec(str(anchor))
        result = RewindResult(session_id=cli_session_id, anchor_text=spec.message_text)
        dirs = ([str(d) for d in add_dirs] if add_dirs is not None else
                compute_add_dirs(watched_files, workdir)[0] if watched_files else [])
        owner = None
        try:
            _emit(self.on_stage, RewindStage.STARTING, session_id=cli_session_id)
            owner = _PtySession(_build_resume_argv(cli_session_id, dirs), workdir,
                                rows=self.rows, cols=self.cols)
            result = RewindAutomation(timeout=self.timeout, rows=self.rows, cols=self.cols,
                                      on_stage=self.on_stage).rewind(
                cli_session_id, workdir, spec, session=AutomationSession(owner.context),
                expected_files=expected_files, watched_files=watched_files,
                add_dirs=add_dirs, scope=scope)
        except Exception as exc:
            result.stage = RewindStage.FAILED.value
            result.error = _format_exc(exc)
            startup_cleanup = getattr(exc, "cleanup", None)
            if isinstance(startup_cleanup, dict):
                result.cleanup = dict(startup_cleanup)
        finally:
            if owner is not None:
                result.cleanup = owner.close()
                if result.cleanup.get("ok") is not True:
                    result.success = False
                    result.stage = RewindStage.FAILED.value
                    result.error = result.error or "PTY cleanup not confirmed; owner retained for retry"
            result.elapsed_seconds = round(time.monotonic() - started, 3)
        if result.success:
            _emit(self.on_stage, RewindStage.COMPLETED,
                  navigation_steps=result.navigation_steps,
                  restore_verification=result.restore_verification)
        elif result.cleanup.get("ok") is not True:
            _emit(self.on_stage, RewindStage.FAILED, error=result.error,
                  cleanup=result.cleanup)
        return result


def rewind_in_pty(new_cli_session_id: str, workdir: str | Path, anchor: AnchorSpec | str,
                  *, expected_files: Mapping[str | Path, Any] | None = None,
                  watched_files: Sequence[str | Path] | None = None,
                  add_dirs: Sequence[str | Path] | None = None,
                  timeout: float = 30.0, on_stage: StatusCallback | None = None,
                  scope: int | str = 1) -> RewindResult:
    return RewindDriver(timeout=timeout, on_stage=on_stage).rewind(
        new_cli_session_id, workdir, anchor, expected_files=expected_files,
        watched_files=watched_files, add_dirs=add_dirs, scope=scope,
    )
