"""Session 配置的 adapter 能力校验（统一 helper）。

所有 Session 配置写入口（POST /api/sessions、PATCH /api/sessions、
POST /api/spawn、handoff、worker settings、WS spawn、MCP
session_create/session_update/agent_spawn）共用同一组规则：

- adapter 必须已注册（未知 adapter 给出可用列表，而不是 KeyError→500）；
- model 必须在 adapter 声明的 ``supported_models`` 内（列表非空时）；
- permissionMode 必须在 adapter 声明的 ``permission_modes`` 值域内；
- effort 必须在 adapter 声明（且该 setting 被支持）的值域内；codex 额外按
  ``model_efforts`` 收窄到具体 model 的值域；
- thinking / effort / maxThinkingTokens 等设置项按 ``supported_settings``
  声明核验：adapter 未声明的能力拒绝写入 Session（不再伪成功持久化或
  静默忽略）。

所有错误都是 ``AdapterCapabilityError``（ValueError 子类），message 面向
Agent，总是包含 adapter 名与参数值，例如::

    adapter 'codex' does not support model 'glm-5.3-flash'. Available models: ...

既有语义保留（不误杀）：
- 显式请求（请求体里带值的字段）→ 硬校验，非法直接拒绝；
- config.json / 模板的回退值 → 宽容守卫（非法则丢弃回退到 adapter 默认），
  与既有 stale-model guard 语义一致（见 server._build_session_params）。
"""

from __future__ import annotations

import logging

from .base import CliAdapter
from .registry import get_adapter

_log = logging.getLogger(__name__)

# MCP server 传输类型白名单（manifest 声明 + 落盘到 adapter 的 mcp.json）。
VALID_MCP_TRANSPORTS = ("stdio", "http", "sse")


class AdapterCapabilityError(ValueError):
    """Session 配置与 adapter 能力不匹配（面向调用方的结构化错误）。"""


class UnknownAdapterError(AdapterCapabilityError):
    """请求的 adapter 未注册。"""


def resolve_adapter(name: str) -> CliAdapter:
    """按名取 adapter；未知名抛 UnknownAdapterError（含可用列表）。

    替代裸 ``registry.get_adapter``（KeyError → HTTP 500）在所有面向
    调用方的配置入口使用。
    """
    try:
        return get_adapter(name)
    except KeyError:
        from .registry import list_adapters

        available = ", ".join(a.name for a in list_adapters())
        raise UnknownAdapterError(
            f"Unknown adapter {name!r}. Available adapters: {available}"
        ) from None


def supported_settings(a: CliAdapter) -> set[str] | None:
    """Adapter 声明的设置项集合；未声明（None）时视为不设限（防御兜底）。"""
    declared = getattr(a, "supported_settings", None)
    return set(declared) if declared is not None else None


def require_setting(a: CliAdapter, key: str) -> None:
    """Adapter 未声明某设置项时抛错（拒绝写入 Session）。"""
    declared = supported_settings(a)
    if declared is not None and key not in declared:
        raise AdapterCapabilityError(
            f"adapter '{a.name}' does not support the '{key}' setting "
            f"(supported settings: {', '.join(sorted(declared)) or 'none'})"
        )


def is_valid_model(a: CliAdapter, model: str) -> bool:
    """model 是否在该 adapter 的可选列表内（列表为空时视为无法核验，放行）。"""
    models = a.supported_models
    return not models or model in models


def validate_model(a: CliAdapter, model: str) -> None:
    """显式 model 硬校验：不在 supported_models 内直接拒绝。"""
    models = a.supported_models
    if models and model not in models:
        shown = ", ".join(models[:20]) + ("…" if len(models) > 20 else "")
        raise AdapterCapabilityError(
            f"adapter '{a.name}' does not support model {model!r}. "
            f"Available models: {shown}"
        )


def validate_permission_mode(a: CliAdapter, mode: str) -> None:
    """显式 permissionMode 硬校验：不在声明的值域内直接拒绝。"""
    modes = list(getattr(a, "permission_modes", []) or [])
    values = [str(m.get("value", "")) for m in modes if isinstance(m, dict)]
    if values and mode not in values:
        shown = ", ".join(v or "(default)" for v in values)
        raise AdapterCapabilityError(
            f"adapter '{a.name}' does not support permission mode {mode!r}. "
            f"Allowed modes: {shown}"
        )


