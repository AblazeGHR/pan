"""执行模式解析（adapter-architecture P1 建议 4 / adapter-p1-oneshot.md §3）。

合并 adapter 声明的 ``execution_modes`` 与 session 的 ``output_mode`` → 实际
执行模式（``"stream"`` / ``"oneshot"``）。供 worker 与 server 共用，避免判定
逻辑分散与 cbc 特定矩阵泄漏。
"""

from __future__ import annotations

from .base import CliAdapter, SYSTEM_PROMPT_ARG_MAX_CHARS


def resolve_execution_mode(adapter: CliAdapter, s) -> str:
    """Worker 对某 session 实际使用的执行模式。

    规则：
    - ``output_mode`` 显式设置且 ∈ ``adapter.execution_modes`` → 直接使用；
    - 未设置 / 非法 / 空 → 默认：多模式优先 ``"stream"``（若支持），
      否则取 ``execution_modes`` 的唯一项。

    语义见 docs/design/adapter-p1-oneshot.md：
    - cbc = ["stream", "oneshot"] → 默认 "stream"（主流，含 stream+MCP）；
    - kimi/opencode = ["stream"] → 永远 "stream"（wrapper 长驻，内部一次性
      对 worker 透明）；
    - one-shot-only adapter = ["oneshot"] → 永远 "oneshot"（兜底 clamp 到它）。

    ``s`` 可为 None（无 session 时按 adapter 默认）。
    """
    modes = list(getattr(adapter, "execution_modes", ["stream"]) or ["stream"])
    requested = ""
    if s is not None:
        ac = getattr(s, "adapter_config", None) or {}
        requested = ac.get("output_mode") or ""
    requested = str(requested).strip()
    if requested in modes:
        selected = requested
    else:
        selected = "stream" if "stream" in modes else modes[0]

    # cbc/claude oneshot_args have to put --system-prompt in the native CLI
    # argv.  For an oversized prompt, prefer their stream transport instead;
    # worker can then deliver the prompt through stdin (or a file-aware
    # wrapper) and keep the body out of CreateProcess argv.  All current
    # adapters with oneshot support also expose stream, while one-shot-only
    # adapters retain their declared mode.
    prompt = getattr(s, "system_prompt", None) if s is not None else None
    if (selected == "oneshot" and "stream" in modes
            and isinstance(prompt, str) and len(prompt) > SYSTEM_PROMPT_ARG_MAX_CHARS):
        return "stream"
    return selected
