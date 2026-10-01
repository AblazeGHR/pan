"""Unit tests for the watched-files completion gate helpers.

Regression cover for the 2026-09-30 bug where rewind reported completed
while cbc was killed before restoring files.
"""

from __future__ import annotations

import json

from packages.core.rewind.driver import _files_changed, _snapshot_files
from packages.core.rewind.filetools import extract_mutated_files


def _tool(body):
    return {'role': 'tool', 'content': body}


def test_extract_mutated_files_collects_cbc_write_and_edit():
    history = [
        {'role': 'user', 'content': 'anchor'},
        _tool('Write({"content": "a", "file_path": "/tmp/a.txt"})'),
        {'role': 'assistant', 'content': 'done'},
        _tool('Edit({"file_path": "/tmp/b.txt", "old_string": "x", "new_string": "y"})'),
        _tool('Bash({"command": "echo hi"})'),
        _tool('Read({"file_path": "/tmp/read-only.txt"})'),
    ]
    assert extract_mutated_files(history, 0) == ['/tmp/a.txt', '/tmp/b.txt']


def test_extract_mutated_files_starts_after_anchor():
    history = [
        {'role': 'user', 'content': 'first'},
        _tool('Write({"file_path": "/tmp/before.txt"})'),
        {'role': 'user', 'content': 'anchor'},
        _tool('Edit({"file_path": "/tmp/after.txt"})'),
    ]
    assert extract_mutated_files(history, 2) == ['/tmp/after.txt']


def test_extract_mutated_files_supports_filechange_shapes():
    modern = _tool('FileChange({"changes": [{"path": "/tmp/x.txt"}, {"path": "/tmp/y.txt"}]})')
    legacy = _tool('tool call: FileChange\nargs: ' + json.dumps({'changes': [{'path': '/tmp/z.txt'}]}))
    history = [
        {'role': 'user', 'content': 'anchor'},
        modern,
        legacy,
    ]
    assert extract_mutated_files(history, 0) == ['/tmp/x.txt', '/tmp/y.txt', '/tmp/z.txt']


def test_extract_mutated_files_ignores_malformed_args():
    history = [
        {'role': 'user', 'content': 'anchor'},
        _tool('Write({not json'),
        _tool('Write({"file_path": ""})'),
        _tool('no tool call here'),
    ]
    assert extract_mutated_files(history, 0) == []


def test_files_changed_detects_content_and_existence(tmp_path):
    target = tmp_path / 'watched.txt'
    target.write_text('before', encoding='utf-8')
    missing = tmp_path / 'missing.txt'
    baseline = _snapshot_files([target, missing])
    assert _files_changed(baseline)[0] is False
    target.write_text('after', encoding='utf-8')
    changed, checks = _files_changed(baseline)
    assert changed is True
    assert checks[str(target)]['changed'] is True
    assert checks[str(missing)]['changed'] is False
