'''Atomic transcript truncation for cbc rewind copies.'''

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TranscriptAnchor:
    '''Anchor priority: message_id, then message_text, then absolute_index.'''

    message_id: str | None = None
    message_text: str | None = None
    absolute_index: int | None = None
    #: 0-based occurrence among text-matching user messages, computed from
    #: Pan history (see driver.compute_match_ordinal). When None, multiple
    #: text matches stay an ambiguity error (legacy strict behavior).
    match_ordinal: int | None = None


@dataclass
class TruncationResult:
    path: str
    #: Located anchor row (exclusive end of the kept prefix).
    anchor_line_index: int
    before_lines: int
    #: Rows kept = anchor_line_index (anchor itself excluded).
    after_lines: int
    #: Rows dropped = anchor row plus everything after it.
    removed_lines: int
    anchor_record_id: str | None
    sha1_before: str
    sha1_after: str


def _normalise(value: str) -> str:
    return ' '.join(value.replace(chr(13), ' ').replace(chr(10), ' ').split()).casefold()


def _record_text(record: dict[str, Any]) -> str:
    texts: list[str] = []
    content = record.get('content')
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get('text'), str):
                texts.append(item['text'])
    return ' '.join(texts)


def _is_user_message(record: dict[str, Any]) -> bool:
    return record.get('type') == 'message' and record.get('role') == 'user'


def _record_ids(record: dict[str, Any]) -> set[str]:
    values = {
        record.get('id'),
        record.get('messageId'),
        record.get('message_id'),
        record.get('panMessageId'),
        record.get('pan_message_id'),
    }
    message = record.get('message')
    if isinstance(message, dict):
        values.update({message.get('id'), message.get('messageId'), message.get('message_id')})
    return {str(value) for value in values if isinstance(value, str) and value}


def _parse_line(line: str, index: int) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f'invalid transcript JSON at line {index + 1}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'transcript line {index + 1} is not an object')
    return value


def find_anchor_line(lines: list[str], anchor: TranscriptAnchor) -> tuple[int, dict[str, Any]]:
    records = [_parse_line(line, index) for index, line in enumerate(lines)]
    if anchor.message_id:
        matches = [
            (index, record) for index, record in enumerate(records)
            if anchor.message_id in _record_ids(record)
        ]
        if len(matches) > 1:
            raise LookupError('transcript anchor id is ambiguous')
        if matches:
            index, record = matches[0]
            if not _is_user_message(record):
                raise ValueError('transcript anchor id does not identify a user message')
            return index, record
        raise LookupError('transcript anchor id not found')
    if anchor.message_text:
        needle = _normalise(anchor.message_text)
        if len(needle) < 3:
            raise ValueError('transcript anchor text is too short')
        # cbc may persist only the FIRST LINE of a multi-line user message
        # in the transcript (observed in headless transcripts), so fall back
        # to progressively weaker terms. First term with hits wins; more
        # than one hit is an honest ambiguity error either way.
        terms = [needle]
        text_lines = [line for line in anchor.message_text.splitlines() if line.strip()]
        if text_lines:
            first_line = _normalise(text_lines[0])[:64]
            if len(first_line) >= 8 and first_line not in terms:
                terms.append(first_line)
        if len(needle) > 64:
            for candidate in (needle[:64], needle[-64:]):
                if len(candidate) >= 24 and candidate not in terms:
                    terms.append(candidate)
        for term in terms:
            matches = [
                (index, record) for index, record in enumerate(records)
                if _is_user_message(record) and term in _normalise(_record_text(record))
            ]
            if len(matches) > 1:
                if anchor.match_ordinal is not None:
                    if anchor.match_ordinal < len(matches):
                        return matches[anchor.match_ordinal]
                    raise LookupError(
                        f'transcript anchor match ordinal out of range: '
                        f'{len(matches)} matches, need occurrence {anchor.match_ordinal + 1}')
                raise LookupError('transcript anchor text is ambiguous')
            if matches:
                return matches[0]
        raise LookupError('transcript anchor text not found')
    if anchor.absolute_index is not None:
        index = anchor.absolute_index
        if index < 0 or index >= len(records):
            raise LookupError('transcript anchor absolute index is out of range')
        record = records[index]
        if not _is_user_message(record):
            raise ValueError('transcript absolute index does not identify a user message')
        return index, record
    raise ValueError('a transcript anchor is required')


def _safe_transcript_path(path: str | Path, expected_session_id: str | None) -> Path:
    resolved = Path(path).expanduser().resolve(strict=True)
    root = (Path.home() / '.codebuddy' / 'projects').resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError('transcript path is outside the cbc projects root')
    if resolved.suffix != '.jsonl':
        raise ValueError('transcript path must be a JSONL file')
    if expected_session_id and resolved.name != f'{expected_session_id}.jsonl':
        raise ValueError('transcript path does not belong to the forked session')
    return resolved


def _replace_with_retry(source: Path, target: Path) -> None:
    for attempt in range(20):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01 * (attempt + 1))


def truncate_transcript(path: str | Path, anchor: TranscriptAnchor,
                        *, expected_session_id: str | None = None) -> TruncationResult:
    '''Keep every row BEFORE the anchor user message; discard the anchor
    line itself and every later row.

    cbc's native rewind restores the checkpoint = the state BEFORE the
    anchored message was processed (its file changes are undone), so the
    conversation baseline must match: the new session ends right before the
    anchor, and Pan pre-fills the anchor text into the composer for editing.
    When the anchor is the first message the transcript becomes empty — that
    is the intended "rewind to the very beginning" outcome.
    '''
    resolved = _safe_transcript_path(path, expected_session_id)
    original = resolved.read_bytes()
    lines = original.decode('utf-8').splitlines(keepends=True)
    index, record = find_anchor_line(lines, anchor)
    prefix = ''.join(lines[:index])
    token = secrets.token_hex(6)
    backup = resolved.with_name(f'{resolved.name}.pan-rewind-backup-{token}')
    temporary = resolved.with_name(f'{resolved.name}.pan-rewind-{token}.tmp')
    backup.write_bytes(original)
    try:
        temporary.write_text(prefix, encoding='utf-8', newline='')
        _replace_with_retry(temporary, resolved)
        updated = resolved.read_bytes()
        if updated != prefix.encode('utf-8'):
            raise OSError('truncated transcript verification failed')
    except Exception:
        try:
            if backup.exists():
                _replace_with_retry(backup, resolved)
        finally:
            temporary.unlink(missing_ok=True)
        raise
    backup.unlink(missing_ok=True)
    return TruncationResult(
        path=str(resolved),
        anchor_line_index=index,
        before_lines=len(lines),
        after_lines=index,
        removed_lines=len(lines) - index,
        anchor_record_id=record.get('id') if isinstance(record.get('id'), str) else None,
        sha1_before=hashlib.sha1(original).hexdigest(),
        sha1_after=hashlib.sha1(prefix.encode('utf-8')).hexdigest(),
    )
