"""Structured AttachmentRef/parts contract tests (no protected service ports)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess  # noqa: E402
from packages.core.attachment_projection import (  # noqa: E402
    AttachmentProjectionError,
    project_message_parts,
)
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
    assert result["item"]["parts"][1] == {
        key: value for key, value in item["parts"][1].items() if key != "__serverPath"
    }
    assert "__serverPath" not in result["item"]["parts"][1]


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
    assert cross["ok"] is True
    assert cross["item"]["parts"][0]["attachmentId"] == foreign.name
    assert not (srv._attachment_session_dir(first.id) / foreign.name).exists()

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


def test_worker_projection_uses_canonical_path_and_never_api_href(tmp_path):
    target = tmp_path / "real file.txt"
    target.write_text("body", encoding="utf-8")
    projected = project_message_parts([
        {"type": "text", "text": "请读取 "},
        {
            "type": "attachment",
            "displayName": "client-name.txt",
            "href": "/api/fs/read?path=C%3A%2Fclient%2Ffake.txt&download=1",
            "__serverPath": str(target),
        },
    ], "fallback")
    assert projected == f'请读取 @"{target.resolve()}"'
    assert "/api/" not in projected
    with pytest.raises(AttachmentProjectionError):
        project_message_parts([{
            "type": "attachment", "displayName": "gone.txt",
            "__serverPath": str(tmp_path / "gone.txt"),
        }], "fallback")


def test_cross_session_server_reference_is_imported_without_copying_bytes(monkeypatch, tmp_path):
    first, second = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    monkeypatch.setattr(srv.worker, "_schedule_session_recovery", lambda _sid: None)
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    attachment_id = "att_" + "e" * 32
    srv._register_attachment(first.id, attachment_id, {
        "source": "server_file", "displayName": "source.md", "path": str(source),
        "size": source.stat().st_size, "mimeType": "text/markdown",
    })

    result = asyncio.run(srv.api_session_queue_enqueue(second.id, {
        "text": "ignored",
        "parts": [{"type": "attachment", "attachmentId": attachment_id}],
    }))

    assert result["ok"] is True
    part = second.queue_pending[0]["parts"][0]
    assert part["__serverPath"] == str(source.resolve())
    assert not (srv._attachment_session_dir(second.id) / source.name).exists()
    assert second.queue_pending[0]["text"] == (
        f"[source.md](/api/attachments/ref/{attachment_id}?session_id={first.id})"
    )


def test_legacy_markdown_attachment_is_upgraded_for_worker_projection(monkeypatch, tmp_path):
    first, _second = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    source = tmp_path / "legacy file.md"
    source.write_text("legacy", encoding="utf-8")
    href = srv._fs_download_href(first.id, str(source))

    result = asyncio.run(srv.api_session_queue_enqueue(first.id, {
        "text": f"请读取 [旧链接]({href})",
    }))

    assert result["ok"] is True
    item = first.queue_pending[0]
    assert item["parts"][0] == {"type": "text", "text": "请读取 "}
    assert item["parts"][1]["type"] == "attachment"
    assert item["parts"][1]["__serverPath"] == str(source.resolve())
    assert "/api/fs/read" not in item["text"]


def test_unknown_cross_session_reference_is_rejected(monkeypatch, tmp_path):
    first, _second = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    result = asyncio.run(srv.api_session_queue_enqueue(first.id, {
        "parts": [{"type": "attachment", "attachmentId": "att_" + "0" * 32}],
    }))
    assert result["ok"] is False
    assert result["error"]["code"] == "attachment_not_found"


def test_queue_edit_cannot_make_text_disagree_with_parts(monkeypatch, tmp_path):
    first, _second = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(_sess, "save_async", _noop_save_async)
    monkeypatch.setattr(srv.worker, "_schedule_session_recovery", lambda _sid: None)
    # Keep the path construction explicit on Windows; this test is intended
    # to cover only the queue contract.
    stored = srv._attachment_session_dir(first.id) / ("upload_" + "f" * 32 + ".txt")
    stored.parent.mkdir(parents=True)
    stored.write_text("body", encoding="utf-8")
    srv._register_attachment(first.id, stored.name, {
        "source": "upload", "displayName": "file.txt", "storageFilename": stored.name,
        "path": str(stored), "size": 4, "mimeType": "text/plain",
    })
    result = asyncio.run(srv.api_session_queue_enqueue(first.id, {
        "parts": [{"type": "attachment", "attachmentId": stored.name}],
    }))
    item_id = result["item"]["id"]
    conflict = asyncio.run(srv.api_session_queue_update(first.id, item_id, {
        "text": "different", "expectedRevision": 1,
    }))
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "parts_text_conflict"


def test_history_projects_existing_local_link_to_opaque_editor_reference(monkeypatch, tmp_path):
    first, _second = _setup(tmp_path, monkeypatch)
    source = tmp_path / "docs" / "readme.md"
    source.parent.mkdir()
    source.write_text("one\ntwo\nthree\nfour", encoding="utf-8")
    projected = srv._api_history(first.id, [{
        "role": "assistant",
        "content": "See [readme](docs/readme.md#L2-L3) and [web](https://example.test)",
    }])
    content = projected[0]["content"]
    assert "/api/attachments/editor/att_" in content
    assert "#L2-L3" in content
    assert "docs/readme.md" not in content
    assert "https://example.test" in content


def test_attachment_source_rejects_relative_workdir_escape(tmp_path):
    session = _sess.Session(id="ses_escape", name="Escape", workdir=str(tmp_path / "work"))
    session.workdir and Path(session.workdir).mkdir()
    _sess._cache[session.id] = session
    with pytest.raises(ValueError, match="escapes"):
        srv._resolve_attachment_source_path(session.id, "..\\outside.txt")


def test_editor_destination_parser_covers_paths_ranges_and_external_urls():
    assert srv._parse_editor_destination("docs/My%20File.md#L2-L4") == (
        "docs/My File.md", 2, 4,
    )
    assert srv._parse_editor_destination("C:%5Cwork%5Creadme.md:7") == (
        "C:/work/readme.md", 7, None,
    )
    assert srv._parse_editor_destination("/D:/work/readme.md#L3") == (
        "D:/work/readme.md", 3, None,
    )
    assert srv._parse_editor_destination("file://server/share/readme.md#L5") == (
        "//server/share/readme.md", 5, None,
    )
    assert srv._parse_editor_destination("https://example.test/readme.md#L5") is None
    assert srv._parse_editor_destination("http://example.test/readme.md") is None


def test_text_fallback_cannot_use_a_foreign_session_attachment_link(monkeypatch, tmp_path):
    first, second = _setup(tmp_path, monkeypatch)
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    attachment_id = "att_" + "1" * 32
    srv._register_attachment(first.id, attachment_id, {
        "source": "server_file", "displayName": "source.md", "path": str(source),
        "size": source.stat().st_size, "mimeType": "text/markdown",
    })
    foreign_href = f"/api/attachments/editor/{attachment_id}?session_id={first.id}#L1"
    assert srv._attachment_reference_error(second.id, foreign_href) == {
        "code": "attachment_session_mismatch",
        "message": "Attachment belongs to another session",
    }
