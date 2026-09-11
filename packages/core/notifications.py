"""Pan notification primitives.

The browser side consumes the returned event; the system side is deliberately
injectable so tests never launch a real OS notifier.  The default sender is
diagnostic rather than silently successful when no supported sender is wired.
"""

from __future__ import annotations

import platform
import base64
import shutil
import subprocess
from typing import Callable

PAN_PREFIX = "Pan:"


def normalize_title(title: object) -> str:
    """Return a user-controlled title with exactly one leading ``Pan:``."""
    text = str(title or "").strip()
    while text.startswith(PAN_PREFIX):
        text = text[len(PAN_PREFIX):].lstrip()
    return f"{PAN_PREFIX} {text}" if text else PAN_PREFIX


def normalize_notification_settings(value: object) -> dict:
    """Normalize persisted settings; missing/invalid legacy fields are off."""
    raw = value if isinstance(value, dict) else {}
    return {
        "browser": bool(raw.get("browser", False)),
        "system": bool(raw.get("system", False)),
    }


_WINDOWS_NOTIFYICON_SCRIPT = r'''
param()
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.BalloonTipTitle = $title
$notify.BalloonTipText = $body
$notify.Visible = $true
$notify.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$notify.Dispose()
'''.strip()


def _windows_notifyicon_command(title: str, body: str) -> str:
    """Build an encoded script with data carried only through base64 literals."""
    title_b64 = base64.b64encode(normalize_title(title).encode("utf-8")).decode("ascii")
    body_b64 = base64.b64encode(str(body or "").encode("utf-8")).decode("ascii")
    script = (
        f"$title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{title_b64}'))\n"
        f"$body = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{body_b64}'))\n"
        + _WINDOWS_NOTIFYICON_SCRIPT
    )
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _windows_system_sender(title: str, body: str) -> dict:
    """Use the inbox Windows PowerShell/.NET NotifyIcon implementation."""
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not executable:
        return {
            "ok": False,
            "code": "windows_powershell_unavailable",
            "message": "PowerShell executable was not found",
        }
    try:
        completed = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-EncodedCommand",
             _windows_notifyicon_command(title, body)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "code": "windows_system_notification_timeout",
            "message": "PowerShell NotifyIcon sender timed out",
        }
    except OSError as exc:
        return {
            "ok": False,
            "code": "windows_system_notification_launch_failed",
            "message": str(exc),
        }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "process exited without diagnostics").strip()
        return {
            "ok": False,
            "code": "windows_system_notification_failed",
            "message": f"PowerShell NotifyIcon exited with code {completed.returncode}: {detail}",
        }
    return {"ok": True, "method": "windows.powershell.notifyicon"}


def default_system_sender(title: str, body: str) -> dict:
    """Send via Windows inbox PowerShell, or report a diagnostic capability error."""
    if platform.system() == "Windows":
        return _windows_system_sender(title, body)
    return {
        "ok": False,
        "code": "unsupported_system_notification",
        "message": f"Pan system notifications are unsupported on {platform.system()}",
    }


_system_sender: Callable[[str, str], dict] = default_system_sender


def set_system_sender(sender: Callable[[str, str], dict]) -> None:
    global _system_sender
    _system_sender = sender


def dispatch_completion(session, status: str, result: str) -> dict | None:
    """Build browser payload and best-effort system delivery for a done task."""
    settings = normalize_notification_settings(getattr(session, "notification_settings", None))
    if status != "done" or not (settings["browser"] or settings["system"]):
        return None
    title = normalize_title(f"{session.name} completed")
    body = str(result or "Task completed")
    system = None
    if settings["system"]:
        try:
            system = _system_sender(title, body)
        except Exception as exc:  # notification failure must not fail completion
            system = {"ok": False, "code": "system_notification_failed", "message": str(exc)}
    return {
        "title": title,
        "body": body,
        "browser": settings["browser"],
        "system": system,
    }


def dispatch_reminder(title: object, body: object) -> dict:
    """Deliver a registered system reminder; sender failures are diagnostic."""
    normalized = normalize_title(title)
    text = str(body or "")
    try:
        system = _system_sender(normalized, text)
    except Exception as exc:
        system = {"ok": False, "code": "system_notification_failed", "message": str(exc)}
    return {"title": normalized, "body": text, "system": system}
