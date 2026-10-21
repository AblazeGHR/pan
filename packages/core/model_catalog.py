"""Credential-free projection of service model caches and declared capabilities.

Do not invoke arbitrary adapter properties: model discovery and multiplier
refresh can read credentials or launch CLIs. Cold/unsafe sources stay unknown.
"""
import inspect
import math
import sys
import time


def _literal(adapter, name):
    value = inspect.getattr_static(adapter, name, None)
    return None if isinstance(value, property) or callable(value) else value


def _strings(value):
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
        return None
    return list(dict.fromkeys(value))


def _field(value=None, source="unknown", scope="adapter"):
    return {"value": value, "status": "declared" if value is not None else "unknown",
            "source": source if value is not None else "unknown", "scope": scope}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def adapter_catalog(adapter):
    """Return only allowlisted metadata; never credentials, paths or raw config."""
    name = _literal(adapter, "name")
    cached = _strings(_literal(adapter, "_cached_models"))
    models = cached if cached is not None else _strings(_literal(adapter, "supported_models"))
    source = "adapter._cached_models" if cached is not None else "adapter.supported_models"
    freshness = {"status": "unknown", "ageSeconds": None, "ttlSeconds": None,
                 "sourceUpdatedAt": None}
    if cached is not None:
        stamp = _literal(adapter, "_models_cached_at")
        if stamp is None:
            stamp = _literal(adapter, "_cached_models_ts")
        ttl = _literal(adapter, "_MODELS_TTL_SEC")
        if ttl is None:
            module = sys.modules.get(type(adapter).__module__)
            ttl = vars(module).get("_MODEL_CACHE_TTL") if module else None
        now = time.monotonic()
        if _number(stamp) and stamp > 0 and stamp <= now and _number(ttl) and ttl > 0:
            age = now - stamp
            freshness.update(status="fresh" if age < ttl else "stale",
                             ageSeconds=round(age, 3), ttlSeconds=ttl)
    # Only explicit opt-in hooks are allowed to read non-secret model metadata.
    # Current Codex hook reuses its model cache parser, never auth/config files.
    metadata = {}
    hook = inspect.getattr_static(adapter, "model_catalog_metadata", None)
    if inspect.isfunction(hook):
        try:
            result = hook(adapter)
            if isinstance(result, dict):
                metadata = result
        except Exception:
            pass  # No exception text: it may contain provider paths/secrets.
    efforts = metadata.get("modelEfforts", _literal(adapter, "model_efforts"))
    efforts = efforts if isinstance(efforts, dict) else {}
    multipliers = _literal(adapter, "model_credit_multipliers")
    multipliers = multipliers if isinstance(multipliers, dict) else {}
    rows = []
    for model in models or []:
        levels = _strings(efforts.get(model))
        multiplier = multipliers.get(model)
        rows.append({
            "id": model,
            "effort": _field(levels, "adapter.model_efforts", "model"),
            "creditMultiplier": {
                **_field(multiplier if _number(multiplier) else None,
                         "adapter.model_credit_multipliers", "model"),
                "freshness": "unknown",
            },
        })
    capabilities = {}
    for public, attribute in (
        ("resume", "supports_resume"), ("fork", "supports_fork"),
        ("nativeSteer", "supports_native_steer"),
        ("nativeInterrupt", "supports_native_interrupt"),
        ("steerReceipt", "supports_steer_receipt"),
    ):
        value = _literal(adapter, attribute)
        capabilities[public] = _field(value if type(value) is bool else None, f"adapter.{attribute}")
    execution_modes = _strings(_literal(adapter, "execution_modes"))
    if execution_modes is not None:
        execution_modes = [mode for mode in execution_modes if mode != "oneshot"]
    return {
        "adapter": name,
        "models": rows,
        "modelList": {"status": "cached" if cached is not None else "declared" if models is not None else "unknown",
                      "source": source if models is not None else "unknown",
                      "origin": "unknown", "freshness": freshness},
        "defaultModel": _field(_literal(adapter, "default_model") if isinstance(_literal(adapter, "default_model"), str) else None,
                               "adapter.default_model"),
        "adapterEffortValues": _field(_strings(_literal(adapter, "effort_values")), "adapter.effort_values"),
        "supportedSettings": _field(_strings(_literal(adapter, "supported_settings")), "adapter.supported_settings"),
        "executionModes": _field(execution_modes, "adapter.execution_modes"),
        "capabilities": capabilities,
    }
