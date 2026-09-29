"""Strict validators for reusable scheduled action templates."""

from __future__ import annotations

import re


_QQ_ID = re.compile(r"[1-9][0-9]{4,19}\Z")


def normalize_scheduled_qq_action(action: object) -> tuple[dict | None, str | None]:
    """Validate and normalize the fixed QQ text action schema.

    The action contains only a selected contact identity. Message text stays in
    the Job's top-level ``text`` field, and the plugin endpoint is selected by
    the server rather than supplied by the Job.
    """
    if not isinstance(action, dict) or action.get("api") != "send_qq":
        return None, "send_qq action must contain api=send_qq"
    unknown = sorted(set(action) - {"api", "args"})
    if unknown:
        return None, f"unsupported send_qq action field(s): {', '.join(unknown)}"
    args = action.get("args")
    if not isinstance(args, dict):
        return None, "send_qq action requires an args object"
    unknown = sorted(set(args) - {"targetType", "targetId", "botUin"})
    if unknown:
        return None, f"unsupported send_qq args field(s): {', '.join(unknown)}"

    target_type = args.get("targetType")
    if not isinstance(target_type, str) or target_type not in {"user", "group"}:
        return None, "send_qq args.targetType must be user or group"
    target_id = args.get("targetId")
    if not isinstance(target_id, str) or not _QQ_ID.fullmatch(target_id):
        return None, "send_qq args.targetId must be a valid QQ ID"

    normalized = {"api": "send_qq", "args": {
        "targetType": target_type,
        "targetId": target_id,
    }}
    if "botUin" in args:
        bot_uin = args["botUin"]
        if not isinstance(bot_uin, str) or not _QQ_ID.fullmatch(bot_uin):
            return None, "send_qq args.botUin must be a valid QQ bot ID when provided"
        normalized["args"]["botUin"] = bot_uin
    return normalized, None
