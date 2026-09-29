"""Session Template manifest-save and one-shot creation contract tests."""

from __future__ import annotations

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as sess
from packages.core.character import CharacterManager
from packages.core.adapters.cbc import CbcAdapter
from packages.web import server as srv


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    sess._cache.clear()
    sess._all_loaded = False
    monkeypatch.setattr(srv, "_character_manager", None)
    monkeypatch.setattr(srv, "load_config", lambda: {})
    monkeypatch.setattr(srv, "_resolve_workdir", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(srv, "broadcast", AsyncMock())
    yield
    sess._cache.clear()
    sess._all_loaded = False


def _manifest(path: Path, *, templates=None, profiles=None, mcp_servers=None, **extra) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    data = dict(extra)
    if templates is not None:
        data["session_templates"] = templates
    if profiles is not None:
        data["profiles"] = profiles
    if mcp_servers is not None:
        data["mcp_servers"] = mcp_servers
    target = path / "manifest.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _manager(tmp_path, *manifest_files) -> CharacterManager:
    manager = CharacterManager(str(tmp_path / "manager-data"))
    manager.load_manifest([str(path) for path in manifest_files])
    return manager


def _payload(name: str, **fields) -> dict:
    return {"name": name, **fields}


def test_targets_include_every_loaded_parseable_manifest_without_paths(tmp_path, monkeypatch):
    from packages.core import manifest_loader

    repo_root = tmp_path / "repo"
    monkeypatch.setattr(manifest_loader, "REPO_ROOT", repo_root)
    root = _manifest(repo_root, templates=[])
    plugin_a = _manifest(repo_root / "plugins" / "a", templates=[])
    plugin_b = _manifest(repo_root / "plugins" / "b", templates=[])
    invalid = repo_root / "plugins" / "broken" / "manifest.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("{broken", encoding="utf-8")
    manager = CharacterManager(str(tmp_path / "manager-data"))
    manager.load_manifest(["manifest.json", str(plugin_a), str(plugin_b), str(invalid)])
    monkeypatch.setattr(srv, "_character_manager", manager)

    response = asyncio.run(srv.api_session_template_manifest_targets())
    targets = response["manifestTargets"]
    assert response["loaded"] is True
    assert len(targets) == 3
    assert len({target["id"] for target in targets}) == 3
    assert all(set(target) == {"id", "label", "writable", "reason"} for target in targets)
    assert all("D:/" not in json.dumps(target) and "C:/" not in json.dumps(target) for target in targets)
    assert {target["label"] for target in targets} == {
        "manifest.json", "plugins/a/manifest.json", "plugins/b/manifest.json",
    }
    assert all(target["writable"] is True and target["reason"] is None for target in targets)
    manager.reload_manifest()
    assert [item["id"] for item in manager.list_session_template_manifest_targets()] == [
        item["id"] for item in targets
    ]


def test_root_manifest_is_a_writable_save_target(tmp_path, monkeypatch):
    from packages.core import manifest_loader

    repo_root = tmp_path / "repo"
    monkeypatch.setattr(manifest_loader, "REPO_ROOT", repo_root)
    root = _manifest(repo_root, templates=[], root_key="preserved")
    manager = CharacterManager(str(tmp_path / "manager-data"))
    manager.load_manifest(["manifest.json"])
    target = manager.list_session_template_manifest_targets()[0]
    monkeypatch.setattr(srv, "_character_manager", manager)

    response = asyncio.run(srv.api_save_session_template({
        "manifestId": target["id"], **_payload("root-template"),
    }))
    assert response["ok"] is True, response
    stored = json.loads(root.read_text(encoding="utf-8"))
    assert stored["root_key"] == "preserved"
    assert stored["session_templates"][0]["name"] == "root-template"


def test_read_only_manifest_target_exposes_reason_and_cannot_be_saved(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path / "read-only", templates=[])
    manifest.chmod(0o444)
    manager = _manager(tmp_path, manifest)
    monkeypatch.setattr(srv, "_character_manager", manager)
    target = manager.list_session_template_manifest_targets()[0]
    assert target["writable"] is False
    assert target["reason"]
    response = asyncio.run(srv.api_save_session_template({
        "manifestId": target["id"], **_payload("not-written"),
    }))
    assert response["ok"] is False and "not writable" in response["error"]
    manifest.chmod(0o644)


def test_save_appends_only_to_selected_manifest_and_keeps_legacy_profiles(tmp_path, monkeypatch):
    root = _manifest(
        tmp_path / "root",
        profiles=[{"name": "legacy", "system_prompt": "old"}],
        custom_key={"keep": [1, 2]},
    )
    plugin = _manifest(tmp_path / "plugins" / "target", templates=[])
    untouched = _manifest(tmp_path / "plugins" / "untouched", templates=[], owner="other")
    root_before = root.read_bytes()
    untouched_before = untouched.read_bytes()
    manager = _manager(tmp_path, root, plugin, untouched)
    targets = manager.list_session_template_manifest_targets()
    selected = next(item for item in targets if item["label"] == "target/manifest.json")
    monkeypatch.setattr(srv, "_character_manager", manager)

    response = asyncio.run(srv.api_save_session_template({
        "manifestId": selected["id"],
        **_payload(
            "custom", adapter="cbc", system_prompt="Saved rules",
            mcp_mode="optional", pan_access={"restrict_to_managed": True},
        ),
    }))
    assert response["ok"] is True, response
    assert response["manifestId"] == selected["id"]
    assert response["sessionTemplate"]["name"] == "custom"
    saved_manifest = json.loads(plugin.read_text(encoding="utf-8"))
    assert [item["name"] for item in saved_manifest["session_templates"]] == ["custom"]
    assert root.read_bytes() == root_before
    assert untouched.read_bytes() == untouched_before
    legacy_manifest = json.loads(root.read_text(encoding="utf-8"))
    assert legacy_manifest["profiles"][0]["name"] == "legacy"
    assert legacy_manifest["custom_key"] == {"keep": [1, 2]}

    manager.reload_manifest()
    assert manager.get_session_template("legacy").system_prompt == "old"
    assert manager.get_session_template("custom").system_prompt == "Saved rules"
    assert manager.get_session_template("custom").restrict_to_managed is True


def test_save_rejects_arbitrary_target_empty_name_and_cross_manifest_duplicate(tmp_path, monkeypatch):
    first = _manifest(tmp_path / "plugins" / "first", profiles=[{"name": "legacy-name"}])
    second = _manifest(tmp_path / "plugins" / "second", templates=[])
    manager = _manager(tmp_path, first, second)
    monkeypatch.setattr(srv, "_character_manager", manager)
    before = second.read_bytes()

    arbitrary = asyncio.run(srv.api_save_session_template({
        "manifestId": str(tmp_path / "outside" / "manifest.json"),
        **_payload("would-write"),
    }))
    empty = asyncio.run(srv.api_save_session_template({
        "manifestId": manager.list_session_template_manifest_targets()[1]["id"],
        **_payload(" \t "),
    }))
    duplicate = asyncio.run(srv.api_save_session_template({
        "manifestId": manager.list_session_template_manifest_targets()[1]["id"],
        **_payload("legacy-name"),
    }))
    assert arbitrary["ok"] is False and "currently loaded" in arbitrary["error"]
    assert empty["ok"] is False and "non-empty" in empty["error"]
    assert duplicate["ok"] is False and "already exists" in duplicate["error"]
    assert second.read_bytes() == before


@pytest.mark.parametrize("invalid", [
    {"mystery": 1},
    {"adapter": "not-registered"},
    {"permission_mode": "not-a-permission"},
    {"pan_access": {"restrict_to_managed": "false"}},
    {"pan_access": {"can_delete_everything": True}},
    {"mcp_servers": ["not-in-catalog"]},
    {"mcp_servers": "server-name"},
    {"mcp_mode": "sometimes"},
    {"system_prompt": ["ok", 4]},
])
def test_save_rejects_unknown_invalid_capability_and_mcp_fields(tmp_path, monkeypatch, invalid):
    manifest = _manifest(tmp_path / "plugin", templates=[], mcp_servers=[{
        "name": "available", "url": "http://example.invalid/mcp", "transport": "http",
    }])
    manager = _manager(tmp_path, manifest)
    target_id = manager.list_session_template_manifest_targets()[0]["id"]
    monkeypatch.setattr(srv, "_character_manager", manager)
    before = manifest.read_bytes()

    response = asyncio.run(srv.api_save_session_template({
        "manifestId": target_id,
        **_payload("invalid-template", **invalid),
    }))
    assert response["ok"] is False, response
    assert response["error"]
    assert manifest.read_bytes() == before


def test_save_rejects_model_outside_adapter_capabilities(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path / "plugin", templates=[])
    manager = _manager(tmp_path, manifest)
    monkeypatch.setattr(srv, "_character_manager", manager)
    monkeypatch.setattr(CbcAdapter, "supported_models", property(lambda _self: ["known-model"]))
    before = manifest.read_bytes()

    response = asyncio.run(srv.api_save_session_template({
        "manifestId": manager.list_session_template_manifest_targets()[0]["id"],
        **_payload("bad-model", adapter="cbc", model="unknown-model"),
    }))
    assert response["ok"] is False and "does not support model" in response["error"]
    assert manifest.read_bytes() == before


def test_save_rejects_invalid_mcp_catalog_transport(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path / "plugin", templates=[], mcp_servers=[{
        "name": "bad-transport", "url": "http://example.invalid/mcp", "transport": "ftp",
    }])
    manager = _manager(tmp_path, manifest)
    monkeypatch.setattr(srv, "_character_manager", manager)
    before = manifest.read_bytes()

    response = asyncio.run(srv.api_save_session_template({
        "manifestId": manager.list_session_template_manifest_targets()[0]["id"],
        **_payload("bad-transport-template", mcp_servers=["bad-transport"]),
    }))
    assert response["ok"] is False and "invalid transport" in response["error"]
    assert manifest.read_bytes() == before


def test_concurrent_duplicate_saves_and_observed_file_change_are_safe(tmp_path):
    target = _manifest(tmp_path / "plugin", templates=[])
    other = _manifest(tmp_path / "other", templates=[])
    manager = _manager(tmp_path, target, other)
    target_id = manager.list_session_template_manifest_targets()[0]["id"]

    def validate(payload):
        return {"name": payload["name"], "system_prompt": "test"}

    def save_same_name(_index):
        try:
            manager.save_session_template(target_id, _payload("one-winner"), validate=validate)
            return "saved"
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save_same_name, range(2)))
    assert outcomes.count("saved") == 1
    assert "already exists" in next(item for item in outcomes if item != "saved")
    assert [item.name for item in manager.list_session_templates()].count("one-winner") == 1

    before_target = target.read_bytes()

    def edit_other_during_validation(payload):
        other.write_text(json.dumps({"session_templates": [], "externallyChanged": True}), encoding="utf-8")
        return {"name": payload["name"]}

    with pytest.raises(ValueError, match="changed while saving"):
        manager.save_session_template(
            target_id, _payload("change-race"), validate=edit_other_during_validation,
        )
    assert target.read_bytes() == before_target
    assert "change-race" not in target.read_text(encoding="utf-8")


