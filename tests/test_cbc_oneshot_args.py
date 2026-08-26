"""Pin CbcAdapter.oneshot_args against the old _consumer_mcp argv assembly.

Before worker.py's cbc-specific one-shot logic was moved into the adapter
(adapter-architecture P1 建议 4 / docs/design/adapter-p1-oneshot.md), the argv
was built inline in worker._consumer_mcp. This test replicates that exact
assembly and asserts oneshot_args produces an element-wise equal list, so the
refactor (step 3) cannot silently change behaviour.

Covers combinations: with/without resume, system_prompt, mcp, effort/permission.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core.adapters.cbc.adapter import CbcAdapter


# ── Reference: OLD worker._consumer_mcp argv assembly (pre-refactor) ──

def _old_build_oneshot_args(adapter, s, text):
    args = adapter.base_args_stream() if hasattr(adapter, "base_args_stream") else adapter.base_args()
    args.extend(adapter.model_args(s))
    args.extend(adapter.permission_mode_args(s))
    if hasattr(adapter, "effort_args"):
        args.extend(adapter.effort_args(s))
    if s.cli_session_id and adapter.supports_resume:
        args.extend(adapter.resume_args(s))
    if hasattr(adapter, "mcp_args"):
        args.extend(adapter.mcp_args(s))
    if s.system_prompt and not s.cli_session_id:
        args.extend(["--system-prompt", s.system_prompt])
    args.append(text)
    return args


def _make_session(**adapter_config):
    s = _sess.Session(id="ses_test", name="test", adapter="cbc", model="test-model")
    s.adapter_config = dict(adapter_config)
    return s


# mcp_args writes data/mcp-configs/<id>.mcp.json and returns --mcp-config <path>.
# Patch write_mcp_json to avoid the side-effect file write while staying
# deterministic: both old and new call the same mcp_args, so argv stays identical.
FAKE_MCP_PATH = "/fake/data/mcp-configs/ses_test.mcp.json"


def _patched_write_mcp_json(path, s):
    return str(path)


def _assert_eq(s, text):
    a = CbcAdapter()
    with patch(
        "packages.core.adapters.cbc.adapter.write_mcp_json", _patched_write_mcp_json
    ):
        new_args = a.oneshot_args(s, text)
        old_args = _old_build_oneshot_args(a, s, text)
    assert new_args == old_args, f"\nNEW: {new_args}\nOLD: {old_args}"


def test_oneshot_args_basic_no_mcp_no_resume():
    s = _make_session()
    _assert_eq(s, "hello world")
    print("PASS basic (no mcp, no resume)")


def test_oneshot_args_with_mcp():
    s = _make_session(mcp_servers=[{"name": "pan", "command": "x"}])
    _assert_eq(s, "hello world")
    print("PASS with mcp")


def test_oneshot_args_with_system_prompt_no_cli_id():
    s = _make_session(system_prompt="You are helpful.", mcp_servers=[{"name": "pan"}])
    _assert_eq(s, "hello world")
    print("PASS with system_prompt (no cli_session_id)")


def test_oneshot_args_system_prompt_skipped_when_resumed():
    # system_prompt must NOT be injected when cli_session_id already set
    s = _make_session(
        cli_session_id="existing-cbc-id",
        system_prompt="You are helpful.",
        mcp_servers=[{"name": "pan"}],
    )
    _assert_eq(s, "hello world")
    print("PASS system_prompt skipped when resumed")


def test_oneshot_args_with_resume_and_mcp():
    s = _make_session(
        cli_session_id="existing-cbc-id",
        mcp_servers=[{"name": "pan"}],
    )
    _assert_eq(s, "hello world")
    print("PASS with resume (cli_session_id) + mcp")


def test_oneshot_args_with_effort_and_permission():
    s = _make_session(
        mcp_servers=[{"name": "pan"}],
        always_thinking_enabled=True,
        effort="high",
    )
    _assert_eq(s, "hello world")
    print("PASS with effort(enabled)+permission")


def test_oneshot_args_prompt_is_last_arg():
    s = _make_session()
    a = CbcAdapter()
    with patch(
        "packages.core.adapters.cbc.adapter.write_mcp_json", _patched_write_mcp_json
    ):
        args = a.oneshot_args(s, "my prompt text")
    assert args[-1] == "my prompt text", f"prompt not last: {args}"
    print("PASS prompt is last arg")


def test_execution_modes_declared():
    assert CbcAdapter().execution_modes == ["stream", "oneshot"]
    from packages.core.adapters.kimi.adapter import KimiAdapter
    from packages.core.adapters.opencode.adapter import OpencodeAdapter
    assert KimiAdapter().execution_modes == ["stream"]
    assert OpencodeAdapter().execution_modes == ["stream"]
    print("PASS execution_modes declared correctly")


def test_takeover_command_no_system_prompt_reinject():
    """takeover resumes an existing session; it must NOT re-inject --system-prompt.

    Fix: system_prompt is injected only at first spawn of a fresh session (no
    cli_session_id). Re-injecting on takeover would duplicate the system prompt
    as a user message after --resume.
    """
    s = _make_session(
        cli_session_id="existing-cbc-id",
        system_prompt="You are a helpful assistant. Use Chinese.",
    )
    cmd = CbcAdapter().takeover_command(s)
    assert "--system-prompt" not in cmd, f"takeover must not inject --system-prompt: {cmd}"
    # takeover should still resume the existing session
    assert "--resume" in cmd and "existing-cbc-id" in cmd
    print("PASS takeover_command does not re-inject --system-prompt")


def test_takeover_command_empty_without_cli_session_id():
    """takeover requires an existing cli_session_id; fresh session returns []."""
    s = _make_session(system_prompt="You are helpful.")
    assert CbcAdapter().takeover_command(s) == []
    print("PASS takeover_command empty when no cli_session_id")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"RUN {t.__name__}...")
        t()
    print("ALL PASS")
