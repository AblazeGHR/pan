"""Tests for OpenAI Codex CLI adapter, wrapper arg-building, and session parser.

Hermetic: no real codex process is spawned; session-store tests build a fake
~/.codex (state_5.sqlite + thread_history_1.sqlite + rollout) in a temp dir and
monkeypatch the module-level paths.
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters import CodexAdapter
from packages.core.adapters.codex import adapter as codex_adapter
from packages.core.adapters.codex import sessions as codex_sessions
from packages.core.adapters.codex import wrapper as codex_wrapper
from packages.core import session as _sess
import packages.core.config as core_config


def _adapter() -> CodexAdapter:
    return CodexAdapter()


def _session(**overrides) -> _sess.Session:
    kwargs = dict(id="ses_test", name="test", adapter="codex")
    kwargs.update(overrides)
    return _sess.Session(**kwargs)


# ── adapter 元信息 ──


def test_adapter_metadata():
    a = _adapter()
    assert a.name == "codex"
    assert a.execution_modes == ["stream"]  # wrapper 长驻，worker 只走 stream
    assert a.supports_resume is True
    assert a.supports_fork is True
    assert len(a.supported_models) > 0
    assert any(p["value"] in ("", "bypass", "approve") for p in a.permission_modes)
    assert a.default_permission_mode == "bypass"
    print("PASS: adapter metadata")


def test_shim_resolution():
    """.CMD shim → 真实 codex.js 入口（避开 cmd.exe 中文乱码）。"""
    # 用假 shim 路径验证解析逻辑（不要求文件存在）；Windows 下 os.path.join 用反斜杠
    fake_shim = "D:/node_npm/node_global/codex.CMD"
    resolved = codex_adapter._codex_js_from_shim(fake_shim)
    expected = os.path.join("D:", os.sep, "node_npm", "node_global",
                            "node_modules", "@openai", "codex", "bin", "codex.js")
    assert os.path.normcase(resolved) == os.path.normcase(expected)
    # 非 .CMD 路径不做 shim 解析
    assert codex_adapter._codex_js_from_shim("D:/bin/codex") is None
    print("PASS: shim resolution")


# ── 进程启动参数 ──


def test_build_spawn_args():
    a = _adapter()
    s = _session()
    args = a.build_spawn_args(s)
    assert args[0] == sys.executable
    assert args[2].endswith("wrapper.py")
    assert "--codex-path" in args
    assert "--node-path" in args
    # model 经 -c model= 内联覆盖；默认权限 bypass 也作为一次性 flag 透传
    i = args.index("--codex-extra-args")
    extra = json.loads(args[i + 1])
    assert "-c" in extra
    assert f'model="{a.default_model}"' in extra
    assert "--dangerously-bypass-approvals-and-sandbox" in extra
    print("PASS: build_spawn_args")


def test_build_spawn_args_with_resume():
    a = _adapter()
    s = _session(adapter_config={"cli_session_id": "thread_01a0-xxxx"})
    args = a.build_spawn_args(s)
    assert "--thread-id" in args
    assert "thread_01a0-xxxx" in args
    print("PASS: build_spawn_args with resume")


def test_permission_mode_args():
    a = _adapter()
    s = _session()
    opts = a.permission_mode_args(s)
    assert "--dangerously-bypass-approvals-and-sandbox" in opts
    # approve：不用 --approve-for-me（resume 不支持），改 -c 覆盖等效配置
    s2 = _session(permission_mode="approve")
    assert a.permission_mode_args(s2) == [
        "-c", 'sandbox_mode="workspace-write"',
        "-c", 'approval_policy="never"',
    ]
    s3 = _session(permission_mode="")
    # default_permission_mode=bypass 兜底
    assert a.permission_mode_args(s3) == ["--dangerously-bypass-approvals-and-sandbox"]
    print("PASS: permission_mode_args")


def test_mcp_args_builds_inline_overrides():
    a = _adapter()
    s = _session(adapter_config={"mcp_servers": [{
        "name": "pan",
        "type": "stdio",
        "command": "node",
        "args": ["D:/pan-mcp-server.js"],
        "env": {"FOO": "bar"},
    }]})
    opts = a.mcp_args(s)
    joined = " ".join(opts)
    assert 'mcp_servers.pan.command="node"' in joined
    assert 'mcp_servers.pan.args=["node", "D:/pan-mcp-server.js"]' in joined
    # pan server 注入 MA session 身份
    assert 'mcp_servers.pan.env.PAN_AGENT_SESSION_ID="ses_test"' in joined
    assert 'mcp_servers.pan.env.PAN_AGENT_SESSION_TITLE="test"' in joined
    assert 'mcp_servers.pan.env.FOO="bar"' in joined
    print("PASS: mcp_args stdio inline overrides")


def test_mcp_args_url_server():
    a = _adapter()
    s = _session(adapter_config={"mcp_servers": [{
        "name": "remote",
        "type": "http",
        "url": "http://127.0.0.1:9000/mcp",
    }]})
    joined = " ".join(a.mcp_args(s))
    assert 'mcp_servers.remote.url="http://127.0.0.1:9000/mcp"' in joined
    assert 'mcp_servers.remote.transport="http"' in joined
    print("PASS: mcp_args url server")


def test_no_mcp_returns_empty():
    a = _adapter()
    assert a.mcp_args(_session()) == []
    print("PASS: mcp_args without config")


def test_c_override_toml_escaping():
    assert codex_adapter._c_override("model", "a/b") == 'model="a/b"'
    # 含引号/特殊字符的值经 json.dumps 生成 TOML 安全字面量
    assert codex_adapter._c_override("model_reasoning_effort", 'low "x"') == 'model_reasoning_effort="low \\"x\\""'
    assert codex_adapter._c_override("mcp_servers.pan.args", ["node", "a b"]) == 'mcp_servers.pan.args=["node", "a b"]'
    print("PASS: _c_override TOML escaping")


# ── stdin 编码 ──


def test_encode_user_message():
    a = _adapter()
    assert json.loads(a.encode_user_message("你好 codex")) == {"text": "你好 codex"}
    print("PASS: encode_user_message")


# ── 事件解析（用真实 codex exec --json 捕获的 schema）──


def test_init_event_extracts_thread_id():
    a = _adapter()
    event = {"type": "thread.started", "thread_id": "01a0-xxxx"}
    assert a.is_init_event(event)
    assert a.extract_session_id(event) == "01a0-xxxx"
    assert a.extract_model(event) is None
    print("PASS: init event extracts thread_id")


def test_parse_agent_message():
    a = _adapter()
    # live stdout 用 snake_case
    event = {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "PONG"}}
    assert a.is_assistant_event(event)
    assert a.extract_assistant_blocks(event) == [{"role": "assistant", "content": "PONG"}]
    # 持久化 thread_items 用 camelCase
    event2 = {"type": "item.completed", "item": {"id": "i1", "type": "agentMessage", "text": "PONG2"}}
    assert a.extract_assistant_blocks(event2) == [{"role": "assistant", "content": "PONG2"}]
    print("PASS: parse agent_message (snake + camel)")


def test_parse_reasoning():
    a = _adapter()
    event = {"type": "item.completed", "item": {"id": "i2", "type": "reasoning", "summary": ["think hard"]}}
    assert a.extract_assistant_blocks(event) == [{"role": "thinking", "content": "think hard"}]
    event2 = {"type": "item.completed", "item": {"id": "i3", "type": "reasoning", "text": "direct"}}
    assert a.extract_assistant_blocks(event2) == [{"role": "thinking", "content": "direct"}]
    print("PASS: parse reasoning")


def test_parse_command_execution():
    a = _adapter()
    event = {"type": "item.completed", "item": {
        "id": "i4", "type": "command_execution",
        "command": "echo hi", "aggregated_output": "hi\r\n",
    }}
    blocks = a.extract_assistant_blocks(event)
    assert blocks[0]["role"] == "tool"
    assert "echo hi" in blocks[0]["content"]
    assert "hi" in blocks[0]["content"]
    # camelCase 变体（持久化）
    event2 = {"type": "item.completed", "item": {
        "id": "i4", "type": "commandExecution",
        "command": "echo hi2", "aggregated_output": "hi2",
    }}
    assert "echo hi2" in a.extract_assistant_blocks(event2)[0]["content"]
    print("PASS: parse command_execution (snake + camel)")


def test_parse_user_message_block():
    a = _adapter()
    event = {"type": "item.completed", "item": {
        "id": "i5", "type": "userMessage",
        "content": [{"type": "text", "text": "hello"}],
    }}
    assert a.extract_assistant_blocks(event) == [{"role": "user", "content": "hello"}]
    print("PASS: parse user_message block")


def test_result_event():
    a = _adapter()
    event = {"type": "result", "is_error": False, "result": "done"}
    assert a.is_result_event(event)
    assert a.is_result_error(event) is False
    assert a.extract_result_text(event) == "done"
    err = {"type": "result", "is_error": True, "result": "boom"}
    assert a.is_result_error(err) is True
    print("PASS: result event")


# ── wrapper 参数构建 ──


def test_build_codex_args_fresh():
    args = codex_wrapper._build_codex_args(
        "node", "codex.js", "你好", None,
        ["-c", 'model="m"', "--dangerously-bypass-approvals-and-sandbox"], "C:/work",
    )
    assert args[:3] == ["node", "codex.js", "exec"]
    assert "-c" in args
    assert 'model="m"' in args
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert "你好" in args
    assert "--json" in args
    assert "-C" in args and "C:/work" in args
    assert "--skip-git-repo-check" in args
    assert "resume" not in args
    print("PASS: build_codex_args fresh")


def test_build_codex_args_resume_filters_non_c_flags():
    # approve 模式权限已改为 -c 覆盖；bypass 的 flag 是 resume 支持的，应保留
    opts = [
        "-c", 'model="m"',
        "-c", 'sandbox_mode="workspace-write"',
        "-c", 'approval_policy="never"',
        "--dangerously-bypass-approvals-and-sandbox",
        "--approve-for-me",  # 已不用；若残留应被丢弃（resume 不支持）
    ]
    args = codex_wrapper._build_codex_args(
        "node", "codex.js", "continue", "thread_01a0", opts, "C:/work",
    )
    assert "exec" in args
    assert "resume" in args
    assert "thread_01a0" in args
    # resume 保留 -c 覆盖 + bypass flag；丢弃 --approve-for-me 与 -C（resume 不支持）
    assert "--approve-for-me" not in args
    assert "-C" not in args
    assert "--skip-git-repo-check" in args
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert '-c' in args and 'model="m"' in args
    assert 'sandbox_mode="workspace-write"' in args
    assert 'approval_policy="never"' in args
    print("PASS: build_codex_args resume keeps -c + bypass, drops unsupported flags")


def test_filter_resume_opts():
    assert codex_wrapper._filter_resume_opts(
        ["-c", 'a="1"', "--flag", "-c", 'b="2"']
    ) == ["-c", 'a="1"', "-c", 'b="2"']
    # bypass flag 保留（resume 支持），其它一次性 flag 丢弃
    assert codex_wrapper._filter_resume_opts(
        ["--dangerously-bypass-approvals-and-sandbox", "--approve-for-me", "-c", 'x="1"']
    ) == ["--dangerously-bypass-approvals-and-sandbox", "-c", 'x="1"']
    print("PASS: _filter_resume_opts")


# ── sessions：纯函数 ──


def test_item_to_block_mapping():
    assert codex_sessions._item_to_block({"type": "userMessage", "content": [{"type": "text", "text": "u"}]}) == {"role": "user", "content": "u"}
    assert codex_sessions._item_to_block({"type": "agentMessage", "text": "a"}) == {"role": "assistant", "content": "a"}
    assert codex_sessions._item_to_block({"type": "reasoning", "summary": ["r"]}) == {"role": "thinking", "content": "r"}
    assert codex_sessions._item_to_block({"type": "commandExecution", "command": "cmd", "aggregated_output": "out"}) == {"role": "tool", "content": "cmd\n→ out"}
    assert codex_sessions._item_to_block({"type": "unknownType"}) is None
    print("PASS: _item_to_block mapping")


def test_norm_path():
    # 剥离 \\?\ 长路径前缀 + 大小写/分隔符归一
    assert codex_sessions._norm_path("\\\\?\\C:\\Users\\x\\Temp\\w") == "c:\\users\\x\\temp\\w"
    assert codex_sessions._norm_path("c:/users/x/temp/w") == "c:\\users\\x\\temp\\w"
    print("PASS: _norm_path")


# ── sessions：临时 ~/.codex 端到端（hermetic）──


def _build_fake_codex_dir(tmp: Path) -> tuple[Path, Path, Path]:
    """构造 fake ~/.codex：state_5.sqlite + thread_history_1.sqlite + rollout。"""
    state = tmp / "state_5.sqlite"
    hist = tmp / "thread_history_1.sqlite"
    roll_dir = tmp / "sessions" / "2026" / "08" / "26"
    roll_dir.mkdir(parents=True, exist_ok=True)
    rollout = roll_dir / "rollout-2026-08-26T00-00-00-thread_abc.jsonl"

    # state DB（精简 schema，覆盖本模块实际读写列）
    scon = sqlite3.connect(state)
    scon.execute("""CREATE TABLE threads (
        id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, source TEXT NOT NULL, model_provider TEXT NOT NULL,
        cwd TEXT NOT NULL, title TEXT NOT NULL, sandbox_policy TEXT NOT NULL,
        approval_mode TEXT NOT NULL, tokens_used INTEGER NOT NULL DEFAULT 0,
        has_user_event INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
        archived_at INTEGER, model TEXT, name TEXT, first_user_message TEXT NOT NULL DEFAULT '',
        created_at_ms INTEGER, updated_at_ms INTEGER, recency_at INTEGER NOT NULL DEFAULT 0,
        recency_at_ms INTEGER NOT NULL DEFAULT 0)""")
    scon.execute("""CREATE TABLE thread_spawn_edges (
        parent_thread_id TEXT NOT NULL, child_thread_id TEXT NOT NULL PRIMARY KEY,
        status TEXT NOT NULL)""")
    scon.execute("""INSERT INTO threads (id, rollout_path, created_at, updated_at, source,
        model_provider, cwd, title, sandbox_policy, approval_mode, tokens_used, has_user_event,
        archived, model, name, first_user_message, created_at_ms, updated_at_ms,
        recency_at, recency_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("thread_abc", str(rollout), 1700000000, 1700000100, "cli", "custom",
         str(tmp / "work"), "Test Thread", "read-only", "untrusted", 1234, 1, 0,
         "m1", "", "hello", 1700000000, 1700000100, 1700000100, 1700000100))
    scon.commit()
    scon.close()

    # history DB
    hcon = sqlite3.connect(hist)
    hcon.execute("""CREATE TABLE thread_items (
        thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, item_id TEXT NOT NULL,
        rollout_ordinal INTEGER NOT NULL, created_at_ms INTEGER NOT NULL,
        item_json TEXT NOT NULL, item_type TEXT NOT NULL DEFAULT '',
        updated_at_ordinal INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (thread_id, turn_id, item_id))""")
    hcon.execute("""CREATE TABLE thread_turns (
        thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, rollout_ordinal INTEGER NOT NULL,
        status TEXT NOT NULL, error_json TEXT, started_at INTEGER, completed_at INTEGER,
        duration_ms INTEGER, first_user_item_id TEXT, final_agent_item_id TEXT,
        rollout_byte_offset INTEGER, rollout_end_ordinal INTEGER, rollout_end_byte_offset INTEGER,
        PRIMARY KEY (thread_id, turn_id))""")
    hcon.execute("""INSERT INTO thread_items (thread_id, turn_id, item_id, rollout_ordinal,
        created_at_ms, item_json, item_type, updated_at_ordinal) VALUES (?,?,?,?,?,?,?,?)""",
        ("thread_abc", "turn_1", "it_user", 0, 1700000000,
         json.dumps({"type": "userMessage", "content": [{"type": "text", "text": "hi"}]}),
         "userMessage", 0))
    hcon.execute("""INSERT INTO thread_items (thread_id, turn_id, item_id, rollout_ordinal,
        created_at_ms, item_json, item_type, updated_at_ordinal) VALUES (?,?,?,?,?,?,?,?)""",
        ("thread_abc", "turn_1", "it_agent", 1, 1700000005,
         json.dumps({"type": "agentMessage", "text": "hello!"}),
         "agentMessage", 0))
    hcon.commit()
    hcon.close()

    # rollout（token_count usage）
    rollout.write_text(
        json.dumps({"type": "event_msg", "timestamp": "2026-08-26T00:00:10Z",
                    "payload": {"type": "token_count", "info": {
                        "total_token_usage": {
                            "input_tokens": 100, "cached_input_tokens": 50,
                            "cache_write_input_tokens": 5, "output_tokens": 20,
                            "reasoning_output_tokens": 7, "total_tokens": 120}}}}) + "\n",
        encoding="utf-8",
    )
    return state, hist, tmp / "sessions"


