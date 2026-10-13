"""CBC command-hook client. Sends only turn/tool facts to its owning bridge."""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> int:
    event = json.load(sys.stdin)
    keys = ("hook_event_name", "session_id", "generation_id", "model", "prompt",
            "is_internal_continuation", "tool_name", "tool_use_id", "call_id",
            "tool_input", "tool_response", "last_assistant_message",
            "final_stop_reason", "notification_type", "source", "reason")
    facts = {key: event[key] for key in keys if key in event}
    # Tool output can exceed the HTTP envelope limit. Preserve lifecycle and
    # exit metadata; the resident PTY carries the full rendered output.
    for key in ("tool_input", "tool_response"):
        value = facts.get(key)
        encoded = json.dumps(value, ensure_ascii=True)
        if len(encoded) > 128 * 1024:
            projection = {"truncated": True, "preview": encoded[:16000]}
            if isinstance(value, dict):
                for field in ("exitCode", "exit_code", "signal", "interrupted", "is_error"):
                    if field in value:
                        projection[field] = value[field]
                raw = value.get("rawResponse")
                if isinstance(raw, dict):
                    projection["rawResponse"] = {field: raw[field] for field in
                        ("exitCode", "exit_code", "signal", "interrupted", "is_error") if field in raw}
            facts[key] = projection
    if isinstance(facts.get("last_assistant_message"), str):
        text = facts["last_assistant_message"]
        if len(text) > 16000:
            facts["last_assistant_message"] = text[:16000] + "\n[Structured response truncated; see CBC Terminal TUI]"
    request = urllib.request.Request(os.environ["PAN_CBC_HOOK_URL"],
        data=json.dumps(facts, ensure_ascii=True).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + os.environ["PAN_CBC_HOOK_TOKEN"]})
    # Never inherit proxy routing for this process-local capability endpoint.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            result = json.load(response)
        if result:
            print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception:
        # The CLI's submit hook must fail closed if its owner cannot confirm
        # the handoff. Other hooks must not stop a tool already executing.
        if facts.get("hook_event_name") == "UserPromptSubmit":
            print("CBC control channel unavailable", file=sys.stderr)
            return 2
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
