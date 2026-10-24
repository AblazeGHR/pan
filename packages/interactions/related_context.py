"""Creation-time context: bounded reads, no hydration/backfill or guessed IDs."""
import json

from packages.core import session

MAX_ROWS = 200
MAX_BYTES = 1024 * 1024
from packages.core.message_navigation import is_navigation_user


def previous_user_message_id(owner_id):
    """Inspect at most 200 tail rows / 1 MiB; uncertain or legacy => null.

    Hot Sessions use the append/replace lock and a bounded reverse slice. Cold
    Sessions seek the companion JSONL tail once, never call history_page (which
    scans the whole file) or load_history=True. Oversized/partial rows fail
    closed; no fallback that materializes legacy inline history.
    """
    owner = session.get(owner_id, load_history=False)
    if owner is None:
        return None
    try:
        with owner._summary_lock:
            if getattr(owner, '_history_loaded', True):
                rows = list(reversed(owner.history[-MAX_ROWS:]))
            else:
                path = session._history_path(owner_id)
                before = session._history_file_signature(path)
                if before is None:
                    return None
                with path.open('rb') as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size - MAX_BYTES))
                    tail = handle.read(MAX_BYTES)
                if before != session._history_file_signature(path) or not tail.endswith(b'\n'):
                    return None
                lines = tail.split(b'\n')[:-1]
                if size > MAX_BYTES:
                    lines = lines[1:]  # first row may be partial
                rows = (json.loads(line) for line in reversed(lines[-MAX_ROWS:]))
            budget = MAX_BYTES
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('content'), str):
                    return None
                if len(row['content']) > budget:
                    return None
                budget -= len(row['content'].encode('utf-8'))
                if budget < 0:
                    return None
                if is_navigation_user(row):
                    mid = row.get('messageId')
                    return mid if session.is_pan_message_id(mid) else None
    except (OSError, ValueError, TypeError, UnicodeError):
        return None
    return None
