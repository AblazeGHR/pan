"""QQ gateway registration and selection without starting real QQ processes."""

import asyncio
import copy
import json

import pytest

from packages.qq import gateway_plugins as plugins
from packages.web import server


def test_seed_registration_and_autostart_are_off(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins, "MANIFEST", tmp_path / "manifest.json")
    result = plugins.list_plugins("napcat")
    assert [item["id"] for item in result["plugins"]] == ["napcat", "llonebot", "snowluma"]
    assert all(item["autoStart"] is False for item in result["plugins"])
    assert result["plugins"][-1]["wsUrl"] == "ws://127.0.0.1:3003"

    plugins.set_autostart("snowluma", True)
    saved = json.loads(plugins.MANIFEST.read_text(encoding="utf-8"))
    assert saved["plugins"][-1]["autoStart"] is True


def test_only_selected_plugin_autostarts(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins, "MANIFEST", tmp_path / "manifest.json")
    plugins.set_autostart("snowluma", True)
    called = []
    monkeypatch.setattr(plugins, "start", lambda plugin_id: called.append(plugin_id))
    plugins.auto_start("napcat")
    assert called == []
    plugins.auto_start("snowluma")
    assert called == ["snowluma"]


def test_selection_keeps_legacy_channels_and_sets_bridge_address(monkeypatch):
    config = {"qq": {"channels": [{"name": "napcat", "ws_urls": ["ws://127.0.0.1:3001"]}]}}
    saved = []
    monkeypatch.setattr(server, "read_config_file", lambda: copy.deepcopy(config))
    monkeypatch.setattr(server, "save_config", lambda value: saved.append(value))
    result = asyncio.run(server.api_select_qq_plugin("snowluma"))
    assert result["requiresPanRestart"] is True
    qq = saved[0]["qq"]
    assert qq["plugin_id"] == "snowluma"
    assert qq["channel"] == "snowluma"
    assert qq["snowluma"]["ws_urls"] == ["ws://127.0.0.1:3003"]
    assert qq["channels"] == config["qq"]["channels"]


def test_invalid_manifest_command_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins, "MANIFEST", tmp_path / "manifest.json")
    bad = {"plugins": [{"id": "bad", "name": "Bad", "channel": "onebot",
                        "wsUrl": "ws://127.0.0.1:3010", "cwd": str(tmp_path),
                        "command": ["relative.exe"], "autoStart": False}]}
    plugins.MANIFEST.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="absolute"):
        plugins.list_plugins("bad")
