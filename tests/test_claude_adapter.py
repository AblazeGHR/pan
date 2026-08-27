"""Tests for the Claude Code (claude-cli) adapter and session provider.

Two layers:

1. Pure unit tests (always run): param building, shim resolution against
   synthetic fake npm shims, stdout event parsing, takeover, and the
   result-usage cache bridging extract_result_text → enrich_after_result.
2. Best-effort integration tests against the real probe session written during
   exploration (``~/.claude/projects/C--Users-14709-AppData-Local-Temp-claude-probe/
   3546578e-...jsonl``). These SKIP when the probe dir is absent so the suite
   stays green in CI / other machines.

No real ``claude`` process is spawned by this file.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters import ClaudeAdapter
from packages.core.adapters.claude import sessions as claude_sessions
from packages.core.adapters.claude.adapter import _resolve_claude_exe_from_shim
from packages.core import session as _sess


# ── Real probe session (written during exploration; best-effort) ──

CLAUDE_PROBE_SESSION_ID = "3546578e-b946-483d-9907-d6bbf80c08c4"
CLAUDE_PROBE_CWD = r"C:\Users\14709\AppData\Local\Temp\claude-probe"
CLAUDE_PROBE_PROJECT_DIR = (
    Path.home() / ".claude" / "projects" / "C--Users-14709-AppData-Local-Temp-claude-probe"
)


def _make_session(adapter_config=None, **kw):
    s = _sess.Session(id="ses_claudetest", name="claude-test", adapter="claude", **kw)
    s.adapter_config = dict(adapter_config or {})
    return s


# ── metadata ──

def test_adapter_metadata():
    a = ClaudeAdapter()
    assert a.name == "claude"
    # claude -p is a one-shot process → only oneshot declared
    assert a.execution_modes == ["oneshot"]
    assert "stream" not in a.execution_modes
    assert a.supports_resume is True
    # fork is implemented via JSONL copy (no native --fork)
    assert a.supports_fork is True
    assert len(a.supported_models) > 0
    # builtin models must include the common aliases + full ids
    for m in ("claude-opus-4-8", "claude-sonnet-4-5", "claude-haiku-4-5",
              "opus", "sonnet", "haiku"):
        assert m in a.supported_models
    assert a.default_permission_mode == "bypassPermissions"
    assert a.permission_modes  # non-empty list of {value,label}
    assert "" in a.effort_values
    assert a.supported_settings == ["model", "permissionMode", "effort"]
    print("PASS: adapter metadata")


# ── shim resolution (deterministic, synthetic) ──

def _write_fake_shim(tmp: Path, *, has_exe=True, has_cli_js=False, has_node=False):
    """Create a fake npm shim layout under *tmp* and return the shim path.

    Layout matches a real Windows npm global install:
      <tmp>/claude.CMD
      <tmp>/node_modules/@anthropic-ai/claude-code/bin/claude.exe   (if has_exe)
      <tmp>/node_modules/@anthropic-ai/claude-code/cli.js           (if has_cli_js)
      <tmp>/node.exe                                              (if has_node)
    """
    shim = tmp / "claude.CMD"
    shim.write_text("@echo off\n")
    pkg = tmp / "node_modules" / "@anthropic-ai" / "claude-code"
    if has_exe:
        (pkg / "bin").mkdir(parents=True, exist_ok=True)
        (pkg / "bin" / "claude.exe").write_text("PE")
    if has_cli_js:
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "cli.js").write_text("// entry\n")
    if has_node:
        (tmp / "node.exe").write_text("node")
    return shim


def test_resolve_shim_to_compiled_exe():
    with tempfile.TemporaryDirectory() as d:
        shim = _write_fake_shim(Path(d), has_exe=True)
        argv = _resolve_claude_exe_from_shim(str(shim))
        assert argv is not None
        assert argv[0].lower().endswith("claude.exe")
        # the resolved exe is the compiled binary, NOT the .CMD shim
        assert not argv[0].lower().endswith(".cmd")
        assert str(Path(d) / "node_modules") in argv[0]
    print("PASS: resolve .CMD shim → compiled claude.exe")


def test_resolve_shim_node_fallback():
    with tempfile.TemporaryDirectory() as d:
        # no compiled exe; fall back to node <cli.js>
        shim = _write_fake_shim(Path(d), has_exe=False, has_cli_js=True, has_node=True)
        argv = _resolve_claude_exe_from_shim(str(shim))
        assert argv is not None
        assert len(argv) == 2
        assert argv[0].lower().endswith("node.exe")
        assert argv[1].lower().endswith("cli.js")
    print("PASS: resolve .CMD shim → node cli.js fallback")


def test_resolve_shim_none_when_missing():
    with tempfile.TemporaryDirectory() as d:
        shim = _write_fake_shim(Path(d), has_exe=False, has_cli_js=False)
        # neither compiled exe nor cli.js present → caller must keep the shim
        assert _resolve_claude_exe_from_shim(str(shim)) is None
    print("PASS: resolve returns None when no real entry exists")


def test_runtime_argv_avoids_cmd_shim():
    """Integration: on a machine with claude installed, the resolved runtime
    argv prefix must NOT be the .CMD shim (which would garble Chinese args)."""
    which = shutil.which("claude")
    if not which:
        print("SKIP: test_runtime_argv_avoids_cmd_shim (claude not on PATH)")
        return
    argv = ClaudeAdapter()._claude_argv
    assert argv and argv[0]
    # the pitfall we must avoid: launching via cmd.exe through a .CMD shim
    assert not argv[0].lower().endswith((".cmd", ".bat")), \
        f"runtime argv still uses shim: {argv[0]}"
    print(f"PASS: runtime argv avoids shim → {argv[0]}")


# ── oneshot_args structure ──

FAKE_MCP_PATH = "/fake/data/mcp-configs/ses_claudetest.mcp.json"


def _patched_mcp_json(path, s):
    return {"pan": {"command": "x"}}


def test_oneshot_args_basic():
    a = ClaudeAdapter()
    s = _make_session()
    with patch(
        "packages.core.adapters.claude.adapter.write_mcp_json", _patched_mcp_json
    ):
        args = a.oneshot_args(s, "hello world")
    # base: -p stream-json verbose
    assert "-p" in args
    assert "--output-format" in args
    assert "stream-json" in args
    assert "--verbose" in args
    # no model configured → no --model
    assert "--model" not in args
    # default permission mode always injected (non-interactive safety)
    assert "--permission-mode" in args
    pm_idx = args.index("--permission-mode")
    assert args[pm_idx + 1] == "bypassPermissions"
    # no resume / effort / system-prompt / mcp
    assert "--resume" not in args
    assert "--effort" not in args
    assert "--system-prompt" not in args
    assert "--mcp-config" not in args
    # prompt is the LAST arg
    assert args[-1] == "hello world"
    print("PASS: oneshot_args basic (default permission, no extras)")


def test_oneshot_args_with_model_resume_effort_system_prompt():
    a = ClaudeAdapter()
    s = _make_session(
        adapter_config={"cli_session_id": "real-cli-id", "effort": "high"},
        model="claude-opus-4-8",
        system_prompt="You are a helpful assistant.",
    )
    with patch(
        "packages.core.adapters.claude.adapter.write_mcp_json", _patched_mcp_json
    ):
        args = a.oneshot_args(s, "do the thing")
    # model present
    mi = args.index("--model")
    assert args[mi + 1] == "claude-opus-4-8"
    # resume present (cli_session_id set)
    ri = args.index("--resume")
    assert args[ri + 1] == "real-cli-id"
    # effort present
    ei = args.index("--effort")
    assert args[ei + 1] == "high"
    # system_prompt MUST be skipped when resuming (would duplicate as user msg)
    assert "--system-prompt" not in args
    assert args[-1] == "do the thing"
    print("PASS: oneshot_args with model + resume + effort (system_prompt skipped)")


def test_oneshot_args_system_prompt_only_when_fresh():
    a = ClaudeAdapter()
    s = _make_session(system_prompt="System instructions here.")
    with patch(
        "packages.core.adapters.claude.adapter.write_mcp_json", _patched_mcp_json
    ):
        args = a.oneshot_args(s, "first message")
    # fresh session (no cli_session_id) → system-prompt injected
    assert "--system-prompt" in args
    spi = args.index("--system-prompt")
    assert args[spi + 1] == "System instructions here."
    print("PASS: oneshot_args injects --system-prompt only for fresh session")


def test_oneshot_args_with_mcp():
    a = ClaudeAdapter()
    s = _make_session(adapter_config={"mcp_servers": [{"name": "pan", "command": "x"}]})
    with patch(
        "packages.core.adapters.claude.adapter.write_mcp_json", _patched_mcp_json
    ):
        args = a.oneshot_args(s, "with mcp")
    assert "--mcp-config" in args
    print("PASS: oneshot_args injects --mcp-config when mcp_servers set")


def test_build_spawn_args_stream_defense():
    a = ClaudeAdapter()
    s = _make_session(model="claude-sonnet-4-5")
    with patch(
        "packages.core.adapters.claude.adapter.write_mcp_json", _patched_mcp_json
    ):
        args = a.build_spawn_args(s)
    assert "-p" in args and "stream-json" in args and "--verbose" in args
    assert "--model" in args and "claude-sonnet-4-5" in args
    print("PASS: build_spawn_args (stream-mode defense builder)")


# ── stdout event parsing ──

def test_parse_event_and_types():
    a = ClaudeAdapter()
    ev = a.parse_event(
        json.dumps({"type": "system", "subtype": "init",
                    "session_id": "S1", "model": "claude-opus-4-8"})
    )
    assert ev is not None
    assert a.event_type(ev) == "system"
    assert a.is_init_event(ev) is True
    assert a.extract_session_id(ev) == "S1"
    assert a.extract_model(ev) == "claude-opus-4-8"
    # garbage line → None
    assert a.parse_event("not json {{{") is None
    print("PASS: parse_event / init event extraction")


def test_parse_assistant_blocks_all_three():
    a = ClaudeAdapter()
    ev = {
        "type": "assistant",
        "message": {
            "model": "deepseek-ai/DeepSeek-V4-Flash",
            "content": [
                {"type": "thinking", "thinking": "Let me think..."},
                {"type": "text", "text": "The answer is 42."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
    }
    assert a.is_assistant_event(ev) is True
    blocks = a.extract_assistant_blocks(ev)
    roles = [b["role"] for b in blocks]
    assert roles == ["thinking", "assistant", "tool"]
    assert blocks[1]["content"] == "The answer is 42."
    assert "Bash" in blocks[2]["content"]
    assert '"command": "ls"' in blocks[2]["content"]
    print("PASS: parse_assistant_blocks (thinking/text/tool_use)")


def test_result_event_and_error_flag():
    a = ClaudeAdapter()
    ev = {"type": "result", "is_error": False, "result": "done", "session_id": "S2"}
    assert a.is_result_event(ev) is True
    assert a.is_result_error(ev) is False
    assert a.extract_result_text(ev) == "done"
    err = {"type": "result", "is_error": True, "result": "boom", "session_id": "S3"}
    assert a.is_result_error(err) is True
    print("PASS: result event parsing + error flag")


def test_result_usage_cache_bridges_enrich():
    """extract_result_text caches the result event's usage+cost; enrich_after_result
    pops it to fill raw_usage (cost is only available on stdout, not in JSONL)."""
    a = ClaudeAdapter()
    sid = "cache-bridge-sid"
    claude_sessions_get_raw = claude_sessions.get_raw_usage  # avoid accidental real IO
    result_ev = {
        "type": "result",
        "is_error": False,
        "result": "final text",
        "session_id": sid,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        },
        "total_cost_usd": 0.0123,
        "modelUsage": {"claude-opus-4-8": {"input_tokens": 100}},
        "timestamp": "2026-08-26T15:14:41.550Z",
    }
    # extract_result_text has the side-effect of caching usage under sid
    assert a.extract_result_text(result_ev) == "final text"
    s = _make_session(adapter_config={"cli_session_id": sid})
    with patch.object(claude_sessions, "get_raw_usage", claude_sessions_get_raw):
        entries = a.enrich_after_result(s)
    assert entries is not None and len(entries) == 1
    e = entries[0]
    assert e["model"] == "claude-opus-4-8"
    ru = e["rawUsage"]
    assert ru["prompt_tokens"] == 100
    assert ru["completion_tokens"] == 50
    assert ru["cache_read_tokens"] == 10
    assert ru["cache_write_tokens"] == 5
    assert ru["cost"] == 0.0123
    # cache consumed → second call falls back (no real JSONL for this sid → None)
    s2 = _make_session(adapter_config={"cli_session_id": sid})
    with patch.object(claude_sessions, "get_raw_usage", lambda *a, **k: None):
        assert a.enrich_after_result(s2) is None
    print("PASS: result usage cache bridges extract_result_text → enrich_after_result")


# ── takeover ──

def test_takeover_command_resume():
    a = ClaudeAdapter()
    s = _make_session(adapter_config={"cli_session_id": "cli-xyz"})
    cmd = a.takeover_command(s)
    assert "--resume" in cmd and "cli-xyz" in cmd
    # takeover must NOT re-inject --system-prompt
    assert "--system-prompt" not in cmd
    print("PASS: takeover_command resumes existing session, no --system-prompt")


def test_takeover_command_empty_without_cli_id():
    a = ClaudeAdapter()
    s = _make_session()
    assert a.takeover_command(s) == []
    print("PASS: takeover_command empty when no cli_session_id")


# ── sessions provider (real probe, best-effort) ──

def test_session_exists_probe():
    if not CLAUDE_PROBE_PROJECT_DIR.exists():
        print("SKIP: test_session_exists_probe (probe dir absent)")
        return
    assert claude_sessions.session_exists(CLAUDE_PROBE_SESSION_ID, CLAUDE_PROBE_CWD) is True
    assert claude_sessions.session_exists("no-such-session-id", CLAUDE_PROBE_CWD) is False
    print("PASS: session_exists (probe)")


def test_parse_history_probe():
    if not CLAUDE_PROBE_PROJECT_DIR.exists():
        print("SKIP: test_parse_history_probe (probe dir absent)")
        return
    history = claude_sessions.parse_history(CLAUDE_PROBE_SESSION_ID, CLAUDE_PROBE_CWD)
    roles = [h["role"] for h in history]
    assert "user" in roles
    assert "assistant" in roles
    # the probe replied with "PONG"
    assert any("PONG" in h["content"] for h in history if h["role"] == "assistant")
    print("PASS: parse_history (probe)")


def test_get_raw_usage_probe():
    if not CLAUDE_PROBE_PROJECT_DIR.exists():
        print("SKIP: test_get_raw_usage_probe (probe dir absent)")
        return
    usage = claude_sessions.get_raw_usage(CLAUDE_PROBE_SESSION_ID, CLAUDE_PROBE_CWD)
    assert len(usage) >= 1
    entry = usage[0]
    assert "rawUsage" in entry and "model" in entry
    ru = entry["rawUsage"]
    assert ru["prompt_tokens"] > 0
    assert ru["completion_tokens"] > 0
    # cost is not present in JSONL → 0
    assert ru["cost"] == 0.0
    print(f"PASS: get_raw_usage (probe) model={entry['model']}")


def test_get_session_title_probe():
    if not CLAUDE_PROBE_PROJECT_DIR.exists():
        print("SKIP: test_get_session_title_probe (probe dir absent)")
        return
    assert claude_sessions.get_session_title(CLAUDE_PROBE_SESSION_ID, CLAUDE_PROBE_CWD) == "Pong"
    print("PASS: get_session_title (probe) == 'Pong'")


def test_write_and_restore_custom_title_probe():
    if not CLAUDE_PROBE_PROJECT_DIR.exists():
        print("SKIP: test_write_and_restore_custom_title_probe (probe dir absent)")
        return
    original = claude_sessions.get_session_title(CLAUDE_PROBE_SESSION_ID, CLAUDE_PROBE_CWD)
    try:
        claude_sessions.write_custom_title(
            CLAUDE_PROBE_SESSION_ID, "PAN_TEST_TITLE_XYZ", CLAUDE_PROBE_CWD)
        assert claude_sessions.get_session_title(
            CLAUDE_PROBE_SESSION_ID, CLAUDE_PROBE_CWD) == "PAN_TEST_TITLE_XYZ"
    finally:
        # restore original so we never leave the real probe mutated
        claude_sessions.write_custom_title(
            CLAUDE_PROBE_SESSION_ID, original or "Pong", CLAUDE_PROBE_CWD)
    assert claude_sessions.get_session_title(
        CLAUDE_PROBE_SESSION_ID, CLAUDE_PROBE_CWD) == (original or "Pong")
    print("PASS: write_custom_title + restore (probe)")


def test_fork_session_probe_and_cleanup():
    if not CLAUDE_PROBE_PROJECT_DIR.exists():
        print("SKIP: test_fork_session_probe_and_cleanup (probe dir absent)")
        return
    new_id = claude_sessions.fork_session(
        CLAUDE_PROBE_SESSION_ID, "pan-fork-test", CLAUDE_PROBE_CWD)
    forked_path = CLAUDE_PROBE_PROJECT_DIR / f"{new_id}.jsonl"
    try:
        assert claude_sessions.session_exists(new_id, CLAUDE_PROBE_CWD) is True
        hist = claude_sessions.parse_history(new_id, CLAUDE_PROBE_CWD)
        assert len(hist) > 0
        assert claude_sessions.get_session_title(new_id, CLAUDE_PROBE_CWD) == "pan-fork-test"
    finally:
        # clean up the forked transcript so we don't pollute ~/.claude
        if forked_path.exists():
            forked_path.unlink()
    assert forked_path.exists() is False
    print("PASS: fork_session (probe) + cleanup")


def test_list_sessions_probe():
    if not CLAUDE_PROBE_PROJECT_DIR.exists():
        print("SKIP: test_list_sessions_probe (probe dir absent)")
        return
    sessions = claude_sessions.list_sessions(CLAUDE_PROBE_CWD)
    assert any(s["session_id"] == CLAUDE_PROBE_SESSION_ID for s in sessions)
    print(f"PASS: list_sessions (probe) → {len(sessions)} session(s)")


# ── encode_user_message ──

def test_encode_user_message():
    a = ClaudeAdapter()
    data = json.loads(a.encode_user_message("hello").decode("utf-8"))
    assert data == {"type": "user", "text": "hello"}
    print("PASS: encode_user_message")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n=== {'ALL PASS' if failed == 0 else f'{failed} FAILED'} "
          f"({len(tests)} tests) ===")
