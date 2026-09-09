"""Normalize Codex account rate-limit snapshots for API consumers.

The Codex app-server emits account limits as ``primary`` and ``secondary``;
the WHAM endpoint uses ``primary_window`` and ``secondary_window``. Those
names are provider protocol names, not quota duration. Pan therefore keeps
the provider payload verbatim and classifies each window from its actual
duration. ``updatedAt`` is a compatibility field whose value is the local Pan
observation time; the provider's original update time is not present in the
app-server event path. ``receivedAt`` makes that local timestamp explicit.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_WINDOWS = (
    ("first", ("primary", "primary_window")),
    ("secondary", ("secondary", "secondary_window")),
)

_DURATION_KEYS = (
    "windowDurationMins", "window_duration_mins", "durationMins",
    "durationMinutes", "duration_minutes", "limit_window_seconds",
    "windowDurationSeconds", "window_duration_seconds", "durationSeconds",
    "duration_seconds",
)
_SECRET_KEYS = frozenset({
    "access_token", "accesstoken", "refresh_token", "refreshtoken",
    "id_token", "idtoken", "authorization",
})


def _safe_copy(value: Any) -> Any:
    """Copy provider data while never retaining credential-shaped fields."""
    if isinstance(value, dict):
        return {
            key: _safe_copy(item)
            for key, item in value.items()
            if str(key).replace("-", "_").lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_safe_copy(item) for item in value]
    return deepcopy(value)


def _as_record(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) and value else None


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first explicitly provided value, preserving zero values."""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _duration_seconds(raw: dict[str, Any]) -> int | float | None:
    value = _first_value(raw, _DURATION_KEYS)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    if any(key in raw for key in ("limit_window_seconds", "windowDurationSeconds",
                                  "window_duration_seconds", "durationSeconds",
                                  "duration_seconds")):
        return value
    return value * 60


def _duration_minutes(raw: dict[str, Any], seconds: int | float | None):
    value = _first_value(raw, (
        "windowDurationMins", "window_duration_mins", "durationMins",
        "durationMinutes", "duration_minutes",
    ))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if seconds is None:
        return None
    return seconds / 60


def classify_window_duration(seconds: int | float | None) -> str:
    """Classify a quota window without trusting provider key names."""
    if seconds is None:
        return "unknown"
    if 4.5 * 3600 <= seconds <= 5.5 * 3600:
        return "five_hour"
    if 23 * 3600 <= seconds <= 25 * 3600:
        return "daily"
    if seconds == 7 * 24 * 3600:
        return "weekly"
    if 28 * 24 * 3600 <= seconds <= 31 * 24 * 3600:
        return "monthly"
    return "unknown"


def _kind_labels(kind: str) -> tuple[str, str]:
    return {
        "five_hour": ("5h", "five_hour"),
        "daily": ("day", "daily"),
        "weekly": ("week", "weekly"),
        "monthly": ("month", "monthly"),
    }.get(kind, ("unknown", "unknown"))


def _remaining_percent(used_percent: Any) -> int | float | None:
    """Derive a percentage remainder only from a valid percentage value."""
    if isinstance(used_percent, bool) or not isinstance(used_percent, (int, float)):
        return None
    if not 0 <= used_percent <= 100:
        return None
    return 100 - used_percent


def _window_snapshot(
    key: str,
    provider_key: str,
    raw: Any,
) -> dict[str, Any]:
    """Build one explicit window result without inventing absolute quotas."""
    raw = _as_record(_safe_copy(raw))

    used_percent = _first_value(raw, ("usedPercent", "used_percent")) if raw else None
    duration_seconds = _duration_seconds(raw) if raw else None
    kind = classify_window_duration(duration_seconds)
    name, label = _kind_labels(kind)
    return {
        "key": key,
        "name": name,
        "label": label,
        "providerKey": provider_key,
        "status": "available" if raw is not None else "missing",
        "kind": kind,
        "usage": {
            # The current app-server payload provides this percentage, but
            # does not reliably provide absolute used/remaining/limit values.
            "usedPercent": used_percent,
            "remainingPercent": _remaining_percent(used_percent),
            "used": _first_value(raw, (
                "used", "usedAmount", "usedTokens", "used_tokens",
                "usedCredits", "used_credits", "creditsUsed", "credits_used",
            )) if raw else None,
            "remaining": _first_value(raw, (
                "remaining", "remainingAmount", "remainingTokens", "remaining_tokens",
                "remainingCredits", "remaining_credits", "creditsRemaining", "credits_remaining",
            )) if raw else None,
            "limit": _first_value(raw, (
                "limit", "limitAmount", "limitTokens", "limit_tokens",
                "limitCredits", "limit_credits", "creditsLimit", "credits_limit",
            )) if raw else None,
        },
        "windowDurationMins": _duration_minutes(raw, duration_seconds) if raw else None,
        "windowDurationSeconds": duration_seconds,
        "resetsAt": _first_value(raw, ("resetsAt", "resets_at", "resetAt", "reset_at")) if raw else None,
        # Preserve provider fields so newer Codex payloads remain queryable
        # before Pan adds a dedicated mapping for them.
        "raw": deepcopy(raw) if raw is not None else None,
    }


