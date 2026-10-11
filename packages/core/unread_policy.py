"""Global unread-recording policy for done-unread cursors.

App Settings → Notification owns one persisted choice in config.json
``ui.notifications.unreadRecording`` that decides which Sessions record done
unread at all:

- ``"all"``: every Session records done unread (the legacy default, so old
  configs keep their current behavior);
- ``"none"``: no Session records done unread anywhere (authoritative 0);
- ``"bell-on"`` / ``"bell-off"``: Sessions whose message bridge (bell) is on /
  off, using the existing ``msgBridgeEnabled`` semantics;
- ``"pinned"``: every pinned Session (the shared Session pin, never Job pins).

Category scopes form a union (``bell-on`` + ``pinned`` records the union of
bell-on and pinned Sessions). ``bell-on``/``bell-off`` are mutually exclusive;
``all``/``none`` are exclusive and absorb every other scope. An empty set is
normalized to ``"none"``; the React settings store mirrors the same rule.

Excluded Sessions must be authoritative ``count == 0`` on every surface
(durable projection, WS patches, list summaries, resync snapshots) while the
durable generation stays monotonic: an excluded Session stops recording new
done generations, and its read cursor is advanced to the current generation
whenever a transition excludes it. Re-including a Session therefore counts
from new events only and never restores old unread.
"""

from __future__ import annotations

import logging

from packages.core import config
from packages.core import notifications
from packages.core import session as sess

_log = logging.getLogger(__name__)

# Every scope id accepted in ``ui.notifications.unreadRecording``. Order is the
# canonical presentation order used by the settings UI.
UNREAD_RECORDING_SCOPES = ("bell-on", "bell-off", "pinned", "all", "none")

# Missing / unreadable configuration keeps the pre-feature behavior: every
# Session records done unread.
DEFAULT_UNREAD_RECORDING_SCOPES: tuple[str, ...] = ("all",)

# Process-wide hot snapshot. The summary/terminal hot paths never re-read
# config.json per Session; ``refresh_unread_recording`` republishes it (startup
# and right after the settings PUT that can change it).
_COMPILED_SNAPSHOT: tuple[str, ...] | None = None


def normalize_unread_recording(value: object) -> tuple[str, ...]:
    """Normalize one raw setting value into the canonical scope tuple.

    Missing / non-list values keep the legacy default (record everything). A
    list is filtered to known scope ids: ``all``/``none`` absorb every other
    scope, ``bell-on`` + ``bell-off`` collapse to ``all`` (their union is every
    Session), and an empty result means ``none`` — the shared "empty set is
    no-recording" rule.
    """
    if not isinstance(value, list):
        return DEFAULT_UNREAD_RECORDING_SCOPES
    scopes: list[str] = []
    for item in value:
        if (isinstance(item, str) and item in UNREAD_RECORDING_SCOPES
                and item not in scopes):
            scopes.append(item)
    if "all" in scopes:
        return ("all",)
    if "none" in scopes:
        return ("none",)
    if not scopes:
        return ("none",)
    if "bell-on" in scopes and "bell-off" in scopes:
        return ("all",)
    # Canonical, order-stable output (shared with the React sanitize mirror).
    return tuple(scope for scope in UNREAD_RECORDING_SCOPES if scope in scopes)


def unread_recording_from_ui(ui: object) -> tuple[str, ...]:
    """Extract the policy from one ``ui`` settings object.

    A missing key (old config) keeps the legacy default; an explicit ``null``
    is treated the same as missing. Any other value goes through
    :func:`normalize_unread_recording`.
    """
    if not isinstance(ui, dict):
        return DEFAULT_UNREAD_RECORDING_SCOPES
    notif = ui.get("notifications")
    if not isinstance(notif, dict) or notif.get("unreadRecording", None) is None:
        return DEFAULT_UNREAD_RECORDING_SCOPES
    return normalize_unread_recording(notif.get("unreadRecording"))


def refresh_unread_recording() -> tuple[str, ...]:
    """Re-read the policy from config.json and publish the hot snapshot."""
    global _COMPILED_SNAPSHOT
    try:
        snapshot = unread_recording_from_ui(config.load_config().get("ui"))
    except Exception:
        _log.warning(
            "unread-recording policy reload failed; keeping the previous snapshot",
            exc_info=True,
        )
        snapshot = (_COMPILED_SNAPSHOT if _COMPILED_SNAPSHOT is not None
                    else DEFAULT_UNREAD_RECORDING_SCOPES)
    _COMPILED_SNAPSHOT = snapshot
    return snapshot


def current_unread_recording() -> tuple[str, ...]:
    """Return the process-wide policy snapshot (first use loads it once)."""
    if _COMPILED_SNAPSHOT is None:
        return refresh_unread_recording()
    return _COMPILED_SNAPSHOT


def msg_bridge_enabled(session) -> bool:
    """The Session's bell state (same truth as the API ``msgBridgeEnabled``)."""
    settings = notifications.normalize_notification_settings(
        session.notification_settings,
    )
    return bool(
        session.qq_subscriptions
        or session.qq_report_targets
        or settings["system"]
        or settings["browser"]
    )


def is_scope_recorded(scopes, *, bridge: bool, pinned: bool) -> bool:
    """The single pure scope decision over the normalized scope tuple."""
    if "all" in scopes:
        return True
    if "none" in scopes:
        return False
    if "bell-on" in scopes and bridge:
        return True
    if "bell-off" in scopes and not bridge:
        return True
    if "pinned" in scopes and pinned:
        return True
    return False


def should_record_unread(session, *, scopes=None, pinned=None) -> bool:
    """Evaluate the policy for one Session.

    ``pinned`` may be pre-resolved by callers that already read the shared pin
    snapshot; when omitted it is looked up from the Session pin projection and
    only when the scope tuple actually depends on it. Callers on the terminal
    commit path must guard this call: a damaged pin file may raise here, and a
    policy read must never fail a real terminal commit.
    """
    effective = current_unread_recording() if scopes is None else tuple(scopes)
    if "all" in effective or "none" in effective:
        return "all" in effective
    bridge = msg_bridge_enabled(session)
    if pinned is None:
        pinned = False
        if "pinned" in effective:
            pinned = bool(sess.session_pin_projection(session.id).get("pinned"))
    return is_scope_recorded(effective, bridge=bridge, pinned=pinned)


def unread_cursor_projection(
    session, *, scopes=None, pinned=None,
) -> tuple[int, int, int]:
    """Return ``(count, generation, read_generation)`` for every read surface.

    Excluded Sessions project ``count = 0`` and a read cursor clamped up to the
    generation. The clamp only moves the cursor forward (monotonic), so
    client-side merge guards (`preserveNewerUnread`) converge to 0 instead of
    preserving a stale unread pair, and a late snapshot can never resurrect
    unread for an excluded Session.
    """
    generation = max(0, int(getattr(session, "unread_done_generation", 0) or 0))
    read_generation = max(0, int(
        getattr(session, "unread_done_read_generation", 0) or 0))
    read_generation = min(read_generation, generation)
    if should_record_unread(session, scopes=scopes, pinned=pinned):
        return generation - read_generation, generation, read_generation
    return 0, generation, generation
