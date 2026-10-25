"""Editor: open one file with the system default application.

The endpoint accepts a path only. These tests pin the containment rules, the
per-platform opener selection, and the failure codes the Editor UI surfaces.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def open_external(server, session_id="ses_editor", path="file.txt"):
    return asyncio.run(
        server.api_editor_open_external({"session_id": session_id, "path": path})
    )


def session_with_workdir(monkeypatch, server, workdir):
    # Session metadata must also satisfy effective_workspace_ids: a real root
    # (no managed_by) with no Workspace membership.
    def fake_get(session_id, **_kwargs):
        return SimpleNamespace(
            id=session_id,
            workdir=str(workdir) if workdir is not None else None,
            managed_by=None,
            workspace_ids=[],
        )

    monkeypatch.setattr(server.sess, "get", fake_get)


@pytest.fixture
def workdir(tmp_path):
    directory = tmp_path / "workdir"
    directory.mkdir()
    return directory


@pytest.fixture
def opened(monkeypatch):
    import packages.web.server as server

    calls: list[Path] = []
    monkeypatch.setattr(
        server, "_open_file_with_system_default", lambda target: calls.append(target)
    )
    return calls


def test_opens_an_existing_file_inside_the_session_workdir(monkeypatch, workdir, opened):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "notes.md"
    target.write_text("hello", encoding="utf-8")

    result = open_external(server, path="notes.md")

    assert result == {"ok": True, "name": "notes.md"}
    assert opened == [target.resolve()]


def test_accepts_an_absolute_file_inside_the_session_workdir(monkeypatch, workdir, opened):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "sub" / "notes.md"
    target.parent.mkdir()
    target.write_text("hello", encoding="utf-8")

    result = open_external(server, path=str(target))

    assert result["ok"] is True
    assert opened == [target.resolve()]


def test_rejects_a_directory(monkeypatch, workdir, opened):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "folder").mkdir()

    with pytest.raises(HTTPException) as error:
        open_external(server, path="folder")

    assert error.value.status_code == 400
    assert "Not a file" in error.value.detail
    assert opened == []


def test_rejects_a_missing_file(monkeypatch, workdir, opened):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)

    with pytest.raises(HTTPException) as error:
        open_external(server, path="missing.md")

    assert error.value.status_code == 404
    assert "missing.md" in error.value.detail
    assert str(workdir) not in error.value.detail
    assert opened == []


def test_rejects_relative_traversal(monkeypatch, workdir, tmp_path, opened):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        open_external(server, path="../outside.txt")

    assert error.value.status_code == 403
    assert opened == []


def test_rejects_an_absolute_path_outside_the_allowed_roots(monkeypatch, workdir, tmp_path, opened):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        open_external(server, path=str(outside))

    assert error.value.status_code == 403
    assert "workspace roots" in error.value.detail
    assert opened == []


def make_dir_link(link: Path, target: Path) -> None:
    """Create a directory link; Windows falls back to a junction (no admin)."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("this host cannot create directory symlinks")
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    if created.returncode != 0:
        pytest.skip("this host cannot create directory junctions")


def test_rejects_a_link_that_escapes_the_workdir(monkeypatch, tmp_path, workdir, opened):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    make_dir_link(workdir / "link", outside)

    with pytest.raises(HTTPException) as error:
        open_external(server, path="link/secret.txt")

    assert error.value.status_code == 403
    assert opened == []


def test_allows_a_link_that_stays_inside_the_workdir(monkeypatch, workdir, opened):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    real = workdir / "real"
    real.mkdir()
    target = real / "file.txt"
    target.write_text("hello", encoding="utf-8")
    make_dir_link(workdir / "link", real)

    result = open_external(server, path="link/file.txt")

    assert result["ok"] is True
    assert opened == [target.resolve()]


def test_allows_a_file_inside_a_workspace_dir(monkeypatch, workdir, tmp_path, opened):
    import packages.web.server as server

    shared = tmp_path / "shared"
    shared.mkdir()
    target = shared / "report.md"
    target.write_text("shared", encoding="utf-8")
    session_with_workdir(monkeypatch, server, workdir)
    monkeypatch.setattr(server.sess, "effective_workspace_ids", lambda _sid: ["ws_1"])
    monkeypatch.setattr(
        server.workspaces, "get", lambda _wid: SimpleNamespace(dirs=[str(shared)])
    )

    result = open_external(server, path=str(target))

    assert result["ok"] is True
    assert opened == [target.resolve()]


