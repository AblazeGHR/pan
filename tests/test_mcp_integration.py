"""Tests for MCP injection and RuleWhisper integration."""

import json
import sys
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
    if mcp_servers:
        s.adapter_config["mcp_servers"] = mcp_servers
    return s


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
        # Verify JSON format
        config = json.loads(args[1])
        assert config["name"] == "rulewhisper"
        assert config["command"] == "python"
        assert config["args"] == ["-m", "src.server.mcp"]

    def test_multiple_mcp_servers(self):
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[
            {"name": "a", "command": "cmd_a"},
            {"name": "b", "command": "cmd_b"},
        ])
        args = adapter.mcp_args(s)
        assert len(args) == 4  # 2 servers * 2 args each
        assert args[0] == "--mcp-config"
        assert args[2] == "--mcp-config"

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
        idx = args.index("--mcp-config")
        config = json.loads(args[idx + 1])
        assert config["name"] == "rw"

    def test_json_compact_format(self):
        """Verify JSON serialization uses compact format per spec."""
        adapter = CbcAdapter()
        s = _make_session(mcp_servers=[{
            "name": "rw",
            "command": "python",
            "args": ["-m", "src.server.mcp"],
        }])
        args = adapter.mcp_args(s)
        json_str = args[1]
        # Should not contain spaces after separators (compact format)
        assert '"name":"rw"' in json_str
        assert '"command":"python"' in json_str


class TestKimiMCPArgs:
    def test_mcp_args_injected(self):
        adapter = KimiAdapter()
        s = _make_session(mcp_servers=[{
            "name": "rw",
            "command": "python",
            "args": ["-m", "src.server.mcp"],
        }])
        s.model = "kimi-code/kimi-for-coding"
        args = adapter.build_spawn_args(s)
        assert "--mcp-config" in args


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
