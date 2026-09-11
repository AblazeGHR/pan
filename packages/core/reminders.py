"""Durable one-shot absolute reminders owned by Pan."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from packages.core.notifications import normalize_title

REMINDER_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reminders.json"
_lock = threading.RLock()


def parse_due_at(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("dueAt must be an ISO-8601 absolute timestamp")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("dueAt must be a valid ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError("dueAt must include an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load() -> list[dict]:
    try:
        data = json.loads(REMINDER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict]) -> None:
    REMINDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    REMINDER_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def register(session_id: str, due_at: str, title: str, body: str) -> dict:
    normalized = parse_due_at(due_at)
    with _lock:
        item = {"id": "rem_" + uuid.uuid4().hex[:16], "sessionId": session_id,
                "dueAt": normalized, "title": normalize_title(title), "body": body,
                "status": "pending", "createdAt": datetime.now(timezone.utc).isoformat()}
        items = _load(); items.append(item); _save(items)
        return dict(item)


def list_for_session(session_id: str) -> list[dict]:
    with _lock:
        return [dict(x) for x in _load() if x.get("sessionId") == session_id and x.get("status") == "pending"]


def cancel(session_id: str, reminder_id: str) -> dict | None:
    with _lock:
        items = _load()
        for item in items:
            if item.get("id") == reminder_id and item.get("sessionId") == session_id:
                if item.get("status") != "pending": return None
                item["status"] = "cancelled"; _save(items); return dict(item)
    return None


def claim_due(now: datetime | None = None) -> list[dict]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with _lock:
        items = _load(); due = []
        for item in items:
            if item.get("status") != "pending": continue
            try: is_due = datetime.fromisoformat(item["dueAt"].replace("Z", "+00:00")) <= current
            except (KeyError, ValueError, TypeError): is_due = False
            if is_due:
                item["status"] = "delivered"; item["deliveredAt"] = current.isoformat(); due.append(dict(item))
        if due: _save(items)
        return due
