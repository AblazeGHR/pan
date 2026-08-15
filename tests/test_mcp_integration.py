"""Tests for MCP injection and RuleWhisper integration."""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.session import Session
from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.core.adapters.kimi.adapter import KimiAdapter


# ------------------------------------------------------------------ #
#  MCP args injection (adapter)
# ------------------------------------------------------------------ #

def _make_session(mcp_servers=None):
    s = Session(id="ses_test", name="test", adapter="cbc")
    # mcp_args writes .codebuddy/mcp.json into the workdir
    s.workdir = tempfile.mkdtemp(prefix="pan-mcp-test-")
    if mcp_servers:
        s.adapter_config["mcp_servers"] = mcp_servers
    return s


def _read_mcp_json(s):
    path = os.path.join(s.workdir, ".codebuddy", "mcp.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestCbcMCPArgs:
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
        # Verify written .codebuddy/mcp.json content
        config = _read_mcp_json(s)
        assert "rulewhisper" in config["mcpServers"]
        entry = config["mcpServers"]["rulewhisper"]
        assert entry["command"] == "python"
        assert entry["args"] == ["-m", "src.server.mcp"]
        assert entry["type"] == "stdio"

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

    def test_no_workdir_returns_empty(self):
        """mcp_args requires a workdir to write the config file."""
        adapter = CbcAdapter()
        s = Session(id="ses_test", name="test", adapter="cbc")
        s.adapter_config["mcp_servers"] = [{"name": "rw", "command": "python"}]
        assert adapter.mcp_args(s) == []


class TestKimiMCPArgs:
    def test_mcp_args_empty(self):
        """Kimi configures MCP at user-level (~/.codebuddy/mcp.json), not CLI."""
        adapter = KimiAdapter()
        s = _make_session(mcp_servers=[{
            "name": "rw",
            "command": "python",
            "args": ["-m", "src.server.mcp"],
        }])
        assert adapter.mcp_args(s) == []


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
#  Character mcp_servers
# ------------------------------------------------------------------ #

class TestCharacterMCPServers:
    def test_mcp_servers_field_default(self):
        from packages.core.character import Character
        c = Character(id="char_test", profile_name="p", name="n")
        assert c.mcp_servers == []

    def test_mcp_servers_roundtrip(self):
        from packages.core.character import Character
        c = Character(
            id="char_test",
            profile_name="p",
            name="n",
            mcp_servers=[{"name": "rw", "command": "python"}],
        )
        d = c.to_dict()
        assert d["mcp_servers"] == [{"name": "rw", "command": "python"}]
        c2 = Character.from_dict(d)
        assert c2.mcp_servers == [{"name": "rw", "command": "python"}]


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
        assert configs[0]["command"] == "python"
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