def test_rejects_every_path_when_the_session_has_no_roots(monkeypatch, tmp_path, opened):
    import packages.web.server as server

    target = tmp_path / "file.txt"
    target.write_text("hello", encoding="utf-8")
    session_with_workdir(monkeypatch, server, None)

    with pytest.raises(HTTPException) as error:
        open_external(server, path=str(target))

    assert error.value.status_code == 403
    assert opened == []


def test_requires_a_session_and_a_path(opened):
    import packages.web.server as server

    with pytest.raises(HTTPException) as no_session:
        asyncio.run(server.api_editor_open_external({"path": "file.txt"}))
    assert no_session.value.status_code == 400

    with pytest.raises(HTTPException) as no_path:
        asyncio.run(server.api_editor_open_external({"session_id": "ses_editor"}))
    assert no_path.value.status_code == 400

    assert opened == []


def test_opener_command_selection_per_platform(monkeypatch, tmp_path):
    import packages.web.server as server

    target = tmp_path / "file.txt"

    assert server._external_opener_argv(target, "darwin") == ["open", str(target)]
    assert server._external_opener_argv(target, "win32") is None
    assert server._external_opener_argv(target, "freebsd") is None

    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/xdg-open")
    assert server._external_opener_argv(target, "linux") == [
        "/usr/bin/xdg-open",
        str(target),
    ]

    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    assert server._external_opener_argv(target, "linux") is None


def test_returns_501_when_no_system_opener_is_available(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.shutil, "which", lambda name: None)

    with pytest.raises(HTTPException) as error:
        open_external(server)

    assert error.value.status_code == 501
    assert "no system file opener" in error.value.detail


def test_returns_501_on_an_unsupported_platform(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(server.sys, "platform", "freebsd")

    with pytest.raises(HTTPException) as error:
        open_external(server)

    assert error.value.status_code == 501


def test_windows_uses_startfile(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "file.txt"
    target.write_text("hello", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setattr(server.os, "startfile", opened.append, raising=False)

    result = open_external(server)

    assert result["ok"] is True
    assert opened == [str(target.resolve())]


def test_launches_the_opener_argv_without_a_shell(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "file.txt"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/xdg-open")

    launched: list[tuple[list[str], dict]] = []

    class Process:
        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **kwargs):
        launched.append((list(argv), kwargs))
        return Process()

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)

    result = open_external(server)

    assert result["ok"] is True
    assert launched == [
        (
            ["/usr/bin/xdg-open", str(target.resolve())],
            {
                "stdin": server.subprocess.DEVNULL,
                "stdout": server.subprocess.DEVNULL,
                "stderr": server.subprocess.DEVNULL,
                "close_fds": True,
                "start_new_session": True,
            },
        )
    ]
    assert "shell" not in launched[0][1]


def test_reports_a_nonzero_opener_exit(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(server.sys, "platform", "darwin")

    class Process:
        def wait(self, timeout=None):
            return 3

    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **k: Process())

    with pytest.raises(HTTPException) as error:
        open_external(server)

    assert error.value.status_code == 500
    assert "status 3" in error.value.detail


def test_reports_a_missing_opener_binary(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(server.sys, "platform", "darwin")

    def missing_binary(*_args, **_kwargs):
        raise FileNotFoundError("open")

    monkeypatch.setattr(server.subprocess, "Popen", missing_binary)

    with pytest.raises(HTTPException) as error:
        open_external(server)

    assert error.value.status_code == 501


def test_route_maps_success_and_rejection_to_http_status(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import packages.web.server as server
    from packages.core import session as sess

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    session = sess.create(name="editor-open-external", adapter="cbc", workdir=str(workdir))
    opened: list[Path] = []
    monkeypatch.setattr(
        server, "_open_file_with_system_default", lambda target: opened.append(target)
    )

    with TestClient(server.app) as client:
        success = client.post(
            "/api/editor/open-external",
            json={"session_id": session.id, "path": "file.txt"},
        )
        outside = client.post(
            "/api/editor/open-external",
            json={"session_id": session.id, "path": str(tmp_path / "outside.txt")},
        )

    assert success.status_code == 200
    assert success.json() == {"ok": True, "name": "file.txt"}
    assert outside.status_code == 403
    assert opened == [(workdir / "file.txt").resolve()]


def test_a_still_running_opener_is_not_treated_as_failure(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(server.sys, "platform", "darwin")

    class Process:
        def wait(self, timeout=None):
            if timeout is not None:
                raise server.subprocess.TimeoutExpired(cmd="open", timeout=timeout)
            return 0

    reapers: list[object] = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(server, "_reap_external_opener", reapers.append)

    result = open_external(server)

    assert result["ok"] is True
    assert len(reapers) == 1
