"""Provider error -> bounded RuntimeIssue/Event mapping.

Classification is deliberately conservative: structured provider fields win,
then a small adapter-specific vocabulary.  Ordinary tool failures and generic
429/capacity messages remain unknown unless the adapter supplies an explicit
signal.  This module has no retry, account, restart, or recovery policy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any

SCHEMA_VERSION = 1
MAX_TEXT = 2048
MAX_RAW_KEYS = 32

_SECRET = re.compile(r"(?i)(authorization|cookie|api[-_]?key|access[-_]?token|refresh[-_]?token|password|secret)")
_CODEX_QUOTA_CODES = {"usage_limit_reached", "quota_exceeded", "quota_exhausted", "insufficient_quota"}
_CODEX_CAPACITY_CODES = {"capacity_exceeded", "model_capacity_exceeded", "server_overloaded", "overloaded"}
_CBC_RATE_CODES = {"rate_limited", "rate_limit_exceeded", "too_many_requests", "http_429", "429"}


@dataclass(frozen=True)
class RuntimeIssue:
    schemaVersion: int
    issueId: str
    adapter: str | None
    classification: str
    confidence: str
    observation: dict
    correlation: dict
    delivery: dict


def _clip(value: Any, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _safe(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "[depth-limited]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:MAX_RAW_KEYS]:
            key = str(key)
            result[key] = "[redacted]" if _SECRET.search(key) else _safe(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_safe(item, depth + 1) for item in value[:32]]
    if isinstance(value, str):
        return _clip(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clip(value)


def _get(raw: dict, *paths):
    for path in paths:
        value: Any = raw
        for part in path:
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value is not None:
            return value
    return None


def _metadata(raw: dict, supplied: dict | None) -> dict:
    source = dict(supplied or {})
    aliases = {
        "sessionId": ("sessionId", "session_id"), "workerId": ("workerId", "worker_id"),
        "generation": ("generation", "generation_id"), "taskId": ("taskId", "task_id"),
        "executionId": ("executionId", "execution_id"), "queueItemId": ("queueItemId", "queue_item_id"),
        "threadId": ("threadId", "thread_id"), "turnId": ("turnId", "turn_id"),
    }
    for key, names in aliases.items():
        if key not in source:
            for name in names:
                value = _get(raw, (name,), ("params", name), ("error", name))
                if value is not None:
                    source[key] = value
                    break
    return {key: _safe(value, 0) for key, value in source.items() if value is not None}


def _structured(adapter: str, raw: dict) -> tuple[str | None, str | None]:
    code = _get(raw, ("code",), ("error", "code"), ("error", "type"), ("error", "errorCode"),
                ("params", "error", "code"), ("params", "error", "type"))
    status = _get(raw, ("status",), ("error", "status"), ("error", "statusCode"),
                  ("codexErrorInfo", "responseStreamDisconnected", "httpStatusCode"),
                  ("error", "codexErrorInfo", "responseStreamDisconnected", "httpStatusCode"))
    code_norm = str(code).strip().lower() if code is not None else None
    if adapter == "codex":
        if code_norm in _CODEX_QUOTA_CODES:
            return "codex_quota_exhausted", "structured"
        if code_norm in _CODEX_CAPACITY_CODES:
            return "codex_capacity", "structured"
    if adapter == "cbc" and (code_norm in _CBC_RATE_CODES or status == 429):
        return "cbc_rate_limited", "structured"
    if code_norm in {"rate_limit", "rate_limited", "too_many_requests"} or status == 429:
        return "provider_rate_limited", "structured"
    return None, None


def _text(raw: dict) -> str:
    values = [_get(raw, ("error_text",), ("errorText",), ("message",), ("error", "message"), ("error",))]
    return next((_clip(v) for v in values if isinstance(v, str) and v.strip()), "")


def _strict_text(adapter: str, text: str) -> str | None:
    value = " ".join(text.lower().split())
    # A bare HTTP status or generic 429 prose is insufficient to identify
    # account quota; providers and gateways use it for transient throttling.
    if "429" in value or "http 429" in value:
        return None
    if adapter == "codex":
        if re.search(r"\b(?:usage|plan)\s+limit\s+(?:has\s+been\s+)?(?:reached|exhausted)\b", value) or re.search(r"\bquota\s+(?:has\s+been\s+)?(?:exhausted|exceeded)\b", value):
            return "codex_quota_exhausted"
        if re.search(r"\b(?:model\s+)?capacity\s+(?:has\s+been\s+)?(?:reached|exceeded)\b", value):
            return "codex_capacity"
    if adapter == "cbc" and re.search(r"\b(?:rate\s+limit(?:ed)?|too\s+many\s+requests)\b", value):
        return "cbc_rate_limited"
    return None


def map_runtime_issue(raw: dict, *, adapter: str | None = None, metadata: dict | None = None,
                      delivery: dict | None = None) -> dict:
    if not isinstance(raw, dict):
        raw = {"message": _clip(raw)}
    adapter = adapter or str(raw.get("adapter") or raw.get("provider") or "unknown").lower()
    classification, confidence = _structured(adapter, raw)
    text = _text(raw)
    if classification is None:
        classification = _strict_text(adapter, text) or "unknown"
        confidence = "adapter_text" if classification != "unknown" else "unknown"
    correlation = _metadata(raw, metadata)
    token = json.dumps({"adapter": adapter, "raw": _safe(raw), "correlation": correlation}, sort_keys=True, ensure_ascii=False)
    issue_id = "ri_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    observation = {"rawType": _safe(raw.get("type")), "code": _safe(_get(raw, ("code",), ("error", "code"))),
                   "status": _safe(_get(raw, ("status",), ("error", "status"))), "text": text,
                   "raw": _safe(raw)}
    result = RuntimeIssue(SCHEMA_VERSION, issue_id, adapter, classification, confidence,
                          observation, correlation, dict(delivery or {"observed": True, "classified": True,
                          "persisted": False, "delivered": False, "deduped": False}))
    return {"issue": asdict(result), "event": {"id": issue_id, "type": "runtime.issue.v1",
            "schemaVersion": SCHEMA_VERSION, "source": {"type": "core", "id": "runtime-issues"},
            "occurredAt": None, "payload": asdict(result)}}


def runtime_issue_schema() -> dict:
    return {"version": 1, "eventType": "runtime.issue.v1", "classification": [
        "codex_quota_exhausted", "codex_capacity", "cbc_rate_limited", "provider_rate_limited", "unknown"],
        "confidence": ["structured", "adapter_text", "unknown"],
        "requiredCorrelation": ["sessionId", "workerId", "generation", "taskId", "executionId", "queueItemId", "threadId", "turnId"],
        "delivery": ["observed", "classified", "persisted", "delivered", "deduped"],
        "limits": {"textChars": MAX_TEXT, "rawKeys": MAX_RAW_KEYS}}
