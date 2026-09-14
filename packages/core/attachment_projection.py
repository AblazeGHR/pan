"""Project durable attachment parts into provider-facing message text.

The web API deliberately exposes safe hrefs and display metadata.  Providers
must never receive those hrefs as file targets: the adapter-facing projection
uses the server-owned, canonical absolute path captured in the durable part.
``__serverPath`` is an internal queue/history field and is stripped by the
web serialization boundary before it reaches a browser.
"""

from __future__ import annotations

from pathlib import Path


SERVER_PATH_KEY = "__serverPath"


class AttachmentProjectionError(ValueError):
    """A durable attachment no longer points at a readable server file."""


def public_message_parts(parts: list[dict] | None) -> list[dict] | None:
    """Remove provider-only path material before a part crosses to the UI."""
    if not isinstance(parts, list):
        return None
    return [
        {key: value for key, value in part.items() if key != SERVER_PATH_KEY}
        for part in parts
        if isinstance(part, dict)
    ]


def _worker_attachment_text(path: str) -> str:
    """Return the legacy-compatible path token understood by text adapters."""
    canonical = Path(path).resolve()
    if not canonical.is_file():
        raise AttachmentProjectionError(f"attachment is stale: {canonical}")
    # Keep the legacy marker so existing text adapters can recognize an
    # attachment, while making the actual target the server-side path.  A
    # quote in a filename is escaped rather than allowing it to terminate the
    # marker early.
    return '@"' + str(canonical).replace('"', '\\"') + '"'


def project_message_parts(parts: list[dict] | None, fallback_text: str) -> str:
    """Build provider text from canonical parts without using API hrefs.

    Parts produced by the current server carry ``__serverPath``.  A legacy
    structured row may not have it; in that case the safe compatibility
    behavior is a display-name marker, never the persisted Markdown/API href.
    """
    if not isinstance(parts, list) or not parts:
        return fallback_text

    projected: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            value = part.get("text", part.get("value"))
            if isinstance(value, str):
                projected.append(value)
            continue
        if part.get("type") != "attachment":
            continue
        path = part.get(SERVER_PATH_KEY)
        if isinstance(path, str) and path.strip():
            projected.append(_worker_attachment_text(path))
        else:
            display_name = part.get("displayName")
            projected.append(
                f"[attachment: {display_name}]"
                if isinstance(display_name, str) and display_name.strip()
                else "[attachment]"
            )
    return "".join(projected)