def test_refresh_failure_rolls_back_manifest_bytes(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path / "plugin", templates=[], keep="unchanged")
    manager = _manager(tmp_path, manifest)
    target_id = manager.list_session_template_manifest_targets()[0]["id"]
    monkeypatch.setattr(srv, "_character_manager", manager)
    before = manifest.read_bytes()
    from packages.core import manifest_loader

    original_loader = manifest_loader.load_manifests
    fail_once = {"pending": True}

    def load_once_then_fail(paths):
        if fail_once["pending"]:
            fail_once["pending"] = False
            raise OSError("simulated catalog refresh failure")
        return original_loader(paths)

    monkeypatch.setattr(manifest_loader, "load_manifests", load_once_then_fail)
    response = asyncio.run(srv.api_save_session_template({
        "manifestId": target_id,
        **_payload("must-rollback", system_prompt="rules"),
    }))
    assert response["ok"] is False and "rolled back" in response["error"]
    assert manifest.read_bytes() == before
    assert manager.get_session_template("must-rollback") is None


def test_atomic_write_failure_keeps_original_json_and_removes_temp_file(tmp_path, monkeypatch):
    from packages.core import character

    manifest = _manifest(tmp_path / "plugin", templates=[], keep="original")
    manager = _manager(tmp_path, manifest)
    monkeypatch.setattr(srv, "_character_manager", manager)
    before = manifest.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(character.os, "replace", fail_replace)
    response = asyncio.run(srv.api_save_session_template({
        "manifestId": manager.list_session_template_manifest_targets()[0]["id"],
        **_payload("must-not-write"),
    }))
    assert response["ok"] is False and "simulated replace failure" in response["error"]
    assert manifest.read_bytes() == before
    assert sorted(path.name for path in manifest.parent.iterdir()) == ["manifest.json"]


