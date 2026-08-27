"""Tests for MCP injection and RuleWhisper integration."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.session import Session
from packages.core.adapters.cbc import adapter as cbc_adapter
from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.core.adapters.kimi.adapter import KimiAdapter


# ------------------------------------------------------------------ #
#  MCP args injection (adapter)
# ------------------------------------------------------------------ #

def _make_session(mcp_servers=None):
    s = Session(id="ses_test", name="test", adapter="cbc")
    # mcp_args writes data/mcp-configs/<session_id>.mcp.json (Pan-internal),
    # never into the workdir. workdir is still set so tests can assert the
    # legacy .codebuddy/mcp.json is NOT written.
    s.workdir = tempfile.mkdtemp(prefix="pan-mcp-test-")
    if mcp_servers:
        s.adapter_config["mcp_servers"] = mcp_servers
    return s


def _read_mcp_json(s):
    path = cbc_adapter.MCP_CONFIG_DIR / f"{s.id}.mcp.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestCbcMCPArgs:
    @pytest.fixture(autouse=True)
    def _hermetic_config_dir(self, tmp_path, monkeypatch):
        """Point MCP_CONFIG_DIR at a temp dir so tests never touch real data/."""
        monkeypatch.setattr(cbc_adapter, "MCP_CONFIG_DIR", tmp_path / "mcp-configs")

    def test_no_mcp_servers(self):
        adapter = CbcAdapter()
        s = _make_session()
        args = adapter.mcp_args(s)
        assert args == []

    def test_empty_mcp_list(self):
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[])
        args = adapter.mcp_args(s)
        assert args == []

    def test_single_mcp_server(self):
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[{
            "name": "rulewhisper",
            "command": "python",
            "args": ["-m", "src.server.mcp"],
        }])
        args = adapter.mcp_args(s)
        assert "--mcp-config" in args
        assert len(args) == 2
        # Verify written data/mcp-configs/ses_test.mcp.json content
        config = _read_mcp_json(s)
        assert "rulewhisper" in config["mcpServers"]
        entry = config["mcpServers"]["rulewhisper"]
        assert entry["command"] == "python"
        assert entry["args"] == ["-m", "src.server.mcp"]
        assert entry["type"] == "stdio"
        # 4.9: legacy workdir/.codebuddy/mcp.json must NOT be written
        assert not os.path.exists(os.path.join(s.workdir, ".codebuddy", "mcp.json"))

    def test_multiple_mcp_servers(self):
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[
            {"name": "a", "command": "cmd_a"},
            {"name": "b", "command": "cmd_b"},
        ])
        args = adapter.mcp_args(s)
        assert len(args) == 2  # single --mcp-config pointing at one file
        assert args[0] == "--mcp-config"
        config = _read_mcp_json(s)
        assert set(config["mcpServers"].keys()) == {"a", "b"}

    def test_integrated_into_build_spawn_args(self):
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[{
            "name": "rw",
            "command": "python",
            "args": ["-m", "src.server.mcp"],
        }])
        s.model = "hy3"
        args = adapter.build_spawn_args(s)
        assert "--mcp-config" in args
        config = _read_mcp_json(s)
        assert "rw" in config["mcpServers"]

    def test_no_workdir_still_writes(self):
        """Config now lives in Pan's data dir, so workdir is not required."""
        adapter = CbcAdapter()
        s = Session(id="ses_no_wd", name="test", adapter="cbc")
        s.adapter_config["mcp_servers"] = [{"name": "rw", "command": "python"}]
        args = adapter.mcp_args(s)
        assert args[0] == "--mcp-config"
        config = _read_mcp_json(s)
        assert "rw" in config["mcpServers"]

    # ── 4.8 pan env injection ──

    def test_pan_server_env_injection(self):
        """4.8: pan server entry gets MA session identity injected into env."""
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[{
            "name": "pan",
            "command": "python",
            "args": ["-m", "packages.mcp.server"],
        }])
        args = adapter.mcp_args(s)
        assert "--mcp-config" in args
        entry = _read_mcp_json(s)["mcpServers"]["pan"]
        assert entry["env"]["PAN_AGENT_SESSION_ID"] == s.id
        assert entry["env"]["PAN_AGENT_SESSION_TITLE"] == s.name

    def test_pan_env_merges_existing_env(self):
        """Injection merges on top of any existing env passthrough."""
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[{
            "name": "pan",
            "command": "python",
            "env": {"FOO": "bar"},
        }])
        adapter.mcp_args(s)
        entry = _read_mcp_json(s)["mcpServers"]["pan"]
        assert entry["env"]["FOO"] == "bar"
        assert entry["env"]["PAN_AGENT_SESSION_ID"] == s.id
        assert entry["env"]["PAN_AGENT_SESSION_TITLE"] == s.name

    def test_non_pan_env_passthrough_untouched(self):
        """Non-pan servers keep their env passthrough unchanged (4.8 no-op)."""
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[{
            "name": "rw",
            "command": "python",
            "env": {"FOO": "bar"},
        }])
        adapter.mcp_args(s)
        entry = _read_mcp_json(s)["mcpServers"]["rw"]
        assert entry["env"] == {"FOO": "bar"}
        assert "PAN_AGENT_SESSION_ID" not in entry["env"]


