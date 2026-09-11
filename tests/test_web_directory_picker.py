import asyncio
import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def call(path=None, include_files=False):
    from packages.web.server import list_directories

    return asyncio.run(list_directories(path, include_files=include_files))


def test_directory_roots_are_listed_without_recursive_scan(monkeypatch, tmp_path):
    import packages.web.server as server

    root = tmp_path / "server-root"
    root.mkdir()
    (root / "child").mkdir()
    monkeypatch.setattr(server, "_directory_roots", lambda: [root])

    result = call()

    assert result["current"] == ""
    assert result["parent"] is None
    assert result["entries"] == [{"name": root.name, "path": str(root), "isDirectory": True}]


def test_directory_listing_returns_only_direct_child_directories(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "z-dir").mkdir()
    (root / "z-dir" / "nested").mkdir()
    (root / "a-dir").mkdir()
    (root / "file.txt").write_text("not a directory")

    result = call(str(root))

    assert result["current"] == str(root.resolve())
    assert result["parent"] == str(root.resolve().parent)
    assert [entry["name"] for entry in result["entries"]] == ["a-dir", "z-dir"]
    assert all(entry["isDirectory"] for entry in result["entries"])


def test_directory_listing_in_file_mode_includes_files_but_does_not_recurse(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "z-dir").mkdir()
    (root / "z-dir" / "hidden.txt").write_text("nested", encoding="utf-8")
    (root / "a.txt").write_text("附件", encoding="utf-8")

    result = call(str(root), include_files=True)

    assert [(entry["name"], entry["isDirectory"]) for entry in result["entries"]] == [
        ("a.txt", False),
        ("z-dir", True),
    ]
    assert all("hidden.txt" not in entry["name"] for entry in result["entries"])


def test_directory_listing_rejects_missing_and_non_directory(tmp_path):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as missing:
        call(str(tmp_path / "missing"))
    assert missing.value.status_code == 404

    file_path = tmp_path / "file"
    file_path.write_text("x")
    with pytest.raises(HTTPException) as not_dir:
        call(str(file_path))
    assert not_dir.value.status_code == 400


def test_directory_listing_reports_permission_error(monkeypatch, tmp_path):
    import packages.web.server as server

    directory = tmp_path / "restricted"
    directory.mkdir()
    original_scandir = os.scandir

    def denied(path):
        if Path(path) == directory:
            raise PermissionError("denied")
        return original_scandir(path)

    monkeypatch.setattr(server.os, "scandir", denied)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        call(str(directory))
    assert error.value.status_code == 403


def test_directory_creation_is_explicit_and_stays_inside_the_workdir_root(monkeypatch, tmp_path):
    import packages.web.server as server
    from fastapi import HTTPException

    workdirs = tmp_path / "workdirs"
    workdirs.mkdir()
    monkeypatch.setattr(server, "WORKDIRS_DIR", workdirs)
    monkeypatch.setattr(server, "_ALLOWED_WORKDIR_ROOTS", None)

    created = asyncio.run(server.create_directory({"path": str(workdirs / "confirmed") }))
    assert created == {"ok": True, "path": str((workdirs / "confirmed").resolve())}
    assert (workdirs / "confirmed").is_dir()

    outside = tmp_path / "outside"
    with pytest.raises(HTTPException) as denied:
        asyncio.run(server.create_directory({"path": str(outside)}))
    assert denied.value.status_code == 400
    assert not outside.exists()

    with pytest.raises(HTTPException) as traversal:
        asyncio.run(server.create_directory({"path": ".."}))
    assert traversal.value.status_code == 400


def test_absolute_workdir_creation_is_not_implicit(monkeypatch, tmp_path):
    import packages.web.server as server

    monkeypatch.setattr(server, "_ALLOWED_WORKDIR_ROOTS", [tmp_path])
    with pytest.raises(ValueError, match="confirm directory creation first"):
        server._resolve_workdir(str(tmp_path / "missing"))

    created = asyncio.run(server.create_directory({"path": str(tmp_path / "confirmed") }))
    assert created["ok"] is True
    assert server._resolve_workdir(str(tmp_path / "confirmed")) == (tmp_path / "confirmed").resolve()


