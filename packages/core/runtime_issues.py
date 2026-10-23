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

from packages.core.system_fields import (
    SYSTEM_FIELDS, SYSTEM_FIELD_NAMES, SystemFieldError, canonical_system,
)

SCHEMA_VERSION = 1
MAX_TEXT = 2048
MAX_RAW_KEYS = 32
_OBSERVER = None


def set_observer(callback):
    global _OBSERVER
    _OBSERVER = callback


def observe(mapping):
    if _OBSERVER is not None:
        _OBSERVER(mapping)

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
    text = re.sub(r'(?i)(authorization\s*[:=]\s*bearer\s+|(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*)[^\s,;"\']+',r'\1[redacted]',text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{16,}\b','[redacted]',text)
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


def _one_alias(container: dict, names: tuple[str, ...], field: str, origin: str):
    """Return one alias value or reject an ambiguous duplicate explicitly."""
    values = [(name, container[name]) for name in names if container.get(name) is not None]
    if not values:
        return None
    first = values[0][1]
    if any(value != first for _, value in values[1:]):
        aliases = "/".join(name for name, _ in values)
        raise SystemFieldError(f"Conflicting {origin} aliases for system field {field}: {aliases}")
    return first


def _metadata(raw: dict, supplied: dict | None) -> dict:
    source = {}
    supplied = supplied or {}
    # Host-supplied metadata is the only trust source for Pan identities.
    # Adapter-native thread/turn identities may additionally come from the
    # structured provider envelope. Message text is never parsed for identity.
    for name in SYSTEM_FIELD_NAMES:
        row = SYSTEM_FIELDS[name]
        names = (name,) + tuple(row["aliases"])
        value = _one_alias(supplied, names, name, "supplied")
        if value is None and row["trustedFrom"] == "adapter":
            for candidate in (raw, raw.get("params"), raw.get("error")):
                if not isinstance(candidate, dict):
                    continue
                value = _one_alias(candidate, names, name, "adapter")
                if value is not None:
                    break
        if value is not None:
            source[name] = value
    # Non-system correlation remains host-only. These values support quota
    # dedupe/validation but are not canonical system fields.
    extras = {
        "generation": ("generation", "generation_id"),
        "accountId": ("accountId", "account_id"),
        "taskSeq": ("taskSeq", "task_seq"),
        "executionSeq": ("executionSeq", "execution_seq"),
    }
    for key, names in extras.items():
        value = next((supplied[n] for n in names if supplied.get(n) is not None), None)
        if value is not None:
            source[key] = value
    # Owner epoch is host metadata, never a provider-payload assertion.
    epoch = supplied.get('runtimeEpoch', supplied.get('runtime_epoch'))
    if isinstance(epoch, str) and 0 < len(epoch) <= 256:
        source['runtimeEpoch'] = epoch
    return {key: _safe(value, 0) for key, value in source.items()
            if ((key in SYSTEM_FIELDS and isinstance(value, str) and 1 <= len(value) <= 256)
                or (key not in SYSTEM_FIELDS and type(value) in (str, int)
                    and (not isinstance(value, str) or len(value) <= 256)))}


def _structured(adapter: str, raw: dict) -> tuple[str | None, str | None]:
    native = _get(raw, ("error", "codexErrorInfo"), ("params", "error", "codexErrorInfo"))
    if adapter == "codex" and native == "usageLimitExceeded":
        return "codex_quota_exhausted", "structured"
    if adapter == "codex" and native == "serverOverloaded":
        return "codex_capacity", "structured"
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
        if re.fullmatch(r"(?:usage|plan) limit (?:has been )?(?:reached|exhausted)[.!]?", value):
            return "codex_quota_exhausted"
        if value == "selected model is at capacity. please try a different model.":
            return "codex_capacity"
    if adapter == "cbc" and value in ('rate limited','too many requests'):
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
    if raw.get('type') == 'codex.item_error' or raw.get('kind') == 'tool_failure':
        classification, confidence = 'unknown', 'unknown'
    correlation = _metadata(raw, metadata)
    token = json.dumps({"adapter": adapter, "raw": _safe(raw), "correlation": correlation}, sort_keys=True, ensure_ascii=False)
    issue_id = "ri_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    observation = {"rawType": _safe(raw.get("type")), "code": _safe(_get(raw, ("code",), ("error", "code"))),
                   "status": _safe(_get(raw, ("status",), ("error", "status"))), "text": text,
                   "raw": _safe(raw)}
    result = RuntimeIssue(SCHEMA_VERSION, issue_id, adapter, classification, confidence,
                          observation, correlation, dict(delivery or {"observed": True, "classified": True,
                          "persisted": False, "delivered": False, "deduped": False}))
    event = {"id": issue_id, "type": "runtime.issue.v1",
             "schemaVersion": SCHEMA_VERSION, "source": {"type": "core", "id": "runtime"},
             "occurredAt": None, "payload": asdict(result)}
    system = canonical_system(correlation, allow_unknown=True)
    if system:
        event["system"] = system
    return {"issue": asdict(result), "event": event}


def runtime_issue_event_schema() -> dict:
    """JSON schema for the Event payload accepted by trusted core admission."""
    return {
        "type": "object",
        "required": ["schemaVersion", "issueId", "adapter", "classification",
                     "confidence", "observation", "correlation", "delivery"],
        "properties": {
            "schemaVersion": {"const": SCHEMA_VERSION},
            "issueId": {"type": "string", "minLength": 1, "maxLength": 128},
            "adapter": {"type": ["string", "null"], "maxLength": 64},
            "classification": {"type": "string", "minLength": 1, "maxLength": 128},
            "confidence": {"type": "string", "minLength": 1, "maxLength": 32},
            "observation": {"type": "object"},
            "correlation": {"type": "object"},
            "delivery": {"type": "object"},
        },
        "additionalProperties": False,
    }


def runtime_issue_schema() -> dict:
    return {"version": 1, "eventType": "runtime.issue.v1", "classification": [
        "codex_quota_exhausted", "codex_capacity", "cbc_rate_limited", "provider_rate_limited", "unknown"],
        "confidence": ["structured", "adapter_text", "unknown"],
        "requiredCorrelation": ["runtimeEpoch", "sessionId", "workerId", "generation", "taskId", "executionId", "queueItemId", "threadId", "turnId"],
        "systemFields": list(SYSTEM_FIELD_NAMES),
        "delivery": ["observed", "classified", "persisted", "delivered", "deduped"],
        "limits": {"textChars": MAX_TEXT, "rawKeys": MAX_RAW_KEYS}}
