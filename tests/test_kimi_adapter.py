"""Tests for Kimi Code CLI adapter and session parser.

Uses the test sessions created during exploration; no real Kimi process is spawned
unless explicitly requested.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters import KimiAdapter
from packages.core.adapters.kimi import sessions as kimi_sessions
from packages.core import session as _sess


# Machine-local: points at real recorded Kimi test-session data. Kept as-is
# deliberately; the tests below do a real pytest.skip when the data is absent
# (no fake greens) — see _kimi_data.
KIMI_TEST_WORKDIR = "C:/Users/14709/AppData/Local/Temp/kimi-test"

_kimi_data = pytest.mark.skipif(
    not os.path.exists(KIMI_TEST_WORKDIR),
    reason="kimi test workdir absent (machine-local data)",
)


def _adapter() -> KimiAdapter:
    return KimiAdapter()


def test_adapter_metadata():
    a = _adapter()
    assert a.name == "kimi"
    assert len(a.supported_models) > 0
    # Kimi supports resume (replays history events on resume), so worker.py
    # skips assistant events to avoid duplication.
    assert a.supports_resume is True
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


def test_supports_spawn_system_prompt():
    """kimi wrapper 接受 worker 的 --system-prompt（首轮转 --agent-file）。

    回归背景：SMA(NoAdapter)+kimi 卡死根因 —— worker 曾对一切 stream+MCP
    session 强传 --system-prompt，kimi wrapper argparse 不认识直接 exit 2。
    """
    assert _adapter().supports_spawn_system_prompt is True


def test_build_spawn_args_passthrough_system_prompt():
    a = _adapter()
    s = _sess.Session(id="ses_test", name="test", adapter="kimi")
    args = a.build_spawn_args(s, extra_args=["--system-prompt", "You are SMA."])
    assert "--system-prompt" in args
    assert "You are SMA." in args


def test_wrapper_argparse_accepts_system_prompt():
    """wrapper argparse 必须接受 --system-prompt（原 bug 场景：unrecognized
    arguments -> exit 2 -> 会话永不回复）。"""
    from packages.core.adapters.kimi import wrapper as kimi_wrapper

    args = kimi_wrapper._build_arg_parser().parse_args([
        "--kimi-path", "kimi.exe",
        "--model", "moonshot-cn/kimi-k2.6",
        "--kimi-home", "D:/tmp/home",
        "--system-prompt", "You are SMA.",
    ])
    assert args.kimi_path == "kimi.exe"
    assert args.system_prompt == "You are SMA."
    assert args.session_id is None


def test_wrapper_argparse_accepts_system_prompt_file():
    from packages.core.adapters.kimi import wrapper
    args = wrapper._build_arg_parser().parse_args([
        "--kimi-path", "kimi",
        "--system-prompt-file", r"C:\work\.pan\system-prompts\ses-a.txt",
    ])
    assert args.system_prompt_file.endswith("ses-a.txt")


def test_wrapper_reads_utf8_prompt_file_and_removes_it(tmp_path):
    from packages.core.adapters.kimi import wrapper
    path = tmp_path / "prompt.txt"
    prompt = "中文\nemoji 😀\n"
    path.write_text(prompt, encoding="utf-8", newline="")
    assert wrapper._read_system_prompt_file(str(path)) == prompt
    assert not path.exists()


def test_wrapper_removes_prompt_file_when_read_fails(tmp_path):
    from packages.core.adapters.kimi import wrapper
    path = tmp_path / "broken-prompt.txt"
    path.write_bytes(b"not valid utf8: \xff")
    try:
        wrapper._read_system_prompt_file(str(path))
    except UnicodeDecodeError:
        pass
    else:
        raise AssertionError("invalid UTF-8 prompt should fail closed")
    assert not path.exists()


def test_write_agent_file(tmp_path):
    from packages.core.adapters.kimi.wrapper import _write_agent_file

    prompt = "你是 SMA。"
    path = _write_agent_file(prompt, str(tmp_path))
    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: pan-system-prompt" in text
    assert prompt.strip() in text
    assert Path(path).parent == tmp_path  # 写进隔离 HOME，不污染其它位置


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


@_kimi_data
def test_parse_kimi_history_from_test_session():
    history = kimi_sessions.parse_kimi_history(
        "session_935960e0-ebae-4f92-9389-4f7bce1cb11b",
        workdir=KIMI_TEST_WORKDIR,
    )
    roles = [h["role"] for h in history]
    assert "user" in roles
    assert "assistant" in roles
    print("PASS: parse_kimi_history from test session")


@_kimi_data
def test_get_raw_usage_from_test_session():
    usage = kimi_sessions.get_raw_usage(
        "session_935960e0-ebae-4f92-9389-4f7bce1cb11b",
        workdir=KIMI_TEST_WORKDIR,
    )
    assert len(usage) > 0
    assert "rawUsage" in usage[0]
    assert "model" in usage[0]
    print("PASS: get_raw_usage from test session")


@_kimi_data
def test_fork_kimi_session():
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


# ── kimi 2.3.1 实测回归项（2026-10-24，真实 CLI 端到端核验后固化）──────────


def test_fork_args_is_provider_owned_no_side_effect():
    """fork_args 不得自行 fork：worker branch 路径随后还会经 provider fork，
    两侧都做会双重 fork（每次 branch 留孤儿 kimi 会话）。对齐 claude/opencode/codex。"""
    a = _adapter()
    s = _sess.Session(id="ses_test", name="test", adapter="kimi",
                      adapter_config={"cli_session_id": "session_parent"})
    assert a.fork_args(s) == []
    assert s.cli_session_id == "session_parent"  # 未被改写


def test_tool_result_event_is_transient():
    """2.3.1 新增 role=tool 结果事件（tool_call_id + content）：既不是 assistant
    （不入历史块）也不是 result（不结束回合），worker 原样忽略。"""
    a = _adapter()
    event = {"role": "tool", "tool_call_id": "call_probe_1",
             "content": "PAN_MCP_OK\n<mcp-result-extras>...</mcp-result-extras>"}
    assert a.parse_event(json.dumps(event)) == event
    assert a.is_assistant_event(event) is False
    assert a.is_result_event(event) is False
    assert a.event_type(event) == "tool"


def test_meta_version_event_is_not_init():
    """init 事件只有 meta/session.resume_hint；system.version 等 meta 事件
    必须被 is_init_event 排除（worker 只在 init 事件提取 session_id）。"""
    a = _adapter()
    event = {"role": "meta", "type": "system.version", "version": "2.3.1"}
    assert a.is_init_event(event) is False
    assert a.extract_session_id(event) is None


def test_thinking_flat_content_event():
    """2.3.1 stream-json 的思考事件（role=thinking + content 字符串）。"""
    a = _adapter()
    event = {"role": "thinking", "content": "先分析一下"}
    assert a.is_assistant_event(event) is True
    assert a.extract_assistant_blocks(event) == [
        {"role": "thinking", "content": "先分析一下"}]


def test_takeover_command_with_and_without_isolated_home():
    a = _adapter()
    s = _sess.Session(id="ses_t", name="t", adapter="kimi",
                      adapter_config={"cli_session_id": "session_x"})
    cmd = a.takeover_command(s)
    assert cmd[-2:] == ["-S", "session_x"]
    assert "kimi" in os.path.basename(cmd[0]).lower()

    s2 = _sess.Session(id="ses_t2", name="t2", adapter="kimi",
                       adapter_config={"cli_session_id": "session_x",
                                       "kimi_home_dir": "D:/data/kimi-homes/ses_t2"})
    cmd2 = a.takeover_command(s2)
    assert cmd2[0] == sys.executable
    assert "KIMI_CODE_HOME" in cmd2[2]
    assert cmd2[-2:] == ["-S", "session_x"]


def test_prepare_kimi_home_writes_config_and_mcp(tmp_path, monkeypatch):
    """_prepare_kimi_home（方案 C）：拷贝真实 config.toml + 写含身份注入的
    mcp.json；无 mcp_servers 返回 None（不加 --kimi-home）。"""
    fake_home = tmp_path / "fake-user-home"
    (fake_home / ".kimi-code").mkdir(parents=True)
    (fake_home / ".kimi-code" / "config.toml").write_text(
        'default_model = "moonshot-cn/kimi-k2.6"\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    a = _adapter()
    repo_root = Path(__file__).resolve().parent.parent
    s = _sess.Session(id="ses_mcp", name="mcp-session", adapter="kimi",
                      adapter_config={"mcp_servers": [{
                          "name": "pan",
                          "command": "python",
                          "args": ["-m", "packages.mcp.server"],
                          "cwd": str(repo_root),
                      }]})
    home = a._prepare_kimi_home(s)
    assert home is not None and home.is_dir()
    # (a) 真实 config.toml 被拷入隔离 HOME
    assert (home / "config.toml").read_text(encoding="utf-8").startswith("default_model")
    # (b) mcp.json 含 pan server 与身份注入
    mcp = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert "pan" in mcp["mcpServers"]
    env = mcp["mcpServers"]["pan"]["env"]
    assert env["PAN_AGENT_SESSION_ID"] == "ses_mcp"
    assert env["PAN_AGENT_SESSION_TITLE"] == "mcp-session"
    # 路径回填 adapter_config 供清理/enrich/fork 使用
    assert s.adapter_config["kimi_home_dir"] == str(home)

    # 无 MCP 配置 → None（走原路径，用真实用户目录）
    s_plain = _sess.Session(id="ses_plain", name="plain", adapter="kimi")
    assert a._prepare_kimi_home(s_plain) is None


def test_enrich_usage_cursor_against_synthetic_wire(tmp_path):
    """enrich 增量游标（usage.record 的 time）：首轮返回全部新条目并推进游标，
    二轮无新条目返回 None；model 回填 s.model。"""
    from packages.core.adapters.kimi.adapter import _read_kimi_new_entries

    home = tmp_path / "home"
    sdir = home / "sessions" / "wd_a" / "session_e"
    (sdir / "agents" / "main").mkdir(parents=True)
    events = []
    for i, t in enumerate([1792857801901, 1792857817404]):
        events.append({"type": "usage.record", "agentId": "main",
                       "model": "moonshot-cn/kimi-k2.6",
                       "usage": {"inputOther": 7, "output": 3,
                                 "inputCacheRead": 0, "inputCacheCreation": 0},
                       "time": t})
    (sdir / "agents" / "main" / "wire.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (sdir / "state.json").write_text(json.dumps({"id": "session_e"}), encoding="utf-8")

    s = _sess.Session(id="ses_e", name="e", adapter="kimi",
                      workdir=str(tmp_path),
                      adapter_config={"cli_session_id": "session_e",
                                      "kimi_home_dir": str(home)})
    entries = _read_kimi_new_entries(s)
    assert entries is not None and len(entries) == 2
    assert entries[0]["rawUsage"]["prompt_tokens"] == 7
    assert entries[0]["rawUsage"]["completion_tokens"] == 3
    assert s.adapter_config["kimi_last_usage_ts"] == 1792857817404
    assert s.model == "moonshot-cn/kimi-k2.6"  # stdout 无 model 字段，从 usage 回填
    # 游标已推进 → 二轮无新增
    assert _read_kimi_new_entries(s) is None


def test_wrapper_main_loop_argv_and_resume(monkeypatch, tmp_path):
    """wrapper 主循环对 kimi 的 argv 契约（2.3.1 实测有效）：
    首轮 `-p <text> --output-format stream-json [-m model] [--agent-file f]`，
    二轮起追加 `-S <session_id>` 且不再传 --agent-file（kimi 禁止组合）；
    KIMI_CODE_HOME 经 env 注入。"""
    from packages.core.adapters.kimi import wrapper as kimi_wrapper

    calls: list[dict] = []
    written: list[str] = []

    class _FakeStdout:
        def __init__(self, lines):
            self._lines = lines

        def __iter__(self):
            return iter(self._lines)

    class _FakeProc:
        def __init__(self, lines):
            self.stdout = _FakeStdout(lines)
            self.stderr = _FakeStdout([])
            self.returncode = 0

        def wait(self):
            return 0

    def fake_popen(args, **kwargs):
        calls.append({"args": args, "env": kwargs.get("env"), "cwd": kwargs.get("cwd")})
        if len(calls) == 1:
            lines = [
                b'{"role":"meta","type":"system.version","version":"2.3.1"}\n',
                b'{"role":"assistant","content":"FIRST_OK"}\n',
                b'{"role":"meta","type":"session.resume_hint","session_id":"session_fake1"}\n',
            ]
        else:
            lines = [
                b'{"role":"assistant","content":"SECOND_OK"}\n',
                b'{"role":"meta","type":"session.resume_hint","session_id":"session_fake1"}\n',
            ]
        return _FakeProc(lines)

    monkeypatch.setattr(kimi_wrapper.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(kimi_wrapper, "_write_stdout_line",
                        lambda text: written.append(text))

    def fake_reader(message_queue, shutdown_event):
        message_queue.put({"text": "first"})
        message_queue.put({"text": "second"})
        message_queue.put(None)  # stop

    monkeypatch.setattr(kimi_wrapper, "_stdin_reader", fake_reader)

    kimi_home = tmp_path / "home"
    kimi_home.mkdir()
    rc = kimi_wrapper._main_loop(
        kimi_path="kimi.exe", model="moonshot-cn/kimi-k2.6",
        initial_session_id=None, cwd=str(tmp_path),
        kimi_home=str(kimi_home),
        system_prompt="You are PAN.",
    )
    assert rc == 0
    assert len(calls) == 2

    first, second = calls[0], calls[1]
    assert first["args"][:3] == ["kimi.exe", "-p", "first"]
    assert "--output-format" in first["args"] and "stream-json" in first["args"]
    assert "-m" in first["args"] and "moonshot-cn/kimi-k2.6" in first["args"]
    assert "-S" not in first["args"]
    assert "--agent-file" in first["args"]  # 首轮人设注入
    assert first["env"]["KIMI_CODE_HOME"] == str(kimi_home)
    assert first["cwd"] == str(tmp_path)

    assert second["args"][:3] == ["kimi.exe", "-p", "second"]
    assert "-S" in second["args"] and "session_fake1" in second["args"]
    assert "--agent-file" not in second["args"]  # resume 后不再传（kimi 禁止组合）

    # 合成 result 事件（worker 据此结束回合）
    results = [json.loads(w) for w in written if '"role": "result"' in w.replace("'", '"')]
    assert results and results[-1]["result"] == "SECOND_OK"
    assert results[-1]["is_error"] is False


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