def test_sessions_provider_e2e():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        state, hist, sess_dir = _build_fake_codex_dir(tmp)
        # monkeypatch 模块级路径到 fake ~/.codex
        codex_sessions._STATE_DB = state
        codex_sessions._HISTORY_DB = hist
        codex_sessions._SESSIONS_DIR = sess_dir

        # list_sessions + cwd 过滤
        all_sessions = codex_sessions.list_sessions()
        assert any(s["session_id"] == "thread_abc" for s in all_sessions)
        sid = [s for s in all_sessions if s["session_id"] == "thread_abc"][0]
        assert sid["message_count"] == 2
        assert sid["model"] == "m1"
        assert sid["title"] == "Test Thread"
        filtered = codex_sessions.list_sessions(str(tmp / "work"))
        assert len(filtered) == 1
        assert codex_sessions.list_sessions(str(tmp / "other")) == []

        # parse_history
        history = codex_sessions.parse_history("thread_abc")
        assert history[0] == {"role": "user", "content": "hi"}
        assert history[1] == {"role": "assistant", "content": "hello!"}

        # get_raw_usage（rollout token_count）
        usage = codex_sessions.get_raw_usage("thread_abc")
        assert len(usage) == 1
        ru = usage[0]["rawUsage"]
        assert ru["input_tokens"] == 100
        assert ru["output_tokens"] == 20
        assert ru["total_tokens"] == 120

        # session_exists
        assert codex_sessions.session_exists("thread_abc") is True
        assert codex_sessions.session_exists("nope") is False

        # write_custom_title
        codex_sessions.write_custom_title("thread_abc", "Renamed")
        assert codex_sessions.get_session_title("thread_abc") == "Renamed"

        # fork_session：DB 行复制
        new_id = codex_sessions.fork_session("thread_abc", "forked-test")
        assert new_id != "thread_abc"
        assert codex_sessions.session_exists(new_id) is True
        fhist = codex_sessions.parse_history(new_id)
        assert len(fhist) == 2
        assert fhist[1]["content"] == "hello!"
        listed = [s for s in codex_sessions.list_sessions() if s["session_id"] == new_id]
        assert len(listed) == 1
        assert listed[0]["parent_id"] == "thread_abc"
        print("PASS: sessions provider e2e (fake ~/.codex)")


