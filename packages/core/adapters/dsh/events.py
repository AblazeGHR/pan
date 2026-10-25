"""DSH (DeepSeek Harness) ``--json`` 事件模型 → Pan worker 事件模型。

DSH headless 的 ``--json`` 逐行输出一套自己的事件词表（源码证据：
``@deepseek-ai/dsh-headless/lib/json-stream-*.js``）：

- ``{"type":"session","sessionId":...,"cwd":...}``：流的第一行，携带本 run 的
  Session 身份（``--session-id`` 未指定时是新生成的 ``session-<uuid>``）；
- ``{"type":"status","phase":"turn_start"|"step_start"|"step_end"|"turn_end"}``：
  ``step_end`` 可能带 ``usage``，``turn_end`` 必带 ``reason``
  （``reason.kind`` ∈ completed / blocked / aborted / error / max-tokens，
  error 时 ``reason.error.{message,code}``）；
- ``{"type":"text"|"thinking","text":...}``：**step 提交时**一次性给出，不是逐
  token 增量；
- ``{"type":"tool_call","callId","tool","input"}`` /
  ``{"type":"tool_result","callId","status":"completed"|"error","result"}``；
- ``{"type":"final","text":...}``：进程正常收尾时的最终答案（不截断）；
- ``{"type":"error","message":...}``：runner 级失败，**没有** ``final``。

Pan 的 worker 只认 adapter 的 ``parse_event`` 系列方法，且前端对 ``assistant`` /
``thinking`` 形状有专门的规整逻辑（packages/web/src/hooks/useWebSocket.ts
``extractBlocks``）。这里把 DSH 事件翻成 cbc 同形的 Pan 事件，使：

1. ``system/init`` 进入 worker 的 init 分支（写 ``cli_session_id``，标记 worker
   ready）；
2. ``assistant`` 事件沿用 cbc 的 ``message.content`` 块形状，历史与实时渲染
   都与 cbc 一致；
3. 终止事件统一合成 ``result``（DSH 没有 result 事件，且 worker 必须收到一个
   才能结束本轮）。

本模块被 ``wrapper.py``（活进程桥接）与 ``adapter.py``（防御性再解析/测试）
共用，避免两处翻译逻辑漂移。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# turn_end.reason.kind 中表示"本轮没有正常完成"的取值。
FAILURE_REASON_KINDS = ("blocked", "aborted", "error", "max-tokens")


@dataclass
class TurnState:
    """一次 DSH run 的累积状态（wrapper 每个任务新建一个）。"""

    session_id: str = ""
    cwd: str = ""
    turn: int | None = None
    last_text: str = ""
    final_text: str | None = None
    run_error: str | None = None
    turn_reason: dict | None = None
    usage: dict = field(default_factory=dict)

    def add_usage(self, usage) -> None:
        """累计 step_end 的 usage（同一 run 可能多 step）。"""
        if not isinstance(usage, dict):
            return
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            self.usage[key] = int(self.usage.get(key, 0)) + int(value)


def pan_raw_usage(native_usage: dict | None) -> dict:
    """DSH usage → Pan 账本字段（与 opencode/kimi 的 rawUsage 同名字段）。

    DSH 的 ``inputTokens`` 是含缓存的输入总量（其投影缓存另记
    ``uncachedInputTokens``），Pan 账本把 prompt/completion 与 cache read/write
    分开统计，故这里做减法（下限 0），避免缓存 token 被计入两次。
    """
    native = native_usage if isinstance(native_usage, dict) else {}

    def _int(key: str) -> int:
        value = native.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return max(0, int(value))

    cache_read = _int("cacheReadTokens")
    cache_write = _int("cacheWriteTokens")
    prompt = max(0, _int("inputTokens") - cache_read - cache_write)
    completion = _int("outputTokens")
    total = _int("totalTokens")
    if not total:
        total = prompt + cache_read + cache_write + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": _int("reasoningTokens"),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": total,
    }


def _assistant_event(content: list[dict]) -> dict:
    """构造 cbc 形状的 assistant 事件（前端 extractBlocks 直接可渲染）。"""
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _reason_message(reason: dict) -> tuple[str, dict]:
    """把 turn_end.reason 转成 (人类可读错误, 结构化 error)。"""
    kind = str(reason.get("kind") or "")
    error = reason.get("error") if isinstance(reason.get("error"), dict) else {}
    detail = str(error.get("message") or "").strip()
    code = str(error.get("code") or "").strip()
    if kind == "error":
        message = detail or "DSH turn failed"
        if code and code not in message:
            message = f"{message} (code: {code})"
        return message, dict(error or {"message": message})
    if kind == "max-tokens":
        return "DSH turn stopped at the model output token limit", {"kind": kind}
    if kind == "aborted":
        cancel = reason.get("reason")
        detail = cancel if isinstance(cancel, str) else ""
        return f"DSH turn aborted{': ' + detail if detail else ''}", {"kind": kind}
    if kind == "blocked":
        return "DSH turn blocked before the first step", {"kind": kind}
    return f"DSH turn ended with reason {kind or 'unknown'}", {"kind": kind}


def map_event(raw: dict, state: TurnState) -> list[dict]:
    """把一行 DSH 事件翻译成 0..n 个 Pan worker 事件，并更新 *state*。

    未知事件返回空列表（DSH 会新增事件类型；不猜测语义，也不让整轮失败）。
    """
    if not isinstance(raw, dict):
        return []
    etype = raw.get("type")

    if etype == "session":
        state.session_id = str(raw.get("sessionId") or state.session_id or "")
        state.cwd = str(raw.get("cwd") or state.cwd or "")
        # cbc 同形 init：worker 据此写 cli_session_id 并标记 worker ready。
        return [{
            "type": "system",
            "subtype": "init",
            "session_id": state.session_id,
            "cwd": state.cwd,
        }]

    if etype == "status":
        phase = raw.get("phase")
        if raw.get("turn") is not None:
            state.turn = raw.get("turn")
        event = {"type": "status", "phase": phase}
        if raw.get("turn") is not None:
            event["turn"] = raw.get("turn")
        if raw.get("step") is not None:
            event["step"] = raw.get("step")
        if phase == "step_end":
            state.add_usage(raw.get("usage"))
            return [event]
        if phase == "turn_end":
            reason = raw.get("reason") if isinstance(raw.get("reason"), dict) else {}
            state.turn_reason = reason
            event["reason"] = reason
            return [event]
        return [event]

    if etype == "text":
        text = raw.get("text")
        if not isinstance(text, str) or not text:
            return []
        state.last_text = text
        return [_assistant_event([{"type": "text", "text": text}])]

    if etype == "thinking":
        text = raw.get("text")
        if not isinstance(text, str) or not text:
            return []
        return [_assistant_event([{"type": "thinking", "thinking": text}])]

    if etype == "tool_call":
        name = str(raw.get("tool") or "tool")
        tool_input = raw.get("input")
        if not isinstance(tool_input, (dict, list)):
            tool_input = {"input": tool_input} if tool_input is not None else {}
        return [_assistant_event([{"type": "tool_use", "name": name,
                                    "input": tool_input, "id": raw.get("callId")}])]

    if etype == "tool_result":
        return [{
            "type": "tool_result",
            "callId": raw.get("callId"),
            "status": raw.get("status"),
            "result": raw.get("result") or "",
        }]

    if etype == "final":
        text = raw.get("text")
        state.final_text = text if isinstance(text, str) else ""
        # 终止 result 由 wrapper 在进程退出后统一合成（需要退出码/stderr）。
        return []

    if etype == "error":
        message = raw.get("message")
        state.run_error = str(message) if message else "DSH runner error"
        return []

    return []


def terminal_result(state: TurnState, returncode: int | None,
                    stderr_tail: str = "") -> dict:
    """按 DSH 官方的成败判定合成 Pan ``result`` 事件。

    判定顺序（docs: dsh-headless "Machine-readable output" / "Exit mapping"）：
    1. runner 级 ``error`` 事件（无 final）→ 失败；
    2. ``turn_end.reason.kind`` 不在 completed → 失败（哪怕 exit code 与 final
       看起来正常，DSH 明确要求以 turn_end reason 为准）；
    3. 非 0 退出码且没有正常 final → 失败（附 stderr 尾部）；
    4. 其余为成功，正文优先取 ``final.text``，回退最后一条 text。
    """
    if state.run_error:
        return {
            "type": "result",
            "is_error": True,
            "result": state.run_error,
            "error": {"message": state.run_error},
            "error_text": state.run_error,
            "error_source": "dsh_error_event",
            "cli_session_id": state.session_id or None,
        }

    reason = state.turn_reason or {}
    kind = str(reason.get("kind") or "")
    if kind and kind != "completed":
        message, error = _reason_message(reason)
        return {
            "type": "result",
            "is_error": True,
            "result": message,
            "error": error,
            "error_text": message,
            "error_source": f"dsh_turn_{kind}",
            "cli_session_id": state.session_id or None,
        }

    if not kind and returncode not in (None, 0):
        tail = (stderr_tail or "").strip()[-2000:]
        message = f"dsh exited with code {returncode}" + (f":\n{tail}" if tail else "")
        return {
            "type": "result",
            "is_error": True,
            "result": message,
            "error": {"exitCode": returncode, "stderr": (stderr_tail or "").strip()},
            "error_text": (stderr_tail or "").strip() or message,
            "error_source": "dsh_stderr",
            "cli_session_id": state.session_id or None,
        }

    text = state.final_text
    if text is None:
        text = state.last_text
    return {
        "type": "result",
        "is_error": False,
        "result": text or "",
        "usage": pan_raw_usage(state.usage) if state.usage else None,
        "cli_session_id": state.session_id or None,
    }


def encode_message(text: str) -> bytes:
    """Pan worker → wrapper 的 stdin 帧（与 kimi/codex wrapper 同形）。"""
    return json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
