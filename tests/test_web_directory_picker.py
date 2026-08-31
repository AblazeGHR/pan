import asyncio
import os
from pathlib import Path

import pytest


def call(path=None):
    from packages.web.server import list_directories

    return asyncio.run(list_directories(path))


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
