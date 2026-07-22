"""Tests for Kimi Code CLI adapter and session parser.

Uses the test sessions created during exploration; no real Kimi process is spawned
unless explicitly requested.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters import KimiAdapter
from packages.core.adapters.kimi import sessions as kimi_sessions
from packages.core import session as _sess


KIMI_TEST_WORKDIR = "C:/Users/14709/AppData/Local/Temp/kimi-test"


def _adapter() -> KimiAdapter:
    return KimiAdapter()


def test_adapter_metadata():
    a = _adapter()
    assert a.name == "kimi"
    assert "kimi-code/kimi-for-coding" in a.supported_models
    # supports_resume=False means Kimi won't replay history events on resume,
    # so worker.py should NOT skip assistant events.
    assert a.supports_resume is False
    # fork is implemented via file copy since Kimi CLI has no stable --fork flag.
    assert a.supports_fork is True
    print("PASS: adapter metadata")


def test_build_spawn_args():
    a = _adapter()
    s = _sess.Session(id="ses_test", name="test", adapter="kimi")
    args = a.build_spawn_args(s)
    assert args[0] == sys.executable
    assert "wrapper.py" in args[2]
    assert "--kimi-path" in args
    assert "--model" in args
    assert a.default_model in args
    print("PASS: build_spawn_args")


def test_build_spawn_args_with_resume():
    a = _adapter()
    s = _sess.Session(id="ses_test", name="test", adapter="kimi",
                      adapter_config={"cli_session_id": "session_abc123"})
    args = a.build_spawn_args(s)
    assert "--session-id" in args
    assert "session_abc123" in args
    print("PASS: build_spawn_args with resume")


def test_encode_user_message():
    a = _adapter()
    data = json.loads(a.encode_user_message("hello"))
    assert data == {"text": "hello"}
    print("PASS: encode_user_message")


def test_parse_assistant_event():
    a = _adapter()
    event = {"role": "assistant", "content": "hello"}
    assert a.is_assistant_event(event)
    blocks = a.extract_assistant_blocks(event)
    assert blocks == [{"role": "assistant", "content": "hello"}]
    print("PASS: parse assistant event")


def test_parse_tool_call_event():
    a = _adapter()
    event = {
        "role": "assistant",
        "tool_calls": [{
            "type": "function",
            "function": {"name": "Bash", "arguments": '{"command":"ls"}'},
        }],
    }
    blocks = a.extract_assistant_blocks(event)
    assert len(blocks) == 1
    assert blocks[0]["role"] == "tool"
    assert "Bash" in blocks[0]["content"]
    print("PASS: parse tool call event")


def test_init_event_extracts_session_id():
    a = _adapter()
    event = {
        "role": "meta",
        "type": "session.resume_hint",
        "session_id": "session_abc123",
    }
    assert a.is_init_event(event)
    assert a.extract_session_id(event) == "session_abc123"
    print("PASS: init event extracts session id")


def test_result_event():
    a = _adapter()
    event = {"role": "result", "is_error": False, "result": "done"}
    assert a.is_result_event(event)
    assert a.is_result_error(event) is False
    assert a.extract_result_text(event) == "done"
    print("PASS: result event")


def test_parse_kimi_history_from_test_session():
    if not os.path.exists(KIMI_TEST_WORKDIR):
        print("SKIP: parse_kimi_history (test workdir not found)")
        return
    history = kimi_sessions.parse_kimi_history(
        "session_935960e0-ebae-4f92-9389-4f7bce1cb11b",
        workdir=KIMI_TEST_WORKDIR,
    )
    roles = [h["role"] for h in history]
    assert "user" in roles
    assert "assistant" in roles
    print("PASS: parse_kimi_history from test session")


def test_get_raw_usage_from_test_session():
    if not os.path.exists(KIMI_TEST_WORKDIR):
        print("SKIP: get_raw_usage (test workdir not found)")
        return
    usage = kimi_sessions.get_raw_usage(
        "session_935960e0-ebae-4f92-9389-4f7bce1cb11b",
        workdir=KIMI_TEST_WORKDIR,
    )
    assert len(usage) > 0
    assert "rawUsage" in usage[0]
    assert "model" in usage[0]
    print("PASS: get_raw_usage from test session")


def test_fork_kimi_session():
    if not os.path.exists(KIMI_TEST_WORKDIR):
        print("SKIP: fork_kimi_session (test workdir not found)")
        return
    parent_id = "session_935960e0-ebae-4f92-9389-4f7bce1cb11b"
    new_id = kimi_sessions.fork_kimi_session(
        parent_id, "forked-test", workdir=KIMI_TEST_WORKDIR
    )
    assert new_id.startswith("session_")
    assert new_id != parent_id
    # Verify the forked session is registered and parseable
    sessions = kimi_sessions.list_kimi_sessions_for_cwd(KIMI_TEST_WORKDIR)
    assert any(s["session_id"] == new_id for s in sessions)
    history = kimi_sessions.parse_kimi_history(new_id, workdir=KIMI_TEST_WORKDIR)
    assert len(history) > 0
    print("PASS: fork_kimi_session")


if __name__ == "__main__":

    test_adapter_metadata()
    test_build_spawn_args()
    test_build_spawn_args_with_resume()
    test_encode_user_message()
    test_parse_assistant_event()
    test_parse_tool_call_event()
    test_init_event_extracts_session_id()
    test_result_event()
    test_parse_kimi_history_from_test_session()
    test_get_raw_usage_from_test_session()
    test_fork_kimi_session()
    print("\n=== ALL KIMI ADAPTER TESTS PASSED ===")