# ── model_catalog_json 解析（任务 C）──


def _reset_models_cache():
    CodexAdapter._cached_models = None
    CodexAdapter._models_cached_at = 0.0


def _fake_codex_home(tmp_path: Path, catalog_models: list[dict] | None = None) -> Path:
    """构造 fake ~/.codex：config.toml 指向 cc-switch catalog + 可选 catalog 文件。"""
    home = tmp_path / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        'model_catalog_json = "cc-switch-model-catalog.json"\nmodel = "deepseek-ai/DeepSeek-V4-Flash"\n',
        encoding="utf-8",
    )
    if catalog_models is not None:
        (home / "cc-switch-model-catalog.json").write_text(
            json.dumps({"models": catalog_models}), encoding="utf-8"
        )
    return home


def test_parse_models_from_catalog(monkeypatch, tmp_path):
    home = _fake_codex_home(tmp_path, [
        {"slug": "a/b", "display_name": "a/b"},
        {"display_name": "c/d"},          # 无 slug → 回退 display_name
        {"slug": "a/b"},                  # 重复 → 去重
        {},                               # 无标识 → 跳过
    ])
    monkeypatch.setattr(codex_adapter, "_codex_home", lambda: home)
    assert codex_adapter._parse_models_from_catalog() == ["a/b", "c/d"]
    # 文件缺失 → []（容错）
    monkeypatch.setattr(codex_adapter, "_codex_home", lambda: tmp_path / "no-such-home")
    assert codex_adapter._parse_models_from_catalog() == []
    # config.toml 无 model_catalog_json → []（容错）
    home2 = _fake_codex_home(tmp_path, [{"slug": "x/y"}])
    (home2 / "config.toml").write_text("model = \"m\"\n", encoding="utf-8")
    monkeypatch.setattr(codex_adapter, "_codex_home", lambda: home2)
    assert codex_adapter._parse_models_from_catalog() == []
    # catalog 文件 JSON 损坏 → []（容错）
    (home2 / "cc-switch-model-catalog.json").write_text("{broken", encoding="utf-8")
    (home2 / "config.toml").write_text('model_catalog_json = "cc-switch-model-catalog.json"\n', encoding="utf-8")
    assert codex_adapter._parse_models_from_catalog() == []
    print("PASS: _parse_models_from_catalog (dedupe + fallback + fault tolerance)")


