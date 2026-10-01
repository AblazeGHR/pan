from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.rewind import transcript
from packages.core.rewind.hybrid import ensure_rewind_supported
from packages.core.rewind.driver import _anchor_terms, screen_matches_anchor


def _line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False) + chr(10)


def _transcript_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(transcript.Path, 'home', classmethod(lambda cls: tmp_path))
    path = tmp_path / '.codebuddy' / 'projects' / 'project-a' / 'session-b.jsonl'
    path.parent.mkdir(parents=True)
    return path


def test_truncate_keeps_rows_before_anchor_and_removes_anchor_onwards(tmp_path, monkeypatch):
    path = _transcript_path(tmp_path, monkeypatch)
    original = [
        {'type': 'system', 'id': 'sys'},
        {'type': 'message', 'role': 'user', 'id': 'u1', 'content': [{'text': 'anchor text'}]},
        {'type': 'function_call', 'id': 'tool'},
        {'type': 'message', 'role': 'assistant', 'id': 'a1', 'content': [{'text': 'done'}]},
    ]
    path.write_text(''.join(_line(item) for item in original), encoding='utf-8')

    result = transcript.truncate_transcript(
        path,
        transcript.TranscriptAnchor(message_text='anchor text'),
        expected_session_id='session-b',
    )

    rows = path.read_text(encoding='utf-8').splitlines()
    assert result.before_lines == 4
    assert result.after_lines == 1
    assert result.removed_lines == 3
    assert result.anchor_record_id == 'u1'
    # The anchor row itself is excluded: only the preceding system row stays.
    assert len(rows) == 1
    assert json.loads(rows[0])['id'] == 'sys'
    assert not list(path.parent.glob('*.tmp'))
    assert not list(path.parent.glob('*.pan-rewind-backup-*'))


def test_truncate_anchor_on_first_row_yields_empty_transcript(tmp_path, monkeypatch):
    path = _transcript_path(tmp_path, monkeypatch)
    original = [
        {'type': 'message', 'role': 'user', 'id': 'u1', 'content': [{'text': 'anchor text'}]},
        {'type': 'message', 'role': 'assistant', 'id': 'a1', 'content': [{'text': 'done'}]},
    ]
    path.write_text(''.join(_line(item) for item in original), encoding='utf-8')

    result = transcript.truncate_transcript(
        path,
        transcript.TranscriptAnchor(message_text='anchor text'),
        expected_session_id='session-b',
    )

    assert result.after_lines == 0
    assert result.removed_lines == 2
    assert path.read_bytes() == b''
    assert not list(path.parent.glob('*.tmp'))
    assert not list(path.parent.glob('*.pan-rewind-backup-*'))


def test_truncate_rejects_missing_or_ambiguous_anchor(tmp_path, monkeypatch):
    path = _transcript_path(tmp_path, monkeypatch)
    user = {'type': 'message', 'role': 'user', 'id': 'u1', 'content': [{'text': 'same text'}]}
    path.write_text(_line(user) + _line(user), encoding='utf-8')
    before = path.read_bytes()

    with pytest.raises(LookupError, match='ambiguous'):
        transcript.truncate_transcript(path, transcript.TranscriptAnchor(message_text='same text'))
    with pytest.raises(LookupError, match='not found'):
        transcript.truncate_transcript(path, transcript.TranscriptAnchor(message_text='missing text'))

    assert path.read_bytes() == before


def test_truncate_match_ordinal_picks_the_requested_occurrence(tmp_path, monkeypatch):
    path = _transcript_path(tmp_path, monkeypatch)
    first = {'type': 'message', 'role': 'user', 'id': 'u1', 'content': [{'text': 'same text'}]}
    second = {'type': 'message', 'role': 'user', 'id': 'u2', 'content': [{'text': 'same text'}]}
    tail = {'type': 'message', 'role': 'assistant', 'id': 'a1', 'content': [{'text': 'done'}]}
    path.write_text(_line(first) + _line(second) + _line(tail), encoding='utf-8')

    result = transcript.truncate_transcript(
        path,
        transcript.TranscriptAnchor(message_text='same text', match_ordinal=1),
        expected_session_id='session-b',
    )
    assert result.anchor_record_id == 'u2'
    assert result.after_lines == 1
    assert result.removed_lines == 2


def test_truncate_match_ordinal_out_of_range_is_a_clear_error(tmp_path, monkeypatch):
    path = _transcript_path(tmp_path, monkeypatch)
    user = {'type': 'message', 'role': 'user', 'id': 'u1', 'content': [{'text': 'same text'}]}
    path.write_text(_line(user) + _line(user), encoding='utf-8')
    before = path.read_bytes()

    with pytest.raises(LookupError, match='ordinal out of range'):
        transcript.truncate_transcript(
            path,
            transcript.TranscriptAnchor(message_text='same text', match_ordinal=5),
            expected_session_id='session-b',
        )
    assert path.read_bytes() == before


def test_truncate_rejects_wrong_path_and_non_user_index(tmp_path, monkeypatch):
    path = _transcript_path(tmp_path, monkeypatch)
    path.write_text(_line({'type': 'system', 'id': 'sys'}), encoding='utf-8')

    with pytest.raises(ValueError, match='forked session'):
        transcript.truncate_transcript(path, transcript.TranscriptAnchor(absolute_index=0), expected_session_id='other')
    with pytest.raises(ValueError, match='user message'):
        transcript.truncate_transcript(path, transcript.TranscriptAnchor(absolute_index=0), expected_session_id='session-b')

    outside = tmp_path / 'outside.jsonl'
    outside.write_text(_line({'type': 'message', 'role': 'user', 'content': [{'text': 'x'}]}), encoding='utf-8')
    with pytest.raises(ValueError, match='outside'):
        transcript.truncate_transcript(outside, transcript.TranscriptAnchor(message_text='x'))


def test_rewind_adapter_extension_point_is_explicit():
    ensure_rewind_supported('cbc')
    with pytest.raises(NotImplementedError, match='only cbc'):
        ensure_rewind_supported('claude')


def test_long_anchor_uses_visible_prefix_or_suffix():
    anchor = 'prefix ' + 'middle ' * 40 + 'suffix-marker'
    terms = _anchor_terms(anchor)
    assert len(terms) == 2
    assert screen_matches_anchor('screen shows ' + terms[0], anchor)
    assert screen_matches_anchor('screen shows ' + terms[1], anchor)
    assert not screen_matches_anchor('unrelated screen', anchor)
