"""Unit tests for the rewind --add-dir support (cbc 2.160.0 outside-workspace fix).

cbc refuses to restore checkpoints whose tracked files live outside the
workspace unless their directories are passed via --add-dir. These tests
cover the directory-set computation (parent extraction, dedupe, workspace
filtering, dangerous-root rejection, cap truncation) and the spawn argv.
"""

from __future__ import annotations

import os
from pathlib import Path

from packages.core.rewind.driver import _build_resume_argv, _crash_error
from packages.core.rewind.filetools import compute_add_dirs


def test_compute_add_dirs_collects_existing_parent_dirs(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'world.py'
    target.write_text('v1', encoding='utf-8')
    workdir = tmp_path / 'workspace'
    workdir.mkdir()
    dirs, notes = compute_add_dirs([target], workdir)
    assert dirs == [str(outside)]
    assert notes == []


def test_compute_add_dirs_dedupes_and_preserves_order(tmp_path):
    dir_a = tmp_path / 'a'
    dir_b = tmp_path / 'b'
    dir_a.mkdir()
    dir_b.mkdir()
    files = [dir_a / 'one.txt', dir_b / 'two.txt', dir_a / 'three.txt']
    dirs, _ = compute_add_dirs(files, tmp_path / 'elsewhere')
    assert dirs == [str(dir_a), str(dir_b)]


def test_compute_add_dirs_skips_workspace_dirs(tmp_path):
    workdir = tmp_path / 'workspace'
    nested = workdir / 'sub'
    nested.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    files = [workdir / 'top.txt', nested / 'inner.txt', outside / 'out.txt']
    dirs, notes = compute_add_dirs(files, workdir)
    assert dirs == [str(outside)]
    assert notes == []


def test_compute_add_dirs_resolves_relative_against_workdir(tmp_path):
    workdir = tmp_path / 'workspace'
    workdir.mkdir()
    sibling = tmp_path / 'sibling'
    sibling.mkdir()
    # ../sibling/x.txt escapes the workspace and must be added.
    dirs, _ = compute_add_dirs([os.path.join('..', 'sibling', 'x.txt')], workdir)
    assert dirs == [str(sibling)]


def test_compute_add_dirs_skips_missing_dirs_with_note(tmp_path):
    missing = tmp_path / 'gone' / 'file.txt'
    dirs, notes = compute_add_dirs([missing], tmp_path / 'workspace')
    assert dirs == []
    assert any('不存在' in note for note in notes)


def test_compute_add_dirs_rejects_drive_root(tmp_path):
    root = Path(Path.cwd().anchor)  # e.g. C:\ on Windows, / on POSIX
    loose = root / 'loose-at-root.txt'
    dirs, notes = compute_add_dirs([loose], tmp_path)
    assert dirs == []
    assert any('盘根' in note for note in notes)


def test_compute_add_dirs_caps_at_max_with_note(tmp_path):
    base = tmp_path / 'many'
    files = []
    for index in range(5):
        sub = base / f'd{index}'
        sub.mkdir(parents=True)
        files.append(sub / 'f.txt')
    dirs, notes = compute_add_dirs(files, tmp_path / 'workspace', max_dirs=3)
    assert len(dirs) == 3
    assert any('上限' in note and '2' in note for note in notes)


def test_build_resume_argv_appends_add_dir(monkeypatch):
    monkeypatch.setattr('packages.core.rewind.driver._find_cbc', lambda: ['cbc'])
    argv = _build_resume_argv('sess-1', ['D:\\extra', 'D:\\more'])
    assert argv == ['cbc', '-r', 'sess-1', '--permission-mode', 'bypassPermissions',
                    '--add-dir', 'D:\\extra', 'D:\\more']


def test_build_resume_argv_omits_add_dir_when_empty(monkeypatch):
    monkeypatch.setattr('packages.core.rewind.driver._find_cbc', lambda: ['cbc'])
    argv = _build_resume_argv('sess-1', [])
    assert '--add-dir' not in argv


def test_crash_error_suggests_retry_or_conversation_only():
    message = _crash_error('CheckpointRestoreValidationError: outside the workspace')
    assert message is not None
    assert '重试' in message
    assert '仅对话' in message
