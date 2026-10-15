"""CBC command-hook client. Sends only turn/tool facts to its owning bridge."""
from __future__ import annotations

import json
import os
import sys
import socket
import urllib.error
import urllib.request


# Only fixed protocol reasons may cross into CLI diagnostics. Never render an
# exception, URL, response body or event value containing private capabilities.
HOOK_REJECTIONS = {
    "Wrong CBC Session": "wrong_session",
    "Missing or stale CBC turn identity": "stale_turn",
    "Continuation has no active CBC turn": "orphan_continuation",
    "Another CBC turn is active": "active_turn",
    "CBC prompt receipt does not match its input": "prompt_mismatch",
    "Native submit has no input owner": "missing_owner",
}


def failure(event, reason):
    print("CBC control channel unavailable: " + reason, file=sys.stderr)
    return 2 if event.get("hook_event_name") == "UserPromptSubmit" else 1


def main() -> int:
    try:
        # CBC writes JSON.stringify directly to a UTF-8 pipe. On Windows the
        # Python text wrapper can default to GBK, silently corrupting Chinese
        # prompts before the owning bridge compares their receipts.
        raw = getattr(sys.stdin, "buffer", sys.stdin).read()
        event = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        if not isinstance(event, dict):
            raise ValueError
    except (ValueError, UnicodeError):
        print("CBC control channel unavailable: invalid hook input", file=sys.stderr)
        return 2
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
    try:
        url = os.environ.get("PAN_CBC_HOOK_URL")
        token = os.environ.get("PAN_CBC_HOOK_TOKEN")
        if not url or not token:
            return failure(facts, "missing bridge environment; restart through Pan after confirmed kernel retirement")
        request = urllib.request.Request(url,
            data=json.dumps(facts, ensure_ascii=True).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        # Never inherit proxy routing for this process-local capability endpoint.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError
        if result:
            print(json.dumps(result, ensure_ascii=True))
        return 0
    except urllib.error.HTTPError as error:
        reason = {403: "bridge authentication rejected", 409: "bridge rejected hook"}.get(
            error.code, "bridge HTTP failure")
        if error.code == 409:
            try:
                code = json.loads(error.read(4096)).get("error")
                if code in HOOK_REJECTIONS.values():
                    reason += " (" + code + ")"
            except (ValueError, AttributeError, TypeError):
                pass
        return failure(facts, reason)
    except (TimeoutError, socket.timeout):
        return failure(facts, "bridge request timed out")
    except urllib.error.URLError as error:
        reason = "bridge request timed out" if isinstance(error.reason, TimeoutError) else "loopback connection failed; bridge may have retired"
        return failure(facts, reason)
    except Exception:
        # The CLI's submit hook must fail closed if its owner cannot confirm
        # the handoff. Other hooks must not stop a tool already executing.
        return failure(facts, "invalid bridge request or response")


if __name__ == "__main__":
    raise SystemExit(main())
