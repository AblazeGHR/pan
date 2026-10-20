"""Read-only body projections. Never promote provider or positional ids to Pan ids."""
import json
import re

from .session import is_pan_message_id

DEFAULT_ROLES = ("user", "assistant", "tool")
ROLES = frozenset((*DEFAULT_ROLES, "thinking"))


def options(roles=None, tool_output="command_only"):
    selected = DEFAULT_ROLES if roles is None else roles
    if isinstance(selected, str):
        selected = selected.split(",") if selected else []
    if not isinstance(selected, (list, tuple)) or any(role not in ROLES for role in selected):
        raise ValueError("roles must contain only user, assistant, tool, thinking")
    if tool_output not in ("command_only", "include_output"):
        raise ValueError("tool_output must be command_only or include_output")
    return frozenset(selected), tool_output


def command_content(content):
    """Fail closed for opaque/truncated legacy tool displays.

    Canonical displays are Name(JSON). Only explicit command strings survive;
    arbitrary arguments may themselves embed output (Codex result/file items).
    Unknown calls retain their name, without guessing where their output ends.
    """
    match = re.fullmatch(r"([\w./:-]+)\((.*)\)", content, re.DOTALL)
    if not match:
        return "[tool call; command unavailable]"
    name, raw = match.groups()
    try:
        args = json.loads(raw)
    except (ValueError, TypeError):
        return f"{name}([command unavailable])"
    safe = {}
    if isinstance(args, dict):
        for key in ("command", "cmd"):
            if isinstance(args.get(key), str):
                safe[key] = args[key]
    return f"{name}({json.dumps(safe, ensure_ascii=False, separators=(',', ':'))})"


def project(row, index, roles, tool_output):
    if not isinstance(row, dict) or row.get("role") not in roles:
        return None
    content = row.get("content")
    if not isinstance(content, str) or not content.strip() or row.get("source") == "system_prompt":
        return None
    # An allowlist is intentional: parts/rawContent/output can duplicate bodies
    # of other kinds, even on a user or assistant row.
    result = {key: row[key] for key in ("role", "ts", "source", "taskId") if key in row}
    result["content"] = command_content(content) if row["role"] == "tool" and tool_output == "command_only" else content
    result["messageIndex"] = index
    if is_pan_message_id(row.get("messageId")):
        result["messageId"] = row["messageId"]
        result["identityKind"] = "persistent"
    else:
        result["identityKind"] = "legacy_unaddressable"
    return result