class TestKimiMCPArgs:
    def test_mcp_args_kimi_home(self):
        """kimi 经 KIMI_CODE_HOME 隔离 HOME 加载 MCP（方案 C）：有 mcp_servers 时
        mcp_args 返回 --kimi-home <dir> 且隔离 HOME 生成；无 mcp_servers 返回 []。"""
        adapter = KimiAdapter()
        s = _make_session(mcp_servers=[{
            "name": "rw",
            "command": "python",
            "args": ["-m", "src.server.mcp"],
        }])
        args = adapter.mcp_args(s)
        assert args and args[0] == "--kimi-home" and len(args) == 2
        home_dir = args[1]
        # 隔离 HOME 已生成（mcp.json + config.toml）
        assert (Path(home_dir) / "mcp.json").exists()
        assert (Path(home_dir) / "config.toml").exists()
        # 无 mcp_servers → []（走原路径，使用真实用户目录）
        s2 = _make_session()
        assert adapter.mcp_args(s2) == []
        # 清理隔离 HOME（测试残留）
        import shutil
        shutil.rmtree(home_dir, ignore_errors=True)


# ------------------------------------------------------------------ #
#  Session game_id
# ------------------------------------------------------------------ #

class TestSessionGameId:
    def test_game_id_default_none(self):
        s = Session(id="ses_test", name="test")
        assert s.game_id is None

    def test_game_id_roundtrip(self):
        s = Session(id="ses_test", name="test", game_id="game_abc123")
        assert s.game_id == "game_abc123"

    def test_game_id_in_to_dict(self):
        s = Session(id="ses_test", name="test", game_id="game_xyz")
        d = s.to_dict()
        assert d["game_id"] == "game_xyz"

    def test_game_id_from_dict(self):
        data = {"id": "ses_test", "name": "test", "game_id": "game_456"}
        s = Session(**data)
        assert s.game_id == "game_456"


# ------------------------------------------------------------------ #
#  SessionTemplate mcp_servers (moved from Character)
# ------------------------------------------------------------------ #

class TestSessionTemplateMCPServers:
    def test_mcp_servers_field_default(self):
        from packages.core.manifest_loader import SessionTemplate
        t = SessionTemplate(name="t")
        assert t.mcp_servers == []

    def test_mcp_servers_parsed_from_manifest(self):
        from packages.core.manifest_loader import load_manifests
        import json as _json
        tmp = tempfile.mkdtemp(prefix="pan-mcp-tpl-")
        try:
            manifest = {
                "session_templates": [
                    {"name": "ma", "mcp_mode": "always", "mcp_servers": ["pan"]}
                ],
                "mcp_servers": [],
                "command_routes": [],
            }
            p = Path(tmp) / "manifest.json"
            p.write_text(_json.dumps(manifest), encoding="utf-8")
            cfg = load_manifests([str(p)])
            assert cfg.session_templates[0].mcp_servers == ["pan"]
            assert cfg.session_templates[0].mcp_locked is True
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ #
#  _apply_mcp_servers (PATCH mcpServers)
# ------------------------------------------------------------------ #