def validate_effort(a: CliAdapter, effort: str, model: str | None = None) -> None:
    """显式 effort 硬校验：setting 需被支持，值需在（按 model 收窄的）值域内。

    codex 的 ``model_efforts`` 非空且包含目标 model 时，按该 model 的
    reasoning levels 收窄；否则退回全局 ``effort_values``。
    """
    require_setting(a, "effort")
    per_model = getattr(a, "model_efforts", None) or {}
    per = per_model.get(str(model)) if model else None
    allowed = list(per) if per else list(a.effort_values)
    if not allowed:
        raise AdapterCapabilityError(
            f"adapter '{a.name}' does not support any effort value"
            + (f" for model {model!r}" if model else "")
        )
    if effort not in allowed:
        raise AdapterCapabilityError(
            f"effort {effort!r} is not supported by adapter '{a.name}'"
            + (f" for model {model!r}" if model else "")
            + f". Allowed values: {', '.join(allowed)}"
        )


def validate_output_mode(a: CliAdapter, mode: str) -> None:
    """显式 outputMode 硬校验：必须在 adapter 的 execution_modes 内。"""
    allowed = list(a.execution_modes)
    if mode not in allowed:
        raise AdapterCapabilityError(
            f"outputMode must be one of {allowed} for adapter '{a.name}', "
            f"got {mode!r}"
        )


def validate_session_settings(
    adapter_name: str,
    settings: dict,
    *,
    current_model: str | None = None,
) -> None:
    """校验一批显式 Session 设置（camelCase 键，与 API 请求体同形）。

    ``settings`` 只需包含调用方显式提供的键；值为 None/"" 视为清除，
    不做能力校验（清除一个不受支持的残留设置永远安全）。

    - model：须在 supported_models 内；
    - permissionMode：须在声明值域内；
    - alwaysThinkingEnabled：truthy 时 adapter 须声明 thinking；
    - effort：非空时按值域（codex 按 model 收窄）校验，收窄用的 model 取
      ``settings["model"]``（本次同时切换时以新 model 为准），否则
      ``current_model``（session 当前 model）；
    - maxThinkingTokens：目前没有任何 adapter 消费 → 一律拒绝，不再
      伪成功持久化；
    - outputMode：须在 execution_modes 内。
    """
    a = resolve_adapter(adapter_name)
    model = settings.get("model") or current_model

    if settings.get("model"):
        validate_model(a, str(settings["model"]))
    if settings.get("permissionMode"):
        validate_permission_mode(a, str(settings["permissionMode"]))
    if settings.get("alwaysThinkingEnabled"):
        require_setting(a, "thinking")
    if settings.get("effort"):
        validate_effort(a, str(settings["effort"]), model=model)
    if "maxThinkingTokens" in settings and settings["maxThinkingTokens"] is not None:
        require_setting(a, "maxThinkingTokens")
        value = settings["maxThinkingTokens"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AdapterCapabilityError(
                f"maxThinkingTokens must be a positive integer, "
                f"got {value!r} (adapter '{a.name}')"
            )
    if settings.get("outputMode"):
        validate_output_mode(a, str(settings["outputMode"]))


def sanitize_adapter_config(
    adapter_name: str,
    cfg: dict | None,
    *,
    model: str | None = None,
) -> dict:
    """把一份 adapter_config 清洗成目标 adapter 可接受的形态。

    用于 handoff 跨 adapter 复制设置：源 adapter 的配置（effort / thinking /
    output_mode / maxThinkingTokens）对目标 adapter 可能不成立。清除不受
    支持的键（交还 adapter 默认值），保留合法键。与显式请求不同，复制来的
    残值降级为默认是既有语义（参见 _apply_session_updates 的 model 切换
    effort 重置）。``model`` 为 B 会话的（新）model，供按 model 收窄的
    effort 校验。
    """
    a = resolve_adapter(adapter_name)
    out = dict(cfg or {})
    declared = supported_settings(a)
    if declared is not None:
        if out.get("effort"):
            try:
                validate_effort(a, str(out["effort"]), model=model)
            except AdapterCapabilityError:
                out["effort"] = ""
        if "thinking" not in declared:
            out.pop("always_thinking_enabled", None)
            out.pop("thinking", None)
    # maxThinkingTokens 无 adapter 消费，跨 adapter 复制一律丢弃。
    out.pop("max_thinking_tokens", None)
    if out.get("output_mode") and out["output_mode"] not in a.execution_modes:
        out.pop("output_mode")
    return out
