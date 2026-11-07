"""Select one file in the platform's file manager.

This is the thin platform boundary for the Editor reveal action. Business code
passes a resolved filesystem path only; the opener argv is fixed here and never
comes from a request, so a request can never execute an arbitrary command.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import DARWIN, LINUX, WINDOWS, platform_name

__all__ = [
    "RevealFailed",
    "RevealUnavailable",
    "reveal_available",
    "reveal_argv",
    "reveal_file_in_file_manager",
]

_LAUNCH_TIMEOUT_SECONDS = 2.0
_LOGGER = logging.getLogger("pan.platform.file_manager")


class RevealUnavailable(RuntimeError):
    """This host has no usable desktop file manager."""


class RevealFailed(RuntimeError):
    """The platform file manager could not be started or exited with an error."""


def _has_desktop_session() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _explorer_path() -> str | None:
    found = shutil.which("explorer.exe") or shutil.which("explorer")
    if found:
        return found
    root = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if not root:
        return None
    candidate = Path(root) / "explorer.exe"
    return str(candidate) if candidate.is_file() else None


def reveal_available(platform: str | None = None) -> bool:
    """Whether this platform can reveal a file right now.

    Declaration-only platforms stay false. The three supported platforms also
    need their runtime opener: Explorer, ``open``, or xdg-open in a desktop
    session. Capability probing and the request path call this same function.
    """
    resolved = platform_name(platform)
    if resolved == WINDOWS:
        return _explorer_path() is not None
    if resolved == DARWIN:
        return shutil.which("open") is not None
    if resolved == LINUX:
        return _has_desktop_session() and shutil.which("xdg-open") is not None
    return False


def reveal_argv(target: Path, platform: str | None = None) -> list[str] | None:
    """Fixed reveal argv for POSIX platforms, or None when unavailable.

    Windows has no argv form (Explorer's ``/select,`` switch is handled by
    ``reveal_file_in_file_manager``). Linux has no portable "select this file"
    primitive, so xdg-open receives the containing directory.
    """
    resolved = platform_name(platform)
    if resolved == DARWIN:
        return ["open", "-R", str(target)]
    if resolved == LINUX and _has_desktop_session():
        opener = shutil.which("xdg-open")
        return [opener, str(target.parent)] if opener else None
    return None


def _failure_detail(exc: OSError) -> str:
    code = getattr(exc, "winerror", None) or exc.errno
    suffix = f" {code}" if code else ""
    return f"system file manager failed ({type(exc).__name__}{suffix})"


def _reap(process: "subprocess.Popen") -> None:
    def reap() -> None:
        returncode = process.wait()
        if returncode != 0:
            _LOGGER.warning("file manager exited with status %s", returncode)

    threading.Thread(target=reap, name="file-manager-reaper", daemon=True).start()


def _launch(argv: list[str], *, ignore_nonzero_exit: bool = False) -> None:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RevealUnavailable("no system file manager is available") from exc
    except OSError as exc:
        raise RevealFailed(_failure_detail(exc)) from exc

    try:
        returncode = process.wait(timeout=_LAUNCH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _reap(process)
        return
    # Explorer reports a nonzero status even when the window opens; only a
    # spawn failure is actionable for that switch.
    if returncode != 0 and not ignore_nonzero_exit:
        raise RevealFailed(f"system file manager exited with status {returncode}")


def reveal_file_in_file_manager(target: Path) -> None:
    """Reveal one existing file, never through a shell."""
    if platform_name() == WINDOWS:
        explorer = _explorer_path()
        if explorer is None:
            raise RevealUnavailable("no system file manager is available")
        _launch([explorer, "/select,", str(target)], ignore_nonzero_exit=True)
        return

    argv = reveal_argv(target)
    if argv is None:
        raise RevealUnavailable("no system file manager is available")
    _launch(argv)
