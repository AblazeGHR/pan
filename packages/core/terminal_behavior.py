"""Persisted terminal Behavior policy for automatic manager reports."""
from __future__ import annotations

import copy

DEFAULT = {"autoReportByErrorType": {}}


def normalize(value, *, strict=False):
    if not isinstance(value, dict):
        if strict:
            raise ValueError("terminalBehavior must be an object")
        return copy.deepcopy(DEFAULT)
    if set(value) - set(DEFAULT):
        if strict:
            raise ValueError("Unknown terminalBehavior field")
        return copy.deepcopy(DEFAULT)
    raw = value.get("autoReportByErrorType", {})
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("autoReportByErrorType must be an object")
        return copy.deepcopy(DEFAULT)
    result = {}
    for key, enabled in raw.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 128 or type(enabled) is not bool:
            if strict:
                raise ValueError("autoReportByErrorType entries must be boolean")
            continue
        result[key] = enabled
    return {"autoReportByErrorType": dict(sorted(result.items()))}


def auto_report_enabled(classification, policy, default=True):
    """Return the explicit error-type decision, preserving legacy by default."""
    if not isinstance(classification, str):
        return default
    return normalize(policy).get("autoReportByErrorType", {}).get(classification, default)
