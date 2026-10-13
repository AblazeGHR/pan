"""Pan notification primitives.

The browser side consumes the returned event; the system side is deliberately
injectable so tests never launch a real OS notifier.  The default sender is
diagnostic rather than silently successful when no supported sender is wired.
"""

from __future__ import annotations

import platform
import base64
import asyncio
import shutil
import subprocess
import re
from urllib.parse import urlsplit
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

# A bounded per-notification message loop, not an installed/background service.
# A real Shell click is required before launching the URL. Each helper keeps
# its own immutable target, including when several Sessions complete together.
_WINDOWS_CLICK_SCRIPT = r'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$context = New-Object System.Windows.Forms.ApplicationContext
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 60000
$script:clicked = $false
$script:clickFailed = $false
$timer.add_Tick({ $context.ExitThread() })
$notify.add_BalloonTipClicked({
    if ($script:clicked) { return }
    $script:clicked = $true
    try { Start-Process -FilePath $link -ErrorAction Stop | Out-Null }
    catch { $script:clickFailed = $true; [Console]::Error.WriteLine('Pan notification URL launch failed') }
    $context.ExitThread()
})
try {
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.BalloonTipTitle = $title
    $notify.BalloonTipText = $body
    $notify.Visible = $true
    $notify.ShowBalloonTip(5000)
    $timer.Start()
    [System.Windows.Forms.Application]::Run($context)
} finally {
    $timer.Dispose()
    $notify.Dispose()
    $context.Dispose()
}
if ($script:clickFailed) { exit 1 }
'''.strip()


def completion_deep_link(session_id: object) -> str | None:
    """Use the local instance's existing /react/?session contract.

    Never use a browser-supplied origin, credentials, result text or arbitrary
    path as a Shell launch target. Invalid configuration disables only click.
    """
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
        return None
    from packages.core.config import resolve_pan_api_url
    try:
        base = urlsplit(resolve_pan_api_url())
        if (base.scheme not in ("http", "https")
                or base.hostname not in ("127.0.0.1", "localhost", "::1")
                or base.username is not None or base.password is not None
                or base.path not in ("", "/") or base.query or base.fragment):
            return None
        if base.port is not None and not 1 <= base.port <= 65535:
            return None
        return f"{base.scheme}://{base.netloc}/react/?session={session_id}"
    except (ValueError, OSError):
        return None


def _windows_notifyicon_command(title: str, body: str, deep_link: str | None = None) -> str:
    """Build an encoded script with data carried only through base64 literals."""
    title_b64 = base64.b64encode(normalize_title(title).encode("utf-8")).decode("ascii")
    body_b64 = base64.b64encode(str(body or "").encode("utf-8")).decode("ascii")
    script = (
        f"$title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{title_b64}'))\n"
        f"$body = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{body_b64}'))\n"
    )
    if deep_link is not None:
        link_b64 = base64.b64encode(deep_link.encode("utf-8")).decode("ascii")
        script += f"$link = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{link_b64}'))\n"
        script += _WINDOWS_CLICK_SCRIPT
    else:
        script += _WINDOWS_NOTIFYICON_SCRIPT
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _windows_system_sender(title: str, body: str, deep_link: str | None = None) -> dict:
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
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
             "-ExecutionPolicy", "Bypass", "-EncodedCommand",
             _windows_notifyicon_command(title, body, deep_link)],
            capture_output=True,
            text=True,
            timeout=75 if deep_link else 15,
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
    response = {"ok": True, "method": "windows.powershell.notifyicon"}
    if deep_link:
        # Process completion is not proof of OS display or a user click.
        response.update({"delivery": "unknown", "clickWindowSeconds": 60})
    return response


def default_system_sender(title: str, body: str, *, deep_link: str | None = None) -> dict:
    """Send via Windows inbox PowerShell, or report a diagnostic capability error."""
    if platform.system() == "Windows":
        return _windows_system_sender(title, body, deep_link)
    return {
        "ok": False,
        "code": "unsupported_system_notification",
        "message": f"Pan system notifications are unsupported on {platform.system()}",
    }


_system_sender: Callable[[str, str], dict] = default_system_sender


def set_system_sender(sender: Callable[[str, str], dict]) -> None:
    global _system_sender
    _system_sender = sender


def _send_completion_system(title: str, body: str, session_id: object) -> dict:
    # Preserve the existing injectable two-argument sender contract.
    if _system_sender is default_system_sender:
        return default_system_sender(title, body, deep_link=completion_deep_link(session_id))
    return _system_sender(title, body)


def _completion_parts(session, status: str, result: str) -> tuple[dict | None, str, str, bool]:
    settings = normalize_notification_settings(getattr(session, "notification_settings", None))
    if status != "done" or not (settings["browser"] or settings["system"]):
        return None, "", "", False
    title = normalize_title(f"{session.name} completed")
    body = str(result or "Task completed")
    return {
        "title": title,
        "body": body,
        "browser": settings["browser"],
        "system": None,
    }, title, body, settings["system"]


def dispatch_completion_nonblocking(session, status: str, result: str) -> dict | None:
    """Return the browser event now and run the OS sender off the event loop."""
    payload, title, body, needs_system = _completion_parts(session, status, result)
    if payload is None:
        return None
    if needs_system:
        session_id = session.id
        async def send_in_background() -> None:
            try:
                await asyncio.to_thread(_send_completion_system, title, body, session_id)
            except Exception:
                # Desktop delivery is best effort and must never affect the
                # already-persisted terminal result or its broadcasts.
                return
        asyncio.create_task(send_in_background())
        payload["system"] = {"ok": True, "method": "background"}
    return payload


def dispatch_completion(session, status: str, result: str) -> dict | None:
    """Build browser payload and best-effort system delivery for a done task."""
    payload, title, body, needs_system = _completion_parts(session, status, result)
    if payload is None:
        return None
    if needs_system:
        try:
            payload["system"] = _send_completion_system(title, body, session.id)
        except Exception as exc:  # notification failure must not fail completion
            payload["system"] = {"ok": False, "code": "system_notification_failed", "message": str(exc)}
    return payload


def dispatch_reminder(title: object, body: object) -> dict:
    """Deliver a registered system reminder; sender failures are diagnostic."""
    normalized = normalize_title(title)
    text = str(body or "")
    try:
        system = _system_sender(normalized, text)
    except Exception as exc:
        system = {"ok": False, "code": "system_notification_failed", "message": str(exc)}
    return {"title": normalized, "body": text, "system": system}


def completion_bridge_defaults(value: object, *, strict: bool = False) -> dict:
    """Legacy bell behavior is system-only; explicit false/[] stay explicit."""
    import re
    raw = value if isinstance(value, dict) else {}
    if strict and not isinstance(value, dict):
        raise ValueError("Completion defaults must be an object")
    defaults = {"system": True, "browser": False, "qqReport": False, "qqSubscribe": False}
    result = {}
    for key, fallback in defaults.items():
        val = raw.get(key, fallback)
        if strict and not isinstance(val, bool):
            raise ValueError(f"{key} must be boolean")
        result[key] = val if isinstance(val, bool) else fallback
    targets = raw.get("qqTargets", [])
    if not isinstance(targets, list):
        if strict:
            raise ValueError("QQ contacts must be an array")
        targets = []
    valid = [key for key in targets if isinstance(key, str) and re.fullmatch(r"(user|group):[1-9][0-9]*(?:@[1-9][0-9]*)?", key)]
    if strict and len(valid) != len(targets):
        raise ValueError("Invalid QQ contact identity")
    result["qqTargets"] = sorted(set(valid))
    if strict and (result["qqReport"] or result["qqSubscribe"]) and not result["qqTargets"]:
        raise ValueError("Select at least one QQ contact before enabling QQ Report or Subscribe by default")
    return result