def normalize_codex_rate_limits(rate_limits: dict[str, Any]) -> dict[str, Any]:
    """Normalize app-server and WHAM payloads into a stable window projection.

    The returned ``windows`` map uses ``first`` and ``secondary`` for the
    compatibility API selectors, but ``kind`` is always derived from the
    duration. Unknown provider windows remain under ``provider:<key>`` and the
    complete input remains in ``raw``.
    """
    if not isinstance(rate_limits, dict):
        return {"windows": {}, "raw": None, "valid": False}

    payload = rate_limits.get("rate_limit")
    if not isinstance(payload, dict):
        payload = rate_limits

    windows: dict[str, dict[str, Any]] = {}
    consumed: set[str] = set()
    for selector, provider_keys in _WINDOWS:
        found = False
        for provider_key in provider_keys:
            if provider_key in payload:
                windows[selector] = _window_snapshot(
                    selector, provider_key, payload.get(provider_key),
                )
                consumed.add(provider_key)
                found = True
                break
        if not found:
            # Keep the compatibility selectors explicit even when one side of
            # an app-server snapshot is absent. This is a missing window, not
            # an inferred zero quota and not a reason to discard the other
            # last-good window.
            windows[selector] = _window_snapshot(selector, provider_keys[0], None)

    for provider_key, raw in payload.items():
        if provider_key in consumed or not isinstance(raw, dict) or not raw:
            continue
        windows[f"provider:{provider_key}"] = _window_snapshot(
            f"provider:{provider_key}", provider_key, raw,
        )

    valid = any(window["status"] == "available" for window in windows.values())
    return {"windows": windows, "raw": _safe_copy(rate_limits), "valid": valid}


def merge_normalized_quota(
    previous: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any] | None:
    """Merge valid windows while preserving last-good windows on partial input."""
    if not incoming.get("valid"):
        return None
    old = previous if isinstance(previous, dict) else {}
    windows = deepcopy(old.get("windows")) if isinstance(old.get("windows"), dict) else {}
    for key, value in incoming["windows"].items():
        if value.get("status") == "available":
            windows[key] = deepcopy(value)
    raw_sources = deepcopy(old.get("rawSnapshots")) if isinstance(old.get("rawSnapshots"), dict) else {}
    return {
        "windows": windows,
        "rawSnapshots": raw_sources,
        "raw": deepcopy(incoming.get("raw")),
    }


def _format_result(
    windows: dict[str, Any],
    *,
    session_id: str | None,
    worker_id: str | None,
    updated_at: str | None,
    received_at: str | None,
    observed_at: str | None = None,
    source_name: str = "app-server-push",
    stale: bool = False,
    raw_snapshots: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    requested_window: str = "all",
) -> dict[str, Any]:
    if received_at is None:
        received_at = updated_at
    is_push = source_name == "app-server-push"
    is_wham = source_name == "wham-usage"
    result: dict[str, Any] = {
        "ok": True,
        "provider": "codex",
        "sessionId": session_id,
        "workerId": worker_id,
        "updatedAt": updated_at,
        "observedAt": observed_at or updated_at,
        "receivedAt": received_at,
        "stale": stale,
        "source": {
            "provider": "codex",
            "transport": "app-server" if is_push else "chatgpt-backend-api" if is_wham else "cache",
            "event": "account/rateLimits/updated" if is_push else "wham/usage" if is_wham else "cached-snapshot",
            "cacheSource": source_name,
            "sessionId": session_id,
            "workerId": worker_id,
            "updatedAt": updated_at,
            "observedAt": observed_at or updated_at,
            "receivedAt": received_at,
            "providerUpdatedAt": None,
            "timestampMeaning": (
                "observedAt and receivedAt are local Pan Worker receive time "
                "and observation times; "
                "the provider event's original update time is unavailable"
            ),
        },
        "windows": deepcopy(windows),
    }
    if raw_snapshots is not None:
        result["rawSnapshots"] = deepcopy(raw_snapshots)
    if raw is not None:
        result["raw"] = deepcopy(raw)
    if requested_window != "all":
        result["window"] = requested_window
        result["windows"] = {requested_window: result["windows"].get(requested_window, {
            "key": requested_window,
            "status": "missing",
            "name": "unknown",
            "label": "unknown",
            "providerKey": None,
            "kind": "unknown",
            "usage": {"usedPercent": None, "remainingPercent": None,
                      "used": None, "remaining": None, "limit": None},
            "windowDurationMins": None,
            "windowDurationSeconds": None,
            "resetsAt": None,
            "raw": None,
        })}
    return result


def format_codex_quota(
    rate_limits: dict[str, Any],
    *,
    session_id: str,
    worker_id: str,
    updated_at: str | None,
    received_at: str | None = None,
    requested_window: str = "all",
) -> dict[str, Any]:
    """Return the stable API/MCP shape for one live Codex rate-limit snapshot.

    ``updated_at`` is retained for response compatibility and means the same
    local Pan receive time as ``received_at``.  It is never treated as a
    provider-originated timestamp.
    """
    normalized = normalize_codex_rate_limits(rate_limits)
    return _format_result(
        normalized["windows"], session_id=session_id, worker_id=worker_id,
        updated_at=updated_at, received_at=received_at,
        source_name="app-server-push", raw=normalized.get("raw"),
        requested_window=requested_window,
    )


def format_codex_quota_record(
    record: dict[str, Any],
    *,
    session_id: str | None,
    worker_id: str | None,
    requested_window: str = "all",
    stale: bool = False,
) -> dict[str, Any]:
    """Format a persisted global-store record without reclassifying windows."""
    return _format_result(
        record.get("windows") if isinstance(record.get("windows"), dict) else {},
        session_id=session_id,
        worker_id=worker_id,
        updated_at=record.get("updatedAt"),
        observed_at=record.get("observedAt"),
        received_at=record.get("receivedAt"),
        source_name=str(record.get("source") or "cache"),
        stale=stale,
        raw_snapshots=record.get("rawSnapshots"),
        raw=record.get("raw") if isinstance(record.get("raw"), dict) else None,
        requested_window=requested_window,
    )


def validate_quota_window(window: str) -> bool:
    """Whether a caller supplied a supported quota window selector."""
    return window in {"all", "first", "secondary"}
