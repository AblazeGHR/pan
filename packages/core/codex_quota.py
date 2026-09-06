"""Normalize the live Codex account rate-limit snapshot for API consumers.

The Codex app-server emits account limits as ``primary`` and ``secondary``.
Those names are provider protocol names, not adapter fallback or model
selection order. Pan exposes the stable query names ``first`` (five hours)
and ``secondary`` (one week) while retaining the provider snapshot verbatim
per window.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_WINDOWS = (
    ("first", "primary", "5h", "five_hour"),
    ("secondary", "secondary", "week", "weekly"),
)


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first explicitly provided value, preserving zero values."""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


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
    name: str,
    label: str,
    raw: Any,
) -> dict[str, Any]:
    """Build one explicit window result without inventing absolute quotas."""
    if not isinstance(raw, dict):
        raw = None

    used_percent = raw.get("usedPercent") if raw is not None else None
    return {
        "key": key,
        "name": name,
        "label": label,
        "providerKey": provider_key,
        "status": "available" if raw is not None else "missing",
        "usage": {
            # The current app-server payload provides this percentage, but
            # does not reliably provide absolute used/remaining/limit values.
            "usedPercent": used_percent,
            "remainingPercent": _remaining_percent(used_percent),
            "used": _first_value(raw, ("used", "usedAmount", "usedTokens")) if raw else None,
            "remaining": _first_value(raw, ("remaining", "remainingAmount", "remainingTokens")) if raw else None,
            "limit": _first_value(raw, ("limit", "limitAmount", "limitTokens")) if raw else None,
        },
        "windowDurationMins": raw.get("windowDurationMins") if raw else None,
        "resetsAt": raw.get("resetsAt") if raw else None,
        # Preserve provider fields so newer Codex payloads remain queryable
        # before Pan adds a dedicated mapping for them.
        "raw": deepcopy(raw) if raw is not None else None,
    }


def format_codex_quota(
    rate_limits: dict[str, Any],
    *,
    session_id: str,
    worker_id: str,
    updated_at: str | None,
    requested_window: str = "all",
) -> dict[str, Any]:
    """Return the stable API/MCP shape for one live Codex rate-limit snapshot."""
    windows = {
        key: _window_snapshot(key, provider_key, name, label, rate_limits.get(provider_key))
        for key, provider_key, name, label in _WINDOWS
    }
    result: dict[str, Any] = {
        "ok": True,
        "provider": "codex",
        "sessionId": session_id,
        "workerId": worker_id,
        "updatedAt": updated_at,
        "source": {
            "provider": "codex",
            "transport": "app-server",
            "event": "account/rateLimits/updated",
            "sessionId": session_id,
            "workerId": worker_id,
            "updatedAt": updated_at,
        },
        "windows": windows,
    }
    if requested_window != "all":
        result["window"] = requested_window
        result["windows"] = {requested_window: windows[requested_window]}
    return result


def validate_quota_window(window: str) -> bool:
    """Whether a caller supplied a supported quota window selector."""
    return window in {"all", "first", "secondary"}
