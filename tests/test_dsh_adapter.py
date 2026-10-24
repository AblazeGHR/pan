"""Tests for the DeepSeek Harness (dsh) adapter, event mapping and wrapper.

Hermetic: no real dsh process is spawned. The wrapper loop is driven through a
fake ``subprocess.Popen``; the session-store tests live in
``test_dsh_sessions.py``.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters import DshAdapter, get_adapter, get_sessions_provider
from packages.core.adapters.dsh import adapter as dsh_adapter
from packages.core.adapters.dsh import events as dsh_events
from packages.core.adapters.dsh import sessions as dsh_sessions
from packages.core.adapters.dsh import wrapper as dsh_wrapper
from packages.core import session as _sess
import packages.core.config as core_config


def _adapter() -> DshAdapter:
    return DshAdapter()


def _session(**overrides) -> _sess.Session:
    cli_session_id = overrides.pop("cli_session_id", None)
    kwargs = dict(id="ses_dsh", name="probe", adapter="dsh",
                  workdir=str(Path.cwd()))
    kwargs.update(overrides)
    session = _sess.Session(**kwargs)
    session.cli_session_id = cli_session_id
    return session


# ── 注册与元信息 ──


def test_registered_as_adapter_and_sessions_provider():
    assert isinstance(get_adapter("dsh"), DshAdapter)
    assert get_sessions_provider("dsh") is dsh_sessions


def test_adapter_metadata():
    a = _adapter()
    assert a.name == "dsh"
    # 真正常驻的是 Pan wrapper，worker 只走 stream。
    assert a.execution_modes == ["stream"]
    assert a.supports_resume is True
    assert a.supports_fork is True
    # headless 无 --system-prompt flag → worker 走首条消息注入。
    assert a.supports_spawn_system_prompt is False
    assert a.supported_settings == ["model", "permissionMode", "effort"]
    assert a.supported_models == ["deepseek-flash", "deepseek-v4-pro"]
    assert a.effort_values == ["", "off", "low", "high", "max"]
    assert [m["value"] for m in a.permission_modes] == [
        "", "read-only", "workspace-write", "danger-full-access"]
    assert a.default_permission_mode == "danger-full-access"


def test_config_overrides_model_and_whitelist(monkeypatch):
    monkeypatch.setattr(core_config, "load_config", lambda: {
        "dsh": {"model": "deepseek-v4-pro", "models": ["deepseek-v4-pro", "x-model"],
                "permission_mode": "read-only", "provider": "my-route"},
    })
    a = _adapter()
    assert a.supported_models == ["deepseek-v4-pro", "x-model"]
    assert a.default_model == "deepseek-v4-pro"
    assert a.default_permission_mode == "read-only"
    assert a.provider == "my-route"


def test_stale_configured_model_falls_back(monkeypatch):
    monkeypatch.setattr(core_config, "load_config", lambda: {
        "dsh": {"model": "gone-model", "models": ["keep-a"]},
    })
    a = _adapter()
    assert a.default_model == "keep-a"


def test_invalid_permission_mode_config_is_ignored(monkeypatch):
    monkeypatch.setattr(core_config, "load_config", lambda: {
        "dsh": {"permission_mode": "yolo"},
    })
    assert _adapter().default_permission_mode == "danger-full-access"


def test_invalidate_models_cache_is_callable():
    assert DshAdapter.invalidate_models_cache() is None


# ── 入口解析 ──


def test_cli_entry_resolves_exe_and_asar_script(tmp_path, monkeypatch):
    install = tmp_path / "DSH"
    shim = install / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_text(
        '@echo off\nset "ELECTRON_RUN_AS_NODE=1"\n'
        '"%~dp0..\\..\\..\\..\\DeepSeek Harness.exe" --expose-internals '
        '"%~dp0..\\..\\..\\app.asar\\dsh\\node_modules\\@deepseek-ai\\dsh-desktop-host\\lib\\cli.js" %*\n',
        encoding="utf-8",
    )
    (install / "DeepSeek Harness.exe").write_text("stub", encoding="utf-8")
    (install / "resources" / "app.asar").write_text("stub", encoding="utf-8")
    monkeypatch.setenv("PAN_DSH_PATH", str(shim))
    monkeypatch.delenv("PAN_DSH_CLI_JS", raising=False)

    a = _adapter()
    exe, cli_js = a._resolve_entry()
    assert Path(exe).name == "DeepSeek Harness.exe"
    assert cli_js.endswith("cli.js")
    assert ".asar" in cli_js
    # asar 内的脚本无法用 is_file() 核验，诊断只检查可执行文件。
    assert a.resolved_cli_argv() == [exe]


def test_forwarding_shim_is_followed(tmp_path, monkeypatch):
    install = tmp_path / "DSH"
    inner = install / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"
    inner.parent.mkdir(parents=True)
    inner.write_text(
        '"%~dp0..\\..\\..\\..\\DeepSeek Harness.exe" --expose-internals '
        '"%~dp0..\\..\\..\\app.asar\\dsh\\cli.js" %*\n', encoding="utf-8")
    (install / "DeepSeek Harness.exe").write_text("stub", encoding="utf-8")
    (install / "resources" / "app.asar").write_text("stub", encoding="utf-8")
    outer = tmp_path / "global" / "dsh.cmd"
    outer.parent.mkdir(parents=True)
    outer.write_text(f'@echo off\ncall "{inner}" %*\n', encoding="utf-8")
    monkeypatch.setenv("PAN_DSH_PATH", str(outer))
    monkeypatch.delenv("PAN_DSH_CLI_JS", raising=False)

    exe, cli_js = _adapter()._resolve_entry()
    assert Path(exe).name == "DeepSeek Harness.exe"
    assert cli_js.endswith("cli.js")


def test_cli_js_env_override_wins(monkeypatch):
    monkeypatch.setenv("PAN_DSH_PATH", r"C:\stub\DeepSeek Harness.exe")
    monkeypatch.setenv("PAN_DSH_CLI_JS", r"C:\stub\cli.js")
    assert _adapter()._resolve_entry() == (r"C:\stub\DeepSeek Harness.exe", r"C:\stub\cli.js")


# ── spawn argv ──


def test_base_args_point_at_wrapper(monkeypatch):
    monkeypatch.setenv("PAN_DSH_PATH", r"C:\stub\dsh.exe")
    monkeypatch.setenv("PAN_DSH_CLI_JS", r"C:\stub\cli.js")
    args = _adapter().base_args()
    assert args[2].endswith("wrapper.py")
    assert "--dsh-path" in args and "--profile" in args
    assert args[args.index("--profile") + 1] == "headless"
    assert args[args.index("--dsh-cli-js") + 1] == r"C:\stub\cli.js"


def test_build_spawn_args_writes_patch_and_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setenv("PAN_DSH_PATH", r"C:\stub\dsh.exe")
    monkeypatch.delenv("PAN_DSH_CLI_JS", raising=False)
    s = _session(model="deepseek-v4-pro", permission_mode="workspace-write",
                 adapter_config={"effort": "low"})

    args = _adapter().build_spawn_args(s)
    patch_path = Path(args[args.index("--patch") + 1])
    assert patch_path.parent == tmp_path / "dsh-configs"
    assert patch_path.name == "ses_dsh.patch.yml"
    body = patch_path.read_text(encoding="utf-8")
    assert '- id: agent-default-model' in body
    assert 'provider: "deepseek-official"' in body
    assert 'model: "deepseek-v4-pro"' in body
    assert 'reasoningEffort: "low"' in body
    assert args[args.index("--permission-mode") + 1] == "workspace-write"
    # 没有 cli_session_id 时不得带 --session-id（新建会话）。
    assert "--session-id" not in args


def test_build_spawn_args_resumes_with_session_id(tmp_path, monkeypatch):
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setenv("PAN_DSH_PATH", r"C:\stub\dsh.exe")
    s = _session(cli_session_id="session-abc",
                 adapter_config={"effort": "", "mcp_servers": []})
    args = _adapter().build_spawn_args(s)
    assert args[args.index("--session-id") + 1] == "session-abc"


def test_no_patch_flag_when_nothing_to_inject(tmp_path, monkeypatch):
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setenv("PAN_DSH_PATH", r"C:\stub\dsh.exe")
    monkeypatch.setattr(DshAdapter, "default_model", "")
    a = _adapter()
    # model 与 MCP 都为空 → 不写 patch、不传 --patch（不改写用户 profile）。
    assert a.patch_args(_session(model="", adapter_config={})) == []
    assert not (tmp_path / "dsh-configs").exists()


def test_mcp_servers_become_streamable_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setenv("PAN_DSH_PATH", r"C:\stub\dsh.exe")
    s = _session(adapter_config={"mcp_servers": [
        {"name": "pan", "command": "python", "args": ["-m", "packages.mcp.server"],
         "cwd": r"D:\project\Pan"},
        {"name": "web thing", "url": "http://127.0.0.1:9/mcp",
         "headers": {"Authorization": "Bearer x"}},
        {"name": "pan", "command": "python", "args": ["-m", "dup"]},
        {"name": "myfs", "command": "node", "args": ["server.js"],
         "cwd": r"D:\work\fs"},
    ]})
    body = _adapter()._patch_text(s)
    assert "- insert:" in body
    assert 'name: "@deepseek-ai/dsh-mcp-client"' in body
    assert 'serverName: "pan"' in body
    assert 'transport: "stdio"' in body
    assert 'command: "python"' in body
    # 普通 server 的 cwd 原样透传（pan 系会被共享 helper 改写成 Pan 仓库根）
    assert 'cwd: "D:\\\\work\\\\fs"' in body
    # pan 系注入 MA 会话身份（worker_send 打标）
    assert '"PAN_AGENT_SESSION_ID": "ses_dsh"' in body
    # 名字里的空格按 DSH serverName 约束清洗。
    assert 'serverName: "web-thing"' in body
    assert 'transport: "streamable-http"' in body
    assert 'url: "http://127.0.0.1:9/mcp"' in body
    assert '"Authorization": "Bearer x"' in body
    # 重名 server 由共享 helper 的 dict 语义去重（后写覆盖），不会出现两个同名条目。
    assert body.count('serverName: "pan"') == 1
    assert 'serverName: "pan-2"' not in body


def test_patch_write_failure_degrades_to_no_flag(tmp_path, monkeypatch):
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(dsh_adapter, "_patch_dir", lambda: blocker / "sub")
    monkeypatch.setenv("PAN_DSH_PATH", r"C:\stub\dsh.exe")
    assert _adapter().patch_args(_session(adapter_config={})) == []


def test_oneshot_args_unused():
    assert _adapter().oneshot_args(_session(), "hi") == []


def test_encode_user_message_roundtrip():
    raw = _adapter().encode_user_message("你好 「引号」")
    assert json.loads(raw.decode("utf-8")) == {"text": "你好 「引号」"}


# ── 事件映射（events.py） ──


def _state() -> dsh_events.TurnState:
    return dsh_events.TurnState()


def test_map_session_event_is_pan_init():
    state = _state()
    out = dsh_events.map_event({"type": "session", "sessionId": "session-1",
                                "cwd": "D:\\w"}, state)
    assert out == [{"type": "system", "subtype": "init",
                    "session_id": "session-1", "cwd": "D:\\w"}]
    assert state.session_id == "session-1"
    adapter = _adapter()
    assert adapter.is_init_event(out[0])
    assert adapter.extract_session_id(out[0]) == "session-1"


def test_map_text_and_thinking_to_cbc_shape():
    adapter = _adapter()
    mapping = dsh_events.map_event({"type": "text", "text": "hello"}, _state())
    assert mapping[0]["type"] == "assistant"
    assert adapter.is_assistant_event(mapping[0])
    assert adapter.extract_assistant_blocks(mapping[0]) == [
        {"role": "assistant", "content": "hello"}]

    thinking = dsh_events.map_event({"type": "thinking", "text": "hmm"}, _state())
    assert adapter.extract_assistant_blocks(thinking[0]) == [
        {"role": "thinking", "content": "hmm"}]


def test_map_tool_call_and_result():
    adapter = _adapter()
    call = dsh_events.map_event(
        {"type": "tool_call", "callId": "c1", "tool": "read_file",
         "input": {"path": "a.txt"}}, _state())[0]
    assert adapter.extract_assistant_blocks(call) == [
        {"role": "tool", "content": 'read_file({"path":"a.txt"})'}]
    result = dsh_events.map_event(
        {"type": "tool_result", "callId": "c1", "status": "completed",
         "result": "ok"}, _state())[0]
    assert result == {"type": "tool_result", "callId": "c1",
                      "status": "completed", "result": "ok"}


def test_map_status_accumulates_usage_and_keeps_turn_end_reason():
    state = _state()
    dsh_events.map_event({"type": "status", "phase": "turn_start", "turn": 1}, state)
    dsh_events.map_event({"type": "status", "phase": "step_end", "turn": 1, "step": 1,
                          "usage": {"inputTokens": 100, "outputTokens": 10,
                                    "cacheReadTokens": 40, "totalTokens": 110}}, state)
    out = dsh_events.map_event({"type": "status", "phase": "turn_end", "turn": 1,
                                "reason": {"kind": "completed"}}, state)
    assert out[0]["reason"] == {"kind": "completed"}
    assert state.usage["inputTokens"] == 100
    raw = dsh_events.pan_raw_usage(state.usage)
    assert raw == {"prompt_tokens": 60, "completion_tokens": 10,
                   "reasoning_tokens": 0, "cache_read_tokens": 40,
                   "cache_write_tokens": 0, "total_tokens": 110}


def test_map_final_and_unknown_events():
    state = _state()
    assert dsh_events.map_event({"type": "final", "text": "done"}, state) == []
    assert state.final_text == "done"
    assert dsh_events.map_event({"type": "future/thing", "x": 1}, state) == []


# ── 终止 result ──


def test_terminal_result_success_uses_final_text():
    state = _state()
    state.session_id = "session-1"
    state.final_text = "answer"
    state.turn_reason = {"kind": "completed"}
    state.usage = {"inputTokens": 5, "outputTokens": 2}
    result = dsh_events.terminal_result(state, 0)
    assert result["is_error"] is False
    assert result["result"] == "answer"
    assert result["usage"]["prompt_tokens"] == 5
    adapter = _adapter()
    assert adapter.is_result_event(result)
    assert adapter.is_result_error(result) is False
    assert adapter.extract_result_text(result) == "answer"


def test_terminal_result_falls_back_to_last_text():
    state = _state()
    state.last_text = "partial"
    state.turn_reason = {"kind": "completed"}
    state.final_text = ""
    assert dsh_events.terminal_result(state, 0)["result"] == ""


def test_terminal_result_missing_credential_is_error():
    state = _state()
    state.turn_reason = {"kind": "error", "error": {
        "message": "llm-deepseek: no API key", "code": "MISSING_CREDENTIAL"}}
    result = dsh_events.terminal_result(state, 1)
    assert result["is_error"] is True
    assert "MISSING_CREDENTIAL" in result["result"]
    assert result["error"]["code"] == "MISSING_CREDENTIAL"
    assert result["error_source"] == "dsh_turn_error"
    assert _adapter().is_result_error(result) is True


def test_terminal_result_runner_error_event():
    state = _state()
    state.run_error = 'session "x" does not exist'
    result = dsh_events.terminal_result(state, 1)
    assert result["is_error"] is True
    assert result["error_source"] == "dsh_error_event"


def test_terminal_result_nonzero_exit_without_turn_end():
    result = dsh_events.terminal_result(_state(), 3, "boom\nsecond line")
    assert result["is_error"] is True
    assert result["error"]["exitCode"] == 3
    assert "boom" in result["result"]


def test_terminal_result_max_tokens_and_blocked():
    for kind in ("max-tokens", "blocked", "aborted"):
        result = dsh_events.terminal_result(_state_with_reason(kind), 1)
        assert result["is_error"] is True
        assert result["error_source"] == f"dsh_turn_{kind}"


def _state_with_reason(kind: str) -> dsh_events.TurnState:
    state = _state()
    state.turn_reason = {"kind": kind}
    return state


# ── adapter 对原生（未翻译）事件的兼容 ──


def test_adapter_parses_raw_dsh_events():
    a = _adapter()
    assert a.parse_event("not json") is None
    assert a.parse_event("[1,2]") is None
    assert a.is_assistant_event({"type": "text"})
    assert a.extract_assistant_blocks({"type": "text", "text": "raw"}) == [
        {"role": "assistant", "content": "raw"}]
    assert a.extract_assistant_blocks({"type": "thinking", "text": "t"}) == [
        {"role": "thinking", "content": "t"}]
    blocks = a.extract_assistant_blocks(
        {"type": "tool_call", "tool": "ls", "input": {"p": "."}})
    assert blocks == [{"role": "tool", "content": 'ls({"p":"."})'}]
    # 原生 final 也算终止（集成回放场景）
    assert a.is_result_event({"type": "final", "text": "x"})
    assert a.extract_result_text({"type": "final", "text": "x"}) == "x"
    assert a.is_result_error({"type": "final"}) is False


# ── wrapper ──


def test_wrapper_build_command_argv_order(tmp_path):
    opts = argparse.Namespace(
        dsh_path="exe", dsh_cli_js="cli.js", profile="headless",
        patch="p.yml", permission_mode="read-only", session_id=None)
    cmd = dsh_wrapper._build_command(opts, None)
    assert cmd == ["exe", "--expose-internals", "cli.js", "--profile", "headless",
                   "--patch", "p.yml", "--json", "-"]
    cmd = dsh_wrapper._build_command(opts, "session-9")
    assert cmd[-3:] == ["--session-id", "session-9", "-"]


class _RecordingStdin(io.BytesIO):
    """记录 wrapper 写进子进程 stdin 的任务正文（wrapper 会 close，故不真关）。"""

    def __init__(self):
        super().__init__()
        self.written = b""

    def write(self, data):
        self.written += data
        return len(data)

    def close(self):
        return None


class _FakeProc:
    def __init__(self, lines, returncode=0):
        self.stdout = io.BytesIO(b"".join(line + b"\n" for line in lines))
        self.stderr = io.BytesIO(b"")
        self.stdin = _RecordingStdin()
        self.returncode = returncode

    def wait(self):
        return self.returncode

def _run_wrapper_turn(monkeypatch, lines, returncode=0, text="hi"):
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env") or {}
        proc = _FakeProc(lines, returncode)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(dsh_wrapper.subprocess, "Popen", fake_popen)
    out = io.BytesIO()

    class _Out:
        buffer = out

        def write(self, *_args):  # pragma: no cover - 不应被调用
            raise AssertionError("wrapper must write to stdout.buffer")

        def flush(self):
            pass

    class _Err:
        buffer = io.BytesIO()

        def write(self, *_args):
            pass

        def flush(self):
            pass

    monkeypatch.setattr(dsh_wrapper.sys, "stdout", _Out())
    monkeypatch.setattr(dsh_wrapper.sys, "stderr", _Err())
    opts = argparse.Namespace(
        dsh_path="exe", dsh_cli_js="cli.js", profile="headless",
        patch=None, permission_mode="danger-full-access", session_id=None)
    new_sid = dsh_wrapper.run_turn(opts, text, None)
    emitted = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    return emitted, new_sid, captured


def test_wrapper_turn_maps_stream_and_synthesizes_result(monkeypatch):
    lines = [
        b'{"type":"session","sessionId":"session-1","cwd":"D:\\\\w"}',
        b'{"type":"status","phase":"turn_start","turn":1}',
        b'{"type":"status","phase":"step_start","turn":1,"step":1}',
        b'{"type":"thinking","text":"plan"}',
        b'{"type":"text","text":"final answer"}',
        b'{"type":"status","phase":"step_end","turn":1,"step":1,"usage":{"inputTokens":7,"outputTokens":3}}',
        b'{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"completed"}}',
        b'{"type":"final","text":"final answer"}',
    ]
    emitted, sid, captured = _run_wrapper_turn(monkeypatch, lines)
    assert sid == "session-1"
    assert [event["type"] for event in emitted][0] == "system"
    assert emitted[0]["session_id"] == "session-1"
    assert emitted[-1]["type"] == "result"
    assert emitted[-1]["is_error"] is False
    assert emitted[-1]["result"] == "final answer"
    assert emitted[-1]["usage"]["prompt_tokens"] == 7
    # permission mode 与 Electron node 模式都通过环境变量传给子进程。
    assert captured["env"]["DSH_PERMISSION_MODE"] == "danger-full-access"
    assert captured["env"]["ELECTRON_RUN_AS_NODE"] == "1"
    # 任务走 stdin（'-'），不拼进 argv。
    assert captured["cmd"][-1] == "-"
    assert captured["proc"].stdin.written == b"hi"


def test_wrapper_turn_reports_failure(monkeypatch):
    lines = [
        b'{"type":"session","sessionId":"session-2"}',
        b'{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"error",'
        b'"error":{"message":"boom","code":"MISSING_CREDENTIAL"}}}',
        b'{"type":"final","text":""}',
    ]
    emitted, _sid, _captured = _run_wrapper_turn(monkeypatch, lines, returncode=1)
    assert emitted[-1]["is_error"] is True
    assert "MISSING_CREDENTIAL" in emitted[-1]["result"]


def test_wrapper_spawn_failure_yields_result(monkeypatch):
    def boom(*_args, **_kwargs):
        raise FileNotFoundError("nope")

    monkeypatch.setattr(dsh_wrapper.subprocess, "Popen", boom)
    out = io.BytesIO()

    class _Out:
        buffer = out

        def flush(self):
            pass

    class _Err:
        buffer = io.BytesIO()

        def flush(self):
            pass

    monkeypatch.setattr(dsh_wrapper.sys, "stdout", _Out())
    monkeypatch.setattr(dsh_wrapper.sys, "stderr", _Err())
    opts = argparse.Namespace(
        dsh_path="missing.exe", dsh_cli_js=None, profile="headless",
        patch=None, permission_mode=None, session_id=None)
    dsh_wrapper.run_turn(opts, "hi", None)
    emitted = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    assert emitted[-1]["is_error"] is True
    assert emitted[-1]["error_source"] == "wrapper_exception"


def test_wrapper_reads_jsonl_stdin(monkeypatch):
    payload = io.BytesIO(b'{"text":"a"}\n\n{"text":"b"}\n{"ignored":1}\nnot json\n')
    monkeypatch.setattr(dsh_wrapper.sys, "stdin",
                        type("F", (), {"buffer": payload, "readline": payload.readline})())
    assert list(dsh_wrapper._read_stdin_messages()) == ["a", "b"]


# ── enrich ──


def test_enrich_after_result_returns_delta(monkeypatch):
    monkeypatch.setattr(dsh_sessions, "get_raw_usage", lambda sid, cwd=None: [{
        "model": "deepseek-flash",
        "rawUsage": {"prompt_tokens": 100, "completion_tokens": 20,
                     "reasoning_tokens": 0, "cache_read_tokens": 0,
                     "cache_write_tokens": 0, "total_tokens": 120},
        "timestamp": "2026-10-24T10:00:00",
    }])
    s = _session(cli_session_id="session-1",
                 adapter_config={"dsh_prev_usage": {"prompt_tokens": 40,
                                                    "total_tokens": 50}})
    entries = _adapter().enrich_after_result(s)
    assert entries == [{
        "model": "deepseek-flash",
        "rawUsage": {"prompt_tokens": 60, "completion_tokens": 20,
                     "reasoning_tokens": 0, "cache_read_tokens": 0,
                     "cache_write_tokens": 0, "total_tokens": 70},
        "timestamp": "2026-10-24T10:00:00",
    }]
    assert s.adapter_config["dsh_prev_usage"]["prompt_tokens"] == 100
    # 第二轮无新增 → None（不重复累加）
    assert _adapter().enrich_after_result(s) is None


def test_enrich_after_result_without_session_or_usage(monkeypatch):
    assert _adapter().enrich_after_result(_session()) is None
    monkeypatch.setattr(dsh_sessions, "get_raw_usage", lambda sid, cwd=None: [])
    assert _adapter().enrich_after_result(_session(cli_session_id="x")) is None


# ── takeover ──


def test_takeover_requires_existing_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    monkeypatch.setenv("PAN_DSH_PATH", "C:/stub/dsh.cmd")
    monkeypatch.delenv("PAN_DSH_CLI_JS", raising=False)
    a = _adapter()
    assert a.takeover_command(_session()) == []
    # 有 cli_session_id 但没有任何交互式 profile → 明确不支持
    assert a.takeover_command(_session(cli_session_id="session-1")) == []
    (tmp_path / "profiles" / "tui").mkdir(parents=True)
    cmd = a.takeover_command(_session(cli_session_id="session-1"))
    assert cmd == ["C:/stub/dsh.cmd", "--profile", "tui", "--resume", "session-1"]


def test_takeover_prefers_shim_that_sets_node_mode(tmp_path, monkeypatch):
    """shim 自己 set ELECTRON_RUN_AS_NODE=1，直接用它才不会拉起桌面 GUI。"""
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    (tmp_path / "profiles" / "tui").mkdir(parents=True)
    monkeypatch.setenv("PAN_DSH_PATH", "C:/stub/dsh.cmd")
    monkeypatch.delenv("PAN_DSH_CLI_JS", raising=False)
    cmd = _adapter().takeover_command(_session(cli_session_id="session-1"))
    assert cmd[0].lower().endswith(".cmd")
    assert "--expose-internals" not in cmd


def test_takeover_exe_gets_launcher_with_node_mode(tmp_path, monkeypatch):
    """只解析到 exe 时，必须经 Pan 自有 launcher 设 ELECTRON_RUN_AS_NODE。"""
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    (tmp_path / "profiles" / "tui").mkdir(parents=True)
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    exe = "D:/DSH/DeepSeek Harness.exe"
    cli_js = "D:/DSH/resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"
    monkeypatch.setenv("PAN_DSH_PATH", exe)
    monkeypatch.setenv("PAN_DSH_CLI_JS", cli_js)

    cmd = _adapter().takeover_command(_session(cli_session_id="session-1"))
    launcher = Path(cmd[0])
    assert launcher.name == "takeover-launcher.cmd"
    assert launcher.parent == tmp_path / "dsh-configs"
    body = launcher.read_text(encoding="utf-8")
    assert 'set "ELECTRON_RUN_AS_NODE=1"' in body
    assert exe in body and cli_js in body
    assert "--expose-internals" in body
    assert body.rstrip().endswith("%*") or "%*" in body
    # argv 只把 launcher 当可执行体，绝不裸传 exe（那会启动 GUI）
    assert cmd == [str(launcher), "--profile", "tui", "--resume", "session-1"]
    assert exe not in cmd

    # 幂等：同一路径第二次调用不重写文件
    mtime = launcher.stat().st_mtime_ns
    again = _adapter().takeover_command(_session(cli_session_id="session-1"))
    assert again == cmd
    assert launcher.stat().st_mtime_ns == mtime


def test_takeover_launcher_write_failure_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    (tmp_path / "profiles" / "tui").mkdir(parents=True)
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(dsh_adapter, "_patch_dir", lambda: blocker / "sub")
    monkeypatch.setenv("PAN_DSH_PATH", "D:/DSH/DeepSeek Harness.exe")
    monkeypatch.setenv("PAN_DSH_CLI_JS", "D:/DSH/app.asar/cli.js")
    assert _adapter().takeover_command(_session(cli_session_id="session-1")) == []