def test_supported_models_catalog_priority(monkeypatch, tmp_path):
    _reset_models_cache()
    try:
        home = _fake_codex_home(tmp_path, [{"slug": "cat/a"}, {"slug": "cat/b"}])
        monkeypatch.setattr(codex_adapter, "_codex_home", lambda: home)
        a = _adapter()
        # 无白名单 → catalog 解析
        monkeypatch.setattr(core_config, "load_config", lambda: {"codex": {}})
        assert a.supported_models == ["cat/a", "cat/b"]
        # 白名单优先于 catalog
        _reset_models_cache()
        monkeypatch.setattr(core_config, "load_config",
                            lambda: {"codex": {"models": ["wl/m1"]}})
        assert a.supported_models == ["wl/m1"]
        # catalog 缺失 → 内置默认
        _reset_models_cache()
        monkeypatch.setattr(core_config, "load_config", lambda: {"codex": {}})
        monkeypatch.setattr(codex_adapter, "_codex_home", lambda: tmp_path / "nohome")
        assert a.supported_models == ["deepseek-ai/DeepSeek-V4-Flash"]
    finally:
        _reset_models_cache()
    print("PASS: supported_models priority (whitelist > catalog > default)")


def test_supported_models_ttl_cache(monkeypatch, tmp_path):
    _reset_models_cache()
    try:
        home = _fake_codex_home(tmp_path, [{"slug": "cat/a"}])
        monkeypatch.setattr(codex_adapter, "_codex_home", lambda: home)
        monkeypatch.setattr(core_config, "load_config", lambda: {"codex": {}})
        a = _adapter()
        assert a.supported_models == ["cat/a"]
        # TTL 内修改 catalog → 不生效（缓存）
        (home / "cc-switch-model-catalog.json").write_text(
            json.dumps({"models": [{"slug": "new/x"}]}), encoding="utf-8"
        )
        assert a.supported_models == ["cat/a"]
        # 模拟 TTL 过期 → 自动重拉
        CodexAdapter._models_cached_at = 0.0
        assert a.supported_models == ["new/x"]
    finally:
        _reset_models_cache()
    print("PASS: supported_models TTL cache (5min, expired -> re-pull)")


if __name__ == "__main__":
    test_adapter_metadata()
    test_shim_resolution()
    test_build_spawn_args()
    test_build_spawn_args_with_resume()
    test_permission_mode_args()
    test_mcp_args_builds_inline_overrides()
    test_mcp_args_url_server()
    test_no_mcp_returns_empty()
    test_c_override_toml_escaping()
    test_encode_user_message()
    test_init_event_extracts_thread_id()
    test_parse_agent_message()
    test_parse_reasoning()
    test_parse_command_execution()
    test_parse_user_message_block()
    test_result_event()
    test_build_codex_args_fresh()
    test_build_codex_args_resume_filters_non_c_flags()
    test_filter_resume_opts()
    test_parse_models_from_catalog()
    test_supported_models_catalog_priority()
    test_supported_models_ttl_cache()
    test_item_to_block_mapping()
    test_norm_path()
    test_sessions_provider_e2e()
    print("\n=== ALL CODEX ADAPTER TESTS PASSED ===")
