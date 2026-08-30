"""Tests for the app-settings ``ui`` object persisted to config.json.

Covers:
- load_config() falls back to ui defaults when config.json lacks the key
  (compat with old config files that predate the ui object)
- GET /api/settings/ui returns defaults or merged (defaults + overrides)
- PUT /api/settings/ui partial-merge: updates only given fields, preserves the
  rest of config.json, and persists atomically to disk
- PUT /api/settings/ui full replacement / creates config when missing
- save_config() atomic write: no leftover tmp file, file stays valid JSON

All tests run against a tmp config via monkeypatch — the real config.json of
the running service is never touched.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.core.config as config
import packages.web.server as srv


def _use_temp_config(tmp_path, monkeypatch):
    """Point the config module at a temp config.json; return the path."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_path)
    return cfg_path


def _write(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


_UI_DEFAULTS = {
    "defaultGroupBy": "none",
    "showMetaAgent": True,
    "showTaskAgent": True,
    "showQQ": True,
    "notifications": {
        "codexWarningToast": True,
    },
}


# ── load_config default fallback ──

def test_load_config_falls_back_to_ui_defaults_when_key_absent(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    _write(p, {"port": 9999})  # old config without "ui"
    cfg = config.load_config()
    assert cfg["ui"] == _UI_DEFAULTS
    assert cfg["port"] == 9999  # untouched


def test_load_config_falls_back_to_ui_defaults_when_file_missing(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    cfg = config.load_config()
    assert cfg["ui"] == _UI_DEFAULTS


# ── GET /api/settings/ui ──

def test_get_ui_defaults_when_absent(tmp_path, monkeypatch):
    _use_temp_config(tmp_path, monkeypatch)
    r = asyncio.run(srv.api_get_settings_ui())
    assert r == _UI_DEFAULTS


def test_get_ui_merges_partial_config(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    _write(p, {"ui": {"showQQ": False}})
    r = asyncio.run(srv.api_get_settings_ui())
    assert r["showQQ"] is False
    assert r["showMetaAgent"] is True
    assert r["showTaskAgent"] is True
    assert r["defaultGroupBy"] == "none"
    assert r["notifications"]["codexWarningToast"] is True


# ── PUT /api/settings/ui ──

def test_put_ui_partial_merge_persists(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    _write(p, {"port": 1234, "ui": {"showQQ": True}})
    r = asyncio.run(
        srv.api_put_settings_ui({"showQQ": False, "defaultGroupBy": "manager"})
    )
    assert r["showQQ"] is False
    assert r["defaultGroupBy"] == "manager"
    assert r["showMetaAgent"] is True  # untouched default preserved

    # Persisted on disk: only the ui key changed, other keys intact.
    on_disk = _read(p)
    assert on_disk["port"] == 1234
    assert on_disk["ui"]["showQQ"] is False
    assert on_disk["ui"]["defaultGroupBy"] == "manager"

    # A fresh load reflects the persisted values.
    r2 = asyncio.run(srv.api_get_settings_ui())
    assert r2["showQQ"] is False


def test_put_ui_notification_setting_persists(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    r = asyncio.run(
        srv.api_put_settings_ui({"notifications": {"codexWarningToast": False}})
    )
    assert r["notifications"]["codexWarningToast"] is False
    assert _read(p)["ui"]["notifications"]["codexWarningToast"] is False


def test_put_ui_full_replacement(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    _write(
        p,
        {
            "ui": {
                "defaultGroupBy": "workdir",
                "showMetaAgent": False,
                "showTaskAgent": False,
                "showQQ": False,
            }
        },
    )
    full = dict(_UI_DEFAULTS)
    r = asyncio.run(srv.api_put_settings_ui(full))
    assert r == full
    assert _read(p)["ui"] == full


def test_put_ui_creates_config_when_missing(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    assert not p.exists()
    r = asyncio.run(srv.api_put_settings_ui({"showQQ": False}))
    assert r["showQQ"] is False
    assert r["showMetaAgent"] is True  # defaults filled in
    assert p.exists()
    assert _read(p)["ui"]["showQQ"] is False


def test_put_ui_unknown_keys_kept_forward_compat(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    r = asyncio.run(srv.api_put_settings_ui({"futureField": "x"}))
    assert r["futureField"] == "x"
    assert _read(p)["ui"]["futureField"] == "x"


# ── save_config atomicity ──

def test_save_config_atomic_no_tmp_leftover_and_valid_json(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    _write(p, {"ui": {"showQQ": True}, "port": 1})
    config.save_config({"ui": {"showQQ": False}, "port": 1})
    # tmp file is moved away by os.replace (no leftover)
    assert not p.with_suffix(".json.tmp").exists()
    # file remains valid JSON and carries the new value
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["ui"]["showQQ"] is False
    assert data["port"] == 1


def test_read_config_file_handles_missing_and_corrupt(tmp_path, monkeypatch):
    p = _use_temp_config(tmp_path, monkeypatch)
    assert config.read_config_file() == {}
    _write(p, {"port": 1})
    assert config.read_config_file() == {"port": 1}
    p.write_text("{ not json", encoding="utf-8")
    assert config.read_config_file() == {}