def test_create_mcp_override_default_lock_and_prompt_capabilities(tmp_path, monkeypatch):
    manifest = _manifest(
        tmp_path / "plugin",
        templates=[
            {"name": "optional", "mcp_mode": "optional", "mcp_servers": ["extra"],
             "system_prompt": "template rules"},
            {"name": "locked-on", "mcp_mode": "always", "mcp_servers": ["extra"]},
            {"name": "locked-off", "mcp_mode": "never", "mcp_servers": []},
        ],
        mcp_servers=[
            {"name": "pan", "url": "http://example.invalid/pan", "transport": "http"},
            {"name": "extra", "url": "http://example.invalid/extra", "transport": "http"},
        ],
    )
    manager = _manager(tmp_path, manifest)
    monkeypatch.setattr(srv, "_character_manager", manager)

    def create(payload):
        return asyncio.run(srv.api_create_session(payload))

    default = create({"name": "default-mcp"})
    disabled = create({"name": "explicit-empty", "mcpServers": []})
    optional_default = create({"name": "optional-default", "sessionTemplate": "optional"})
    optional_override = create({"name": "optional-override", "sessionTemplate": "optional",
                                "mcpServers": ["extra"]})
    locked_on_match = create({"name": "locked-on-match", "sessionTemplate": "locked-on",
                              "mcpServers": ["extra"]})
    locked_on_change = create({"name": "locked-on-change", "sessionTemplate": "locked-on",
                               "mcpServers": []})
    locked_off_match = create({"name": "locked-off-match", "sessionTemplate": "locked-off",
                               "mcpServers": []})
    locked_off_change = create({"name": "locked-off-change", "sessionTemplate": "locked-off",
                                "mcpServers": ["extra"]})
    prompt_caps = create({
        "name": "prompt-capabilities", "sessionTemplate": "optional",
        "systemPrompt": "", "panAccess": {
            "restrictToManaged": False, "canClaimUnmanaged": True,
            "autoClaimCreated": False,
        },
    })
    bad_boolean = create({"name": "bad-capability", "panAccess": {"restrictToManaged": "false"}})
    unknown_mcp = create({"name": "bad-mcp", "mcpServers": ["absent"]})

    assert default["mcpServers"] == ["pan"]
    assert disabled["mcpServers"] == []
    assert optional_default["mcpServers"] == []
    assert optional_override["mcpServers"] == ["extra"]
    assert locked_on_match["mcpServers"] == ["extra"]
    assert "locks MCP servers" in locked_on_change["error"]
    assert locked_off_match["mcpServers"] == []
    assert "locks MCP servers" in locked_off_change["error"]
    assert prompt_caps["systemPrompt"] == ""
    assert prompt_caps["panAccess"] == {
        "restrictToManaged": False, "canClaimUnmanaged": True,
        "autoClaimCreated": False,
    }
    assert "must be a boolean" in bad_boolean["error"]
    assert "Unknown MCP server" in unknown_mcp["error"]
    assert sess.get(prompt_caps["id"]).original_prompt == ""
    assert sess.get(prompt_caps["id"]).pan_access == {
        "restrict_to_managed": False, "can_claim_unmanaged": True,
        "auto_claim_created": False,
    }
    assert not any(item.name in {"bad-capability", "bad-mcp", "locked-on-change", "locked-off-change"}
                   for item in sess.list_all())
