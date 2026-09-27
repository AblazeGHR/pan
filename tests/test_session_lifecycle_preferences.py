"""Persisted, strict Session lifecycle settings API tests."""

import asyncio
import json

import pytest

from packages.core import config
from packages.web import server


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    return path


def test_lifecycle_settings_default_to_ask_for_old_or_missing_config(config_file):
    assert asyncio.run(server.api_get_settings_session_lifecycle()) == {
        "exitStrategy": "ask",
        "startupPreference": "ask",
    }


def test_lifecycle_settings_put_preserves_other_config_keys(config_file):
    initial = {
        "port": 8765,
        "custom": {"keep": True},
        "session_lifecycle": {"extension": "kept", "exitStrategy": "ask"},
    }
    config_file.write_text(json.dumps(initial), encoding="utf-8")

    result = asyncio.run(server.api_put_settings_session_lifecycle({
        "exitStrategy": "offline",
        "startupPreference": "sync-actual",
    }))

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert result == {
        "exitStrategy": "offline",
        "startupPreference": "sync-actual",
    }
    assert saved["port"] == 8765
    assert saved["custom"] == {"keep": True}
    assert saved["session_lifecycle"] == {
        "extension": "kept",
        "exitStrategy": "offline",
        "startupPreference": "sync-actual",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"exitStrategy": "offline-ish"},
        {"exitStrategy": True},
        {"startupPreference": "wake-all"},
        {"startupPreference": None},
        {"unexpected": "ask"},
        {},
    ],
)
def test_lifecycle_settings_reject_invalid_or_unknown_values_without_writing(
    config_file, body,
):
    original = {"port": 8765, "session_lifecycle": {"exitStrategy": "ask"}}
    config_file.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(server.HTTPException) as caught:
        asyncio.run(server.api_put_settings_session_lifecycle(body))

    assert caught.value.status_code == 422
    assert json.loads(config_file.read_text(encoding="utf-8")) == original
