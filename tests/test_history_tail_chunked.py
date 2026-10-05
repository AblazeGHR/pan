"""Chunked cold reads preserve paging/crash-tail behavior and never write data."""
import hashlib
import json

import pytest

from packages.core.session import _history_page_from_jsonl


@pytest.mark.parametrize("before", [0, 1, 23, 57, 100])
@pytest.mark.parametrize("limit", [1, 50, 200])
@pytest.mark.parametrize("row_size", [37, 40000])
def test_chunked_tail_matches_compatibility_reader(tmp_path, limit, row_size, before):
    rows = [{"role": "assistant", "content": "中文🙂" * row_size + str(i)} for i in range(57)]
    path = tmp_path / "history.jsonl"
    path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows))
    original_hash = hashlib.sha256(path.read_bytes()).digest()
    expected = _history_page_from_jsonl(path, before=before, limit=limit)
    actual = _history_page_from_jsonl(path, before=before, limit=limit, known_total=len(rows))
    end = min(before, len(rows)) if before > 0 else len(rows)
    assert actual == expected == (rows[max(0, end-limit):end], len(rows))
    assert hashlib.sha256(path.read_bytes()).digest() == original_hash


@pytest.mark.parametrize("before", [0, 5, 50])
@pytest.mark.parametrize("suffix", [b'{"role":', b"not-json\n", b"[]\n", b"\n", b"\xff\n"])
def test_chunked_tail_falls_back_for_stale_counts_or_crash_rows(tmp_path, suffix, before):
    path = tmp_path / "history.jsonl"
    rows = [{"role": "user", "content": str(i)} for i in range(10)]
    path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows) + suffix)
    for known in [len(rows), len(rows) + 1, len(rows) - 1]:
        expected = _history_page_from_jsonl(path, before=before, limit=5)
        assert _history_page_from_jsonl(path, before=before, limit=5, known_total=known) == expected


def test_single_message_larger_than_chunk_keeps_its_beginning(tmp_path):
    rows = [{"role": "assistant", "content": "begin" + "a" * (3 * 1024 * 1024) + "end"}]
    path = tmp_path / "history.jsonl"
    path.write_bytes(json.dumps(rows[0]).encode() + b"\n")
    assert _history_page_from_jsonl(path, before=0, limit=50, known_total=1) == (rows, 1)


def test_older_page_decodes_only_window_and_final_row(tmp_path, monkeypatch):
    rows = [{"role": "user", "content": str(i)} for i in range(10000)]
    path = tmp_path / "history.jsonl"
    path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    decode = json.loads
    calls = []

    def counted(value):
        calls.append(value)
        return decode(value)

    monkeypatch.setattr(json, "loads", counted)
    assert _history_page_from_jsonl(path, before=5000, limit=50, known_total=10000) == (rows[4950:5000], 10000)
    assert len(calls) == 51
