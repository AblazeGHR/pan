"""Extract files mutated by cbc file-tool calls from Pan history messages.

Used to build the rewind driver's watched_files completion gate: after the
confirmation, cbc must actually rewrite at least one of these files,
otherwise the restore did not happen and the job must fail loudly instead
of reporting success (the completed-but-nothing-restored bug).

History tool messages look like Write({"file_path": ...}) or
Edit({"file_path": ...}) for cbc, or FileChange({"changes": [...]}) /
"tool call: FileChange\nargs: {...}" for other adapters. Only mutating
tools are collected.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

_MUTATING_PATH_KEYS = ('file_path', 'filePath', 'path')
_MUTATING_TOOL_NAMES = {'write', 'edit', 'multiedit', 'notebookedit', 'filechange'}


def _tool_message_parts(content: Any) -> Iterable[tuple[str, str]]:
    """Yield (tool_name, args_text) pairs from a tool message body."""
    if not isinstance(content, str) or not content.strip():
        return
    text = content.strip()
    call_match = re.match(r'^tool call:\s*(.+?)(?:\r?\n|\r)args:\s*([\s\S]*)$', text)
    if call_match:
        yield call_match.group(1).splitlines()[0].strip(), call_match.group(2).strip()
        return
    modern = re.match(r'^([A-Za-z_][\w]*)\(([\s\S]*)\)$', text, re.DOTALL)
    if modern:
        yield modern.group(1).strip(), modern.group(2).strip()


def _paths_from_args(tool_name: str, args_text: str) -> list[str]:
    if tool_name.strip().casefold() not in _MUTATING_TOOL_NAMES:
        return []
    try:
        args = json.loads(args_text)
    except (ValueError, TypeError):
        return []
    if not isinstance(args, dict):
        return []
    paths: list[str] = []
    for key in _MUTATING_PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    changes = args.get('changes')
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict):
                value = change.get('path')
                if isinstance(value, str) and value:
                    paths.append(value)
    return paths


def extract_mutated_files(history: Sequence[Any], anchor_index: int) -> list[str]:
    """Files mutated by file-tool calls from the anchor's turn onwards.

    anchor_index is the anchor message's absolute position in history; its
    own turn's tool calls follow it, so the scan starts right after it.
    Order is preserved, duplicates removed.
    """
    paths: list[str] = []
    for message in list(history)[anchor_index + 1:]:
        if not isinstance(message, dict) or message.get('role') != 'tool':
            continue
        for tool_name, args_text in _tool_message_parts(message.get('content')):
            for path in _paths_from_args(tool_name, args_text):
                if path not in paths:
                    paths.append(path)
    return paths


DEFAULT_MAX_ADD_DIRS = 8


def _norm_path(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value)))


def compute_add_dirs(file_paths: Sequence[str | Path],
                     workdir: str | Path | None = None,
                     *, max_dirs: int = DEFAULT_MAX_ADD_DIRS,
                     ) -> tuple[list[str], list[str]]:
    """Parent directories of mutated files, for cbc ``--add-dir``.

    cbc 2.160.0 refuses to restore checkpoints touching files outside the
    allowed roots (session workspace + ``--add-dir`` entries), so the rewind
    driver feeds the affected files' parent directories to the PTY spawn.

    Safety rules (returns ``(dirs, notes)``; notes explain every skip):
    - only directories that currently exist are added;
    - directories inside the workspace are dropped (cbc already allows them);
    - filesystem roots (``C:\\``, ``E:\\``, ``/``) are rejected outright —
      never hand a whole drive to cbc;
    - at most ``max_dirs`` entries are kept; the rest are dropped with a note.
    """
    dirs: list[str] = []
    notes: list[str] = []
    seen: set[str] = set()
    work_norm = _norm_path(Path(workdir).resolve()) if workdir else None
    dropped = 0
    for raw in file_paths:
        path = Path(raw)
        if not path.is_absolute():
            base = Path(workdir) if workdir else Path.cwd()
            path = base / path
        # normpath (not resolve) collapses ".." without touching symlinks.
        parent = Path(os.path.normpath(str(path.parent)))
        parent_norm = _norm_path(parent)
        if parent.parent == parent:
            notes.append(f'拒绝把盘根/文件系统根 {parent} 传给 --add-dir')
            continue
        if parent_norm in seen:
            continue
        if work_norm and (parent_norm == work_norm
                          or parent_norm.startswith(work_norm + os.sep)):
            continue  # inside the workspace; cbc allows it already
        if not parent.is_dir():
            notes.append(f'目录不存在，未附加 --add-dir: {parent}')
            continue
        if len(dirs) >= max_dirs:
            dropped += 1
            continue
        seen.add(parent_norm)
        dirs.append(str(parent))
    if dropped:
        notes.append(f'--add-dir 超过上限 {max_dirs} 个，丢弃其余 {dropped} 个目录')
    return dirs, notes
