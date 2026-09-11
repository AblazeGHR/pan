"""Pan notification primitives.

The browser side consumes the returned event; the system side is deliberately
injectable so tests never launch a real OS notifier.  The default sender is
diagnostic rather than silently successful when no supported sender is wired.
"""

from __future__ import annotations

import platform
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


def default_system_sender(title: str, body: str) -> dict:
    """Report the platform capability without pretending delivery succeeded."""
    return {
        "ok": False,
        "code": "unsupported_system_notification",
        "message": f"No Pan system notification sender is configured for {platform.system()}",
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
