"""Pan notification primitives.

The browser side consumes the returned event; the system side is deliberately
injectable so tests never launch a real OS notifier.  The default sender is
diagnostic rather than silently successful when no supported sender is wired.

Windows system delivery uses a real WinRT toast (packages/core/windows_toast)
so completions are recorded in the notification center and stay clickable after
the sender exited; the legacy NotifyIcon balloon (which never reached the
notification center) is gone.
"""

from __future__ import annotations

import platform
import asyncio
import re
import hashlib
import logging
from urllib.parse import urlsplit
from typing import Callable

PAN_PREFIX = "Pan:"
_log = logging.getLogger(__name__)
_notification_tasks: set[asyncio.Task] = set()


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


_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


def completion_deep_link(session_id: object) -> str | None:
    """Use the local instance's existing /react/?session contract.

    Never use a browser-supplied origin, credentials, result text or arbitrary
    path as a Shell launch target. Invalid configuration disables only click.
    """
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
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


def _windows_system_sender(title: str, body: str, deep_link: str | None = None,
                           *, tag: str | None = None, group: str | None = None) -> dict:
    """Deliver one real Windows toast through the inbox WinRT path."""
    from packages.core import windows_toast
    try:
        return windows_toast.show_toast(
            normalize_title(title),
            body,
            launch_url=deep_link,
            tag=tag,
            group=group,
        )
    except Exception as exc:  # notification failure must stay diagnostic
        return {"ok": False, "code": "windows_system_notification_failed", "message": str(exc)}


def default_system_sender(title: str, body: str, *, deep_link: str | None = None,
                          tag: str | None = None, group: str | None = None) -> dict:
    """Send via the inbox Windows toast path, or report a capability error."""
    if platform.system() == "Windows":
        return _windows_system_sender(title, body, deep_link, tag=tag, group=group)
    return {
        "ok": False,
        "code": "unsupported_system_notification",
        "message": f"Pan system notifications are unsupported on {platform.system()}",
    }


_system_sender: Callable[[str, str], dict] = default_system_sender


def set_system_sender(sender: Callable[[str, str], dict]) -> None:
    global _system_sender
    _system_sender = sender


def reminder_dedup_tag(title: str, body: str) -> str:
    """Replace-by-content tag: identical reminders collapse in the center."""
    digest = hashlib.sha1(f"{title}\n{body}".encode("utf-8")).hexdigest()[:16]
    return f"reminder-{digest}"


def _send_completion_system(title: str, body: str, session_id: object) -> dict:
    # Preserve the existing injectable two-argument sender contract.
    if _system_sender is default_system_sender:
        deep_link = completion_deep_link(session_id)
        tag = None
        if isinstance(session_id, str) and _SESSION_ID_PATTERN.fullmatch(session_id):
            # One notification per session: a re-completion replaces the old
            # entry instead of stacking duplicates in the notification center.
            tag = f"completion-{session_id}"
        return default_system_sender(
            title, body,
            deep_link=deep_link, tag=tag, group="Pan-completions",
        )
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
                result = await asyncio.to_thread(_send_completion_system, title, body, session_id)
            except Exception:
                result = {"ok": False, "code": "system_notification_failed"}
            # Keep the actual helper result observable without logging task
            # text, deep links, exception messages or other private payloads.
            log = _log.info if result.get("ok") else _log.warning
            log("System notification session=%s ok=%s code=%s setting=%s hresult=%s winerror=%s exitcode=%s identity=%s stage=%s delivery=%s",
                session_id, result.get("ok"), result.get("code"), result.get("setting"),
                result.get("hresult"), result.get("winerror"), result.get("exitcode"),
                result.get("identityRegistered"), result.get("stage"),
                result.get("delivery", "unknown"))
        task = asyncio.create_task(send_in_background())
        _notification_tasks.add(task)
        task.add_done_callback(_notification_tasks.discard)
        payload["system"] = {"ok": None, "method": "background", "delivery": "unknown"}
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
        if _system_sender is default_system_sender:
            system = default_system_sender(
                normalized, text,
                tag=reminder_dedup_tag(normalized, text), group="Pan-reminders",
            )
        else:
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
