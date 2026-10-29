"""Thin canonical Action adapter for the existing QQ plugin send path.

The host supplies its existing trusted sender (server._send_scheduled_qq).
Endpoint selection, default bot selection, plugin routing and raw receipts stay
owned by that path. This adapter does not create a transport, ledger, query or
retry protocol. The canonical executor/host owns effect intent and replay fences.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from packages.jobs.actions import normalize_scheduled_qq_action


class QQChannelProvider:
    """Translate typed Action arguments and map the original plugin receipt."""

    def __init__(self, sender: Callable[..., Awaitable[Mapping[str, Any]]]):
        self._sender = sender

    @staticmethod
    def receipt_outcome(raw: Mapping[str, Any]) -> str:
        """A transport failure or missing acknowledgement is never delivery.

        Legacy preflight error codes retain their rejected observation, but
        are NOT no-send proof and cannot authorize a retry. Only the original
        nonempty message identity acknowledges a successful QQ send.
        """
        if raw.get("ok") is True:
            message_id = raw.get("message_id")
            return "succeeded" if (isinstance(message_id, (str, int))
                and not isinstance(message_id, bool) and str(message_id).strip()) else "unknown"
        error = raw.get("error")
        if raw.get("ok") is False and isinstance(error, Mapping) and error.get("code") in {
                "unknown_bot_uin", "bot_not_connected", "invalid_target_type",
                "invalid_target_id", "empty_text"}:
            return "rejected"
        return "unknown"

    async def send(self, target_type: str, target_id: str, text: str, *,
                   bot_uin: str | None = None,
                   effect_key: Mapping[str, Any]) -> dict[str, Any]:
        args = {"targetType": target_type, "targetId": target_id}
        if bot_uin is not None:
            args["botUin"] = bot_uin
        _, error = normalize_scheduled_qq_action({"api": "send_qq", "args": args})
        if error or not isinstance(text, str) or not text:
            raise ValueError(error or "QQ text must be a nonempty string")
        # The original sender omits absent bot_uin on the wire. The plugin uses
        # its configured default channel, or validates an explicitly selected bot.
        raw = await self._sender(
            target_type="private" if target_type == "user" else "group",
            target_id=target_id, text=text, bot_uin=bot_uin,
        )
        # effect_key belongs to the executor. Do not forward it as a fictional
        # plugin deduplication token or claim a remote receipt query exists.
        if not isinstance(raw, Mapping):
            raise TypeError("QQ sender returned a non-object receipt")
        # Preserve plugin/channel error and acknowledgement semantics exactly.
        # TypedExecutor maps the existing {ok,error,message_id} shape.
        return dict(raw)
