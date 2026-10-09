"""Window policy for background commands, independent of Pan runtime."""
import os
import subprocess


def validate_hide_window(value):
    if type(value) is not bool:
        raise ValueError("hideWindow must be a boolean")
    return value


def windows_creation_flags(hide_window=True):
    # Explicit visible mode gets its own console even under a detached runner.
    return 0x08000000 if hide_window else 0x00000010  # NO_WINDOW / NEW_CONSOLE


def command_window_options(hide_window=True):
    validate_hide_window(hide_window)
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0 if hide_window else 1  # SW_HIDE / SW_SHOWNORMAL
    return {"creationflags": windows_creation_flags(hide_window), "startupinfo": startup}
