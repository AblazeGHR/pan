"""Pipe Worker launch policy; no PTY or process ownership claims.

POSIX starts a new session so host terminal signals do not reach CLI children.
Stopping remains the Worker's explicit tree cleanup, independent of that signal.
"""
from packages.jobs.window import command_window_options
from . import is_windows


def spawn_options(platform: str | None = None) -> dict:
    if is_windows(platform):
        return command_window_options()
    return {"start_new_session": True}
