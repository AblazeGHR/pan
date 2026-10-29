"""Pipe Worker launch policy; no PTY or process ownership claims.

POSIX starts a new session so host terminal signals do not reach CLI children.
Stopping remains the Worker's explicit tree cleanup, independent of that signal.
"""
from packages.jobs.window import command_window_options
from . import is_windows
import os


def shell_command_argv(command: str) -> list[str]:
    """Explicit argv for a command on this execution host; never the browser OS."""
    if is_windows():
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


def spawn_options(platform: str | None = None) -> dict:
    if is_windows(platform):
        return command_window_options()
    return {"start_new_session": True}