class TestApplyMCPServers:
    def _make_manager(self, monkeypatch):
        """Return a session + a character manager with one manifest server 'pan'."""
        import packages.web.server as srv
        from packages.core.character import CharacterManager

        s = Session(id="ses_mcp", name="t")
        cm = CharacterManager()
        cm.load_manifest(["packages/mcp/manifest.json"])
        monkeypatch.setattr(srv, "_character_manager", cm)
        return s

    def test_resolve_names_to_configs(self, monkeypatch):
        import packages.web.server as srv
        s = self._make_manager(monkeypatch)
        srv._apply_mcp_servers(s, ["pan"])
        configs = s.adapter_config.get("mcp_servers") or []
        assert len(configs) == 1
        assert configs[0]["name"] == "pan"
        # Manifest command resolves ${PLUGIN_DIR} to the project venv python
        # (absolute path, since plain "python" depends on PATH and fails when
        # launched from the cbc environment — see 54894ba).
        expected_cmd = str(Path("packages/mcp/../../.venv/Scripts/python").resolve())
        assert configs[0]["command"] == expected_cmd
        assert configs[0]["args"] == ["-m", "packages.mcp.server"]

    def test_clear_with_empty(self, monkeypatch):
        import packages.web.server as srv
        s = self._make_manager(monkeypatch)
        s.set_adapter_field("mcp_servers", [{"name": "pan"}])
        srv._apply_mcp_servers(s, [])
        assert s.adapter_config.get("mcp_servers") == []

    def test_unknown_server_raises(self, monkeypatch):
        import packages.web.server as srv
        s = self._make_manager(monkeypatch)
        try:
            srv._apply_mcp_servers(s, ["nope"])
            assert False, "expected ValueError"
        except ValueError as e:
            assert "Unknown MCP server" in str(e)

    def test_non_list_raises(self, monkeypatch):
        import packages.web.server as srv
        s = self._make_manager(monkeypatch)
        try:
            srv._apply_mcp_servers(s, "pan")
            assert False, "expected ValueError"
        except ValueError as e:
            assert "must be a list" in str(e)


# ------------------------------------------------------------------ #
#  worker_send MA prefix (packages/mcp/server.py, 立项 4.8)
# ------------------------------------------------------------------ #

class TestWorkerSendPrefix:
    def _patch_api(self, monkeypatch):
        import packages.mcp.server as mcp_server
        captured = {}

        def fake_api(method, path, body=None, timeout=30.0):
            if method == "GET" and path == "/api/list":
                # worker-1 可解析到 session（M18 后解析不到会按 deny 拒绝）
                return {"ok": True,
                        "workers": [{"workerId": "worker-1",
                                     "sessionId": "ses_ma_1"}]}
            captured["path"] = path
            captured["body"] = body
            return {"ok": True}

        monkeypatch.setattr(mcp_server, "_api", fake_api)
        return captured

    def test_prefix_applied_when_env_set(self, monkeypatch):
        monkeypatch.setenv("PAN_AGENT_SESSION_ID", "ses_ma_1")
        monkeypatch.setenv("PAN_AGENT_SESSION_TITLE", "meta-agent")
        import packages.mcp.server as mcp_server
        captured = self._patch_api(monkeypatch)
        mcp_server.worker_send(worker_id="worker-1", text="hello")
        assert captured["path"] == "/api/task"
        assert captured["body"]["workerId"] == "worker-1"
        assert captured["body"]["text"] == "////by agent : ses_ma_1 | meta-agent\nhello"

    def test_no_prefix_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("PAN_AGENT_SESSION_ID", raising=False)
        monkeypatch.delenv("PAN_AGENT_SESSION_TITLE", raising=False)
        import packages.mcp.server as mcp_server
        captured = self._patch_api(monkeypatch)
        mcp_server.worker_send(worker_id="worker-1", text="hello")
        assert captured["body"]["text"] == "hello"
