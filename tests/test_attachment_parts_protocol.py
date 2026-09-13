"""Structured AttachmentRef/parts contract tests (no protected service ports)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess  # noqa: E402
import packages.web.server as srv  # noqa: E402


def _setup(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(srv, "ATTACHMENTS_DIR", tmp_path / "attachments")
    _sess._cache.clear()
    first = _sess.Session(id="ses_parts_a", name="A", workdir=str(tmp_path))
    second = _sess.Session(id="ses_parts_b", name="B", workdir=str(tmp_path))
    _sess._cache[first.id] = first
    _sess._cache[second.id] = second
    return first, second


async def _noop_save_async(_session):
    return None


def test_structured_parts_canonicalize_and_preserve_queue_shape(monkeypatch, tmp_path):
    first, _second = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    monkeypatch.setattr(srv.worker, "_schedule_session_recovery", lambda _sid: None)
    target = srv._attachment_session_dir(first.id)
    target.mkdir(parents=True)
    stored = target / "upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt"
    stored.write_text("body", encoding="utf-8")
    srv._register_attachment(first.id, stored.name, {
        "source": "upload", "displayName": "真实名称.txt", "storageFilename": stored.name,
        "path": str(stored), "size": 4, "mimeType": "text/plain",
    })

    result = asyncio.run(srv.api_session_queue_enqueue(first.id, {
        "text": "client text must not win",
        "parts": [
            {"type": "text", "text": "前置 "},
            {"type": "attachment", "attachmentId": stored.name,
             "displayName": "伪造名称.txt", "href": "C:/client/path.txt"},
            {"type": "text", "text": " 后置"},
        ],
        "clientMessageId": "parts-1",
    }))
    assert result["ok"] is True
    item = first.queue_pending[0]
    assert item["parts"][1]["attachmentId"] == stored.name
    assert item["parts"][1]["displayName"] == "真实名称.txt"
    assert item["text"] == (
        f"前置 [真实名称.txt](/api/attachments/{stored.name}?session_id={first.id}) 后置"
    )
    assert result["item"]["parts"] == item["parts"]


def test_structured_parts_reject_cross_session_stale_and_incomplete(monkeypatch, tmp_path):
    first, second = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    monkeypatch.setattr(srv.worker, "_schedule_session_recovery", lambda _sid: None)
    second_dir = srv._attachment_session_dir(second.id)
    second_dir.mkdir(parents=True)
    foreign = second_dir / "upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.txt"
    foreign.write_text("foreign", encoding="utf-8")
    srv._register_attachment(second.id, foreign.name, {
        "source": "upload", "displayName": "foreign.txt", "storageFilename": foreign.name,
        "path": str(foreign), "size": 7,
    })

    cross = asyncio.run(srv.api_session_queue_enqueue(first.id, {
        "parts": [{"type": "attachment", "attachmentId": foreign.name}],
    }))
    assert cross["error"]["code"] == "attachment_session_mismatch"

    incomplete = "att_" + "c" * 32
    srv._register_attachment(first.id, incomplete, {
        "source": "server_file", "displayName": "pending.txt",
        "path": str(tmp_path / "pending.txt"), "completed": False,
    })
    pending = asyncio.run(srv.api_session_queue_enqueue(first.id, {
        "parts": [{"type": "attachment", "attachmentId": incomplete}],
    }))
    assert pending["error"]["code"] == "attachment_incomplete"

    stale = "att_" + "d" * 32
    srv._register_attachment(first.id, stale, {
        "source": "server_file", "displayName": "gone.txt",
        "path": str(tmp_path / "gone.txt"),
    })
    missing = asyncio.run(srv.api_session_queue_enqueue(first.id, {
        "parts": [{"type": "attachment", "attachmentId": stale}],
    }))
    assert missing["error"]["code"] == "attachment_not_found"


def test_server_file_registration_rejects_directory(monkeypatch, tmp_path):
    first, _second = _setup(tmp_path, monkeypatch)
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(HTTPException) as error:
        asyncio.run(srv.register_server_file_attachment(first.id, {"path": str(directory)}))
    assert error.value.status_code == 400
    assert "Directories" in str(error.value.detail)