def test_attachment_upload_is_session_isolated_and_avoids_name_collisions(monkeypatch, tmp_path):
    import packages.web.server as server

    monkeypatch.setattr(server, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(server.sess, "get", lambda session_id: object() if session_id in {"ses_a", "ses_b"} else None)

    async def upload(session_id, body, filename):
        from urllib.parse import quote
        from starlette.requests import Request

        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({
            "type": "http",
            "method": "POST",
            "path": f"/api/sessions/{session_id}/attachments",
            "headers": [(b"x-filename", quote(filename).encode("ascii"))],
        }, receive)
        return await server.upload_session_attachment(session_id, request)

    first = asyncio.run(upload("ses_a", b"one", r"C:\fakepath\需求说明 [v1](最终).md"))
    second = asyncio.run(upload("ses_a", b"two", r"C:\fakepath\需求说明 [v1](最终).md"))
    other_session = asyncio.run(upload("ses_b", b"three", "same.txt"))

    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == b"one"
    assert Path(second["path"]).read_bytes() == b"two"
    assert Path(first["path"]).parent != Path(other_session["path"]).parent
    assert Path(other_session["path"]).read_bytes() == b"three"
    assert "fakepath" not in first["path"]
    assert first["displayName"] == "需求说明 [v1](最终).md"
    assert first["filename"] == first["displayName"]
    assert first["storageFilename"].startswith("upload_")
    assert first["storageFilename"] != first["displayName"]
    assert first["href"] == (
        f"/api/attachments/{first['storageFilename']}?session_id=ses_a"
    )
    assert first["displayName"] not in first["href"]
    assert server._attachment_markdown(first["displayName"], first["href"]) == (
        r"[需求说明 \[v1\]\(最终\).md]("
        + first["href"]
        + ")"
    )


def test_legacy_attachment_history_gets_markdown_fallback_without_touching_normal_links(tmp_path, monkeypatch):
    import packages.web.server as server

    monkeypatch.setattr(server, "ATTACHMENTS_DIR", tmp_path / "attachments")
    history = [{
        "role": "user",
        "content": (
            '请看 @"D:\\old\\upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md" '
            "以及 [普通链接](https://example.test/a_(b))"
        ),
    }]

    normalized = server._api_history("ses_a", history)

    assert normalized[0]["content"] == (
        "请看 [upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md]"
        "(/api/fs/read?session_id=ses_a&path=D%3A%5Cold%5Cupload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md&download=1) "
        "以及 [普通链接](https://example.test/a_(b))"
    )


def test_uploaded_attachment_route_is_session_scoped_and_rejects_path_input(monkeypatch, tmp_path):
    import packages.web.server as server
    from fastapi import HTTPException

    monkeypatch.setattr(server, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(server.sess, "get", lambda session_id: object() if session_id == "ses_a" else None)
    storage = "upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md"
    target_dir = server._attachment_session_dir("ses_a")
    target_dir.mkdir(parents=True)
    (target_dir / storage).write_text("body", encoding="utf-8")

    response = asyncio.run(server.download_session_attachment(storage, "ses_a"))
    assert response.path == target_dir / storage

    with pytest.raises(HTTPException) as traversal:
        asyncio.run(server.download_session_attachment("../" + storage, "ses_a"))
    assert traversal.value.status_code == 400

    with pytest.raises(HTTPException) as wrong_session:
        asyncio.run(server.download_session_attachment(storage, "ses_b"))
    assert wrong_session.value.status_code == 404


def test_attachment_upload_rejects_unknown_session(monkeypatch, tmp_path):
    import packages.web.server as server
    from fastapi import HTTPException

    monkeypatch.setattr(server, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(server.sess, "get", lambda _session_id: None)

    async def run():
        from starlette.requests import Request

        async def receive():
            return {"type": "http.request", "body": b"data", "more_body": False}

        request = Request({"type": "http", "method": "POST", "path": "/attachments", "headers": []}, receive)
        return await server.upload_session_attachment("ses_missing", request, "file.txt")

    with pytest.raises(HTTPException) as error:
        asyncio.run(run())
    assert error.value.status_code == 404


def test_fs_rename_does_not_overwrite_target_that_appears_during_operation(monkeypatch, tmp_path):
    import packages.web.server as server

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    source = workdir / "old.txt"
    target = workdir / "new.txt"
    source.write_text("source", encoding="utf-8")
    monkeypatch.setattr(server.sess, "get", lambda _session_id: SimpleNamespace(workdir=str(workdir)))
    real_rename = server._rename_no_overwrite

    def target_appears_then_rename(src, dst):
        # Model the target being created after path checks but before the
        # filesystem rename commits.
        dst.write_text("target draft", encoding="utf-8")
        return real_rename(src, dst)

    monkeypatch.setattr(server, "_rename_no_overwrite", target_appears_then_rename)
    result = asyncio.run(server.api_fs_rename({
        "session_id": "ses_editor",
        "from": "old.txt",
        "to": "new.txt",
    }))

    assert "error" in result
    assert source.read_text(encoding="utf-8") == "source"
    assert target.read_text(encoding="utf-8") == "target draft"


def test_fs_rename_keeps_normal_file_rename_behavior(monkeypatch, tmp_path):
    import packages.web.server as server

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "old.txt").write_text("content", encoding="utf-8")
    monkeypatch.setattr(server.sess, "get", lambda _session_id: SimpleNamespace(workdir=str(workdir)))

    result = asyncio.run(server.api_fs_rename({
        "session_id": "ses_editor",
        "from": "old.txt",
        "to": "new.txt",
    }))

    assert result == {"from": "old.txt", "to": "new.txt"}
    assert not (workdir / "old.txt").exists()
    assert (workdir / "new.txt").read_text(encoding="utf-8") == "content"


def test_linux_renameat2_enosys_fails_closed_without_link_unlink(monkeypatch, tmp_path):
    import packages.web.server as server

    source = tmp_path / "old.txt"
    target = tmp_path / "new.txt"
    source.write_text("content", encoding="utf-8")

    class RenameAt2:
        argtypes = None
        restype = None

        def __call__(self, *_args):
            return -1

    class FakeLibc:
        renameat2 = RenameAt2()

    monkeypatch.setattr(server.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc())
    monkeypatch.setattr(server.ctypes, "get_errno", lambda: errno.ENOSYS)
    monkeypatch.setattr(server.os, "name", "posix")
    monkeypatch.setattr(server.sys, "platform", "linux")

    def forbidden_fallback(*_args, **_kwargs):
        raise AssertionError("unsafe link+unlink fallback must not run")

    monkeypatch.setattr(server.os, "link", forbidden_fallback)
    monkeypatch.setattr(Path, "unlink", forbidden_fallback)
    with pytest.raises(OSError) as error:
        server._rename_no_overwrite(source, target)

    assert error.value.errno == errno.ENOTSUP
    assert source.read_text(encoding="utf-8") == "content"
    assert not target.exists()


def test_directory_rename_fails_closed_when_atomic_no_overwrite_is_unavailable(monkeypatch, tmp_path):
    import packages.web.server as server

    source = tmp_path / "old-dir"
    target = tmp_path / "new-dir"
    source.mkdir()
    monkeypatch.setattr(server.os, "name", "posix")
    monkeypatch.setattr(server.sys, "platform", "darwin")

    with pytest.raises(OSError) as error:
        server._rename_no_overwrite(source, target)

    assert error.value.errno == errno.ENOTSUP
    assert source.is_dir()
    assert not target.exists()


def test_fs_rename_same_existing_path_is_a_safe_noop(monkeypatch, tmp_path):
    import packages.web.server as server

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    source = workdir / "same.txt"
    source.write_text("content", encoding="utf-8")
    monkeypatch.setattr(server.sess, "get", lambda _session_id: SimpleNamespace(workdir=str(workdir)))

    result = asyncio.run(server.api_fs_rename({
        "session_id": "ses_editor",
        "from": "same.txt",
        "to": "same.txt",
    }))

    assert result == {"from": "same.txt", "to": "same.txt"}
    assert source.read_text(encoding="utf-8") == "content"


def test_fs_rename_same_missing_path_is_an_error(monkeypatch, tmp_path):
    import packages.web.server as server

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.setattr(server.sess, "get", lambda _session_id: SimpleNamespace(workdir=str(workdir)))

    result = asyncio.run(server.api_fs_rename({
        "session_id": "ses_editor",
        "from": "missing.txt",
        "to": "missing.txt",
    }))

    assert "error" in result
    assert "missing.txt" in result["error"]
