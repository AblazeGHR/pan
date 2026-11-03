"""Editor: reveal one file in the platform file manager.

The request carries a path, never a command. These tests pin the same
containment rules as ``/api/editor/open-external``, the fixed per-platform
argv (Windows Explorer select, macOS ``open -R``, Linux ``xdg-open`` on the
parent directory), and the explicit unsupported/error codes.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from packages.core.platform import file_manager


def reveal_external(server, session_id="ses_editor", path="file.txt"):
    return asyncio.run(
        server.api_editor_reveal_external({"session_id": session_id, "path": path})
    )


def session_with_workdir(monkeypatch, server, workdir):
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
def revealed(monkeypatch):
    import packages.web.server as server

    calls: list[Path] = []
    monkeypatch.setattr(
        server.file_manager,
        "reveal_file_in_file_manager",
        lambda target: calls.append(target),
    )
    return calls


def test_reveals_an_existing_file_inside_the_session_workdir(monkeypatch, workdir, revealed):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "notes.md"
    target.write_text("hello", encoding="utf-8")

    result = reveal_external(server, path="notes.md")

    assert result == {"ok": True, "name": "notes.md"}
    assert revealed == [target.resolve()]


def test_accepts_an_absolute_file_inside_the_session_workdir(monkeypatch, workdir, revealed):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "sub" / "notes.md"
    target.parent.mkdir()
    target.write_text("hello", encoding="utf-8")

    result = reveal_external(server, path=str(target))

    assert result["ok"] is True
    assert revealed == [target.resolve()]


def test_rejects_a_directory(monkeypatch, workdir, revealed):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "folder").mkdir()

    with pytest.raises(HTTPException) as error:
        reveal_external(server, path="folder")

    assert error.value.status_code == 400
    assert "Not a file" in error.value.detail
    assert revealed == []


def test_rejects_a_missing_file(monkeypatch, workdir, revealed):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)

    with pytest.raises(HTTPException) as error:
        reveal_external(server, path="missing.md")

    assert error.value.status_code == 404
    assert "missing.md" in error.value.detail
    assert str(workdir) not in error.value.detail
    assert revealed == []


def test_rejects_relative_traversal(monkeypatch, workdir, tmp_path, revealed):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        reveal_external(server, path="../outside.txt")

    assert error.value.status_code == 403
    assert revealed == []


def test_rejects_an_absolute_path_outside_the_allowed_roots(monkeypatch, workdir, tmp_path, revealed):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        reveal_external(server, path=str(outside))

    assert error.value.status_code == 403
    assert "workspace roots" in error.value.detail
    assert revealed == []


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


def test_rejects_a_link_that_escapes_the_workdir(monkeypatch, tmp_path, workdir, revealed):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    make_dir_link(workdir / "link", outside)

    with pytest.raises(HTTPException) as error:
        reveal_external(server, path="link/secret.txt")

    assert error.value.status_code == 403
    assert revealed == []


def test_allows_a_link_that_stays_inside_the_workdir(monkeypatch, workdir, revealed):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    real = workdir / "real"
    real.mkdir()
    target = real / "file.txt"
    target.write_text("hello", encoding="utf-8")
    make_dir_link(workdir / "link", real)

    result = reveal_external(server, path="link/file.txt")

    assert result["ok"] is True
    assert revealed == [target.resolve()]


def test_allows_a_file_inside_a_workspace_dir(monkeypatch, workdir, tmp_path, revealed):
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

    result = reveal_external(server, path=str(target))

    assert result["ok"] is True
    assert revealed == [target.resolve()]


def test_rejects_every_path_when_the_session_has_no_roots(monkeypatch, tmp_path, revealed):
    import packages.web.server as server

    target = tmp_path / "file.txt"
    target.write_text("hello", encoding="utf-8")
    session_with_workdir(monkeypatch, server, None)

    with pytest.raises(HTTPException) as error:
        reveal_external(server, path=str(target))

    assert error.value.status_code == 403
    assert revealed == []


def test_requires_a_session_and_a_path(revealed):
    import packages.web.server as server

    with pytest.raises(HTTPException) as no_session:
        asyncio.run(server.api_editor_reveal_external({"path": "file.txt"}))
    assert no_session.value.status_code == 400

    with pytest.raises(HTTPException) as no_path:
        asyncio.run(server.api_editor_reveal_external({"session_id": "ses_editor"}))
    assert no_path.value.status_code == 400

    assert revealed == []


def test_reveal_argv_selection_per_platform(monkeypatch, tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("hello", encoding="utf-8")

    assert file_manager.reveal_argv(target, "darwin") == [
        "open",
        "-R",
        str(target),
    ]
    assert file_manager.reveal_argv(target, "win32") is None
    assert file_manager.reveal_argv(target, "freebsd") is None

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: "/usr/bin/xdg-open")
    assert file_manager.reveal_argv(target, "linux") == [
        "/usr/bin/xdg-open",
        str(target.parent),
    ]

    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert file_manager.reveal_argv(target, "linux") is None

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: None)
    assert file_manager.reveal_argv(target, "linux") is None


def test_windows_uses_explorer_select_without_a_shell(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "file.txt"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "windows")
    monkeypatch.setattr(
        file_manager, "_explorer_path", lambda: r"C:\Windows\explorer.exe"
    )

    launched: list[tuple[list[str], dict]] = []

    class Process:
        def wait(self, timeout=None):
            # Explorer reports a nonzero status even when the window opens.
            return 1

    def fake_popen(argv, **kwargs):
        launched.append((list(argv), kwargs))
        return Process()

    monkeypatch.setattr(file_manager.subprocess, "Popen", fake_popen)

    result = reveal_external(server)

    assert result["ok"] is True
    assert launched == [
        (
            [r"C:\Windows\explorer.exe", "/select,", str(target.resolve())],
            {
                "stdin": file_manager.subprocess.DEVNULL,
                "stdout": file_manager.subprocess.DEVNULL,
                "stderr": file_manager.subprocess.DEVNULL,
                "close_fds": True,
                "start_new_session": True,
            },
        )
    ]
    assert "shell" not in launched[0][1]


def test_windows_requires_explorer(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "windows")
    monkeypatch.setattr(file_manager, "_explorer_path", lambda: None)
    monkeypatch.setattr(
        file_manager.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("must not launch without Explorer"),
    )

    with pytest.raises(HTTPException) as error:
        reveal_external(server)

    assert error.value.status_code == 501
    assert "file manager" in error.value.detail


def test_macos_reveals_with_open_select(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "file.txt"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "darwin")
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: "/usr/bin/open")

    launched: list[list[str]] = []

    class Process:
        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **kwargs):
        launched.append(list(argv))
        return Process()

    monkeypatch.setattr(file_manager.subprocess, "Popen", fake_popen)

    result = reveal_external(server)

    assert result["ok"] is True
    assert launched == [["open", "-R", str(target.resolve())]]


def test_linux_reveals_the_parent_directory(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    target = workdir / "file.txt"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: "/usr/bin/xdg-open")

    launched: list[list[str]] = []

    class Process:
        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **kwargs):
        launched.append(list(argv))
        return Process()

    monkeypatch.setattr(file_manager.subprocess, "Popen", fake_popen)

    result = reveal_external(server)

    assert result["ok"] is True
    assert launched == [["/usr/bin/xdg-open", str(target.resolve().parent)]]


def test_returns_501_without_an_opener_or_desktop(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: "/usr/bin/xdg-open")

    with pytest.raises(HTTPException) as error:
        reveal_external(server)

    assert error.value.status_code == 501
    assert "file manager" in error.value.detail

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: None)
    with pytest.raises(HTTPException) as missing:
        reveal_external(server)

    assert missing.value.status_code == 501


def test_returns_501_on_an_unsupported_platform(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "freebsd")

    with pytest.raises(HTTPException) as error:
        reveal_external(server)

    assert error.value.status_code == 501


def test_returns_500_when_the_reveal_opener_fails(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "darwin")
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: "/usr/bin/open")

    class Process:
        def wait(self, timeout=None):
            return 3

    monkeypatch.setattr(file_manager.subprocess, "Popen", lambda *a, **k: Process())

    with pytest.raises(HTTPException) as error:
        reveal_external(server)

    assert error.value.status_code == 500
    assert "status 3" in error.value.detail


def test_reports_a_missing_opener_binary(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "darwin")
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: "/usr/bin/open")

    def missing_binary(*_args, **_kwargs):
        raise FileNotFoundError("open")

    monkeypatch.setattr(file_manager.subprocess, "Popen", missing_binary)

    with pytest.raises(HTTPException) as error:
        reveal_external(server)

    assert error.value.status_code == 501


def test_a_still_running_reveal_opener_is_not_treated_as_failure(monkeypatch, workdir):
    import packages.web.server as server

    session_with_workdir(monkeypatch, server, workdir)
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "platform_name", lambda value=None: "darwin")
    monkeypatch.setattr(file_manager.shutil, "which", lambda name: "/usr/bin/open")

    class Process:
        def wait(self, timeout=None):
            if timeout is not None:
                raise file_manager.subprocess.TimeoutExpired(cmd="open", timeout=timeout)
            return 0

    reapers: list[object] = []
    monkeypatch.setattr(file_manager.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(file_manager, "_reap", reapers.append)

    result = reveal_external(server)

    assert result["ok"] is True
    assert len(reapers) == 1


def test_route_returns_501_when_the_capability_probe_has_no_opener(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import packages.web.server as server
    from packages.core import session as sess

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    session = sess.create(name="editor-reveal-missing-explorer", adapter="cbc", workdir=str(workdir))
    monkeypatch.setattr(server.file_manager, "platform_name", lambda value=None: "windows")
    monkeypatch.setattr(server.file_manager, "_explorer_path", lambda: None)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/editor/reveal-external",
            json={"session_id": session.id, "path": "file.txt"},
        )

    assert response.status_code == 501
    assert "file manager" in response.json()["detail"]
    assert server.platform_layer.capabilities()["fileManager.reveal"] is False


def test_route_maps_success_and_rejection_to_http_status(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import packages.web.server as server
    from packages.core import session as sess

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "file.txt").write_text("hello", encoding="utf-8")
    session = sess.create(name="editor-reveal-external", adapter="cbc", workdir=str(workdir))
    revealed: list[Path] = []
    monkeypatch.setattr(
        server.file_manager,
        "reveal_file_in_file_manager",
        lambda target: revealed.append(target),
    )

    with TestClient(server.app) as client:
        success = client.post(
            "/api/editor/reveal-external",
            json={"session_id": session.id, "path": "file.txt"},
        )
        outside = client.post(
            "/api/editor/reveal-external",
            json={"session_id": session.id, "path": str(tmp_path / "outside.txt")},
        )

    assert success.status_code == 200
    assert success.json() == {"ok": True, "name": "file.txt"}
    assert outside.status_code == 403
    assert revealed == [(workdir / "file.txt").resolve()]
