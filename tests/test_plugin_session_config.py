"""Session/plugin config persistence, restart decisions and old QQ consumers."""
import asyncio
import copy
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from packages.core import session as sess
from packages.core.character import CharacterManager
from packages.core.manifest_loader import McpServer, SessionTemplate
from packages.plugins.catalog import Catalog
from packages.web import server as srv


def binding(plugin="qq", mode="gateway", enabled=True):
    return {"plugin_id": plugin, "enabled": enabled, "mode": mode}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    value = CharacterManager(str(tmp_path / "manager"))
    value.load_manifest(["manifest.json", "packages/mcp/manifest.json"])
    value._manifest_config.mcp_servers.append(McpServer("fixture", command=sys.executable, args=["-V"]))
    monkeypatch.setattr(srv, "_character_manager", value)
    monkeypatch.setattr(srv, "broadcast", AsyncMock())
    monkeypatch.setattr(srv.worker, "find_alive_worker_by_session", lambda _: None)
    monkeypatch.setattr(srv, "_resolve_workdir", lambda *a, **kw: "")
    return value


def test_plugin_update_preserves_all_old_qq_fields_and_roundtrips(manager):
    session = sess.create(name="legacy-qq", adapter="codex", adapter_config={"mcp_servers": [{
        "name": "pan-qq", "command": sys.executable, "args": ["-m", "packages.qq.mcp"]}]})
    session.qq_subscriptions = {"user:12345", "group:67890@99999"}
    session.qq_report_targets = {"group:67890@99999"}
    session.qq_report_outbox = {"legacy-key": {"target": "group:67890@99999", "text": "already attempted", "state": "attempted"}}
    old = (copy.deepcopy(session.qq_subscriptions), copy.deepcopy(session.qq_report_targets), copy.deepcopy(session.qq_report_outbox))
    response = asyncio.run(srv.api_update_session(session.id, {"pluginBindings": [binding(mode="pan_native")], "expectedPluginRevision": 0}))
    assert response["mcpServers"] == ["qq"]
    assert response["pluginSelectionRevision"] == 1
    assert response["pluginApplyStatus"] == "next_start"
    assert session.adapter_config["mcp_servers"][0]["name"] == "pan"
    assert "--core" not in session.adapter_config["mcp_servers"][0]["args"]
    restored = sess.Session._from_data(json.loads((sess.SESSION_DIR / f"{session.id}.json").read_text(encoding="utf-8")))
    assert restored.adapter_config["plugin_bindings"] == [binding(mode="pan_native")]
    assert (restored.qq_subscriptions, restored.qq_report_targets, restored.qq_report_outbox) == old
    disabled = asyncio.run(srv.api_update_session(session.id, {"pluginBindings": [binding(mode="pan_native", enabled=False)]}))
    assert disabled["mcpServers"] == []
    assert (session.qq_subscriptions, session.qq_report_targets, session.qq_report_outbox) == old


def test_gateway_only_addition_has_no_worker_restart(manager, monkeypatch):
    session = sess.create(name="gateway", adapter="codex")
    srv._set_plugin_config(session.adapter_config, [binding()])
    monkeypatch.setattr(srv.worker, "find_alive_worker_by_session", lambda _: SimpleNamespace(status="idle", worker_id="w_test"))
    restart = Mock(return_value={"status": "pending"})
    monkeypatch.setattr(srv.worker, "request_delayed_restart", restart)
    result = asyncio.run(srv.api_update_session(session.id, {"pluginBindings": [binding(), binding("fixture")], "expectedPluginRevision": 1}))
    assert result["pluginApplyStatus"] == "applied"
    restart.assert_not_called()
    switched = asyncio.run(srv.api_update_session(session.id, {"pluginBindings": [binding(mode="pan_native"), binding("fixture")]}))
    assert switched["pluginApplyStatus"] == "pending"
    restart.assert_called_once_with(session.id)


def test_invalid_revision_conflicting_fields_and_modes_do_not_mutate(manager):
    session = sess.create(name="validate", adapter="codex")
    srv._set_plugin_config(session.adapter_config, [binding()])
    before = copy.deepcopy(session.adapter_config)
    for body in [
        {"pluginBindings": [], "expectedPluginRevision": 0},
        {"pluginBindings": [], "mcpServers": []},
        {"pluginBindings": [binding("pan", "gateway")]},
        {"pluginBindings": [binding("unknown")]},
        {"pluginBindings": [{"plugin_id": "qq", "enabled": "false"}]},
    ]:
        response = asyncio.run(srv.api_update_session(session.id, body))
        assert "error" in response
        assert session.adapter_config == before


def test_old_qq_name_is_resolved_and_core_remains_a_logical_selection(manager):
    session = sess.create(name="old-api", adapter="codex")
    srv._apply_mcp_servers(session, ["pan-qq"])
    assert srv._session_plugin_fields(session)["mcpServers"] == ["qq"]
    assert session.adapter_config["mcp_servers"][0]["name"] == "qq"
    assert srv._resolve_mcp_server_configs(["pan-qq"])[0]["args"] == ["-m", "packages.qq.mcp"]
    srv._set_plugin_config(session.adapter_config, [binding(mode="pan_native")])
    assert "pan" not in srv._session_plugin_fields(session)["mcpServers"]
    with pytest.raises(ValueError, match="Duplicate MCP server"):
        srv._apply_mcp_servers(session, ["qq", "qq"])


def test_template_locks_and_explicit_empty_remain_distinct(manager):
    manager._manifest_config.session_templates.extend([
        SessionTemplate("plugins-always", mcp_mode="always", plugin_bindings=[binding()]),
        SessionTemplate("plugins-never", mcp_mode="never"),
    ])
    create = lambda body: asyncio.run(srv.api_create_session({"name": "template-" + str(len(sess._cache)), "adapter": "codex", **body}))
    inherited = create({"sessionTemplate": "plugins-always"})
    assert inherited["pluginBindings"] == [binding()]
    assert "error" in create({"sessionTemplate": "plugins-always", "pluginBindings": []})
    assert "error" in create({"sessionTemplate": "plugins-never", "pluginBindings": [binding()]})
    assert create({"sessionTemplate": "plugins-never", "pluginBindings": []})["pluginBindings"] == []
    assert create({"pluginBindings": []})["mcpServers"] == []


def test_handoff_and_branch_copy_logical_binding_independently(manager):
    source = sess.create(name="handoff-plugins", adapter="codex")
    srv._set_plugin_config(source.adapter_config, [binding(mode="pan_native")])
    source.qq_subscriptions = {"user:12345@99999"}
    sess.save(source)
    result = sess.handoff_session(source.id, "continue work", copy_settings=True)
    assert isinstance(result, tuple)
    archived, next_session = result
    assert next_session.adapter_config["plugin_bindings"] == [binding(mode="pan_native")]
    next_session.adapter_config["plugin_bindings"][0]["mode"] = "gateway"
    assert archived.adapter_config["plugin_bindings"][0]["mode"] == "pan_native"
    assert next_session.qq_subscriptions == {"user:12345@99999"}
    assert archived.qq_subscriptions == set()


def test_legacy_scheduled_qq_action_is_unchanged_by_plugin_catalog(manager, monkeypatch):
    from packages.jobs.actions import normalize_scheduled_qq_action
    from packages.core import background_jobs
    action = {"api": "send_qq", "args": {"targetType": "user", "targetId": "123456", "botUin": "999999"}}
    normalized, problem = normalize_scheduled_qq_action(copy.deepcopy(action))
    assert problem is None
    assert normalized == action
    Catalog(manager._manifest_config).compile([binding(mode="pan_native")])
    normalized_again, problem = normalize_scheduled_qq_action(action)
    assert normalized_again == normalized and problem is None
    sender = AsyncMock(return_value={"ok": True, "message_id": "legacy-message-1"})
    monkeypatch.setitem(background_jobs._scheduled_task_hooks, "qq_send", sender)
    result = asyncio.run(background_jobs._run_job_action(
        {"action": action, "text": "scheduled without Worker"}, None, "original-dispatch-key"))
    assert result["status"] == "completed" and result["messageId"] == "legacy-message-1"
    assert result["dispatchKey"] == "original-dispatch-key"
    sender.assert_awaited_once_with(target_type="private", target_id="123456", text="scheduled without Worker", bot_uin="999999")


def test_worker_start_recompiles_manifest_instead_of_reusing_saved_descriptor(tmp_path, monkeypatch):
    from packages.core import config
    from packages.core.adapters.mcp import build_mcp_servers
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"mcp_servers": [{"name": "external", "url": "https://new.example/mcp"}]}), encoding="utf-8")
    config.CONFIG_FILE.write_text(json.dumps({"plugin_manifests": [str(path)]}), encoding="utf-8")
    session = sess.Session(id="ses_restart", name="restart", adapter_config={"plugin_config_version": 2,
        "plugin_bindings": [binding("external", "standalone")],
        "mcp_servers": [{"name": "external", "url": "https://stale.example/mcp"}]})
    assert build_mcp_servers(session)["external"]["url"] == "https://new.example/mcp"


def test_new_session_defaults_can_remember_modes_and_explicit_empty(manager):
    from packages.core import config
    body = {"adapter": "codex", "outputMode": "", "sessionTemplate": "", "workdir": "", "pluginBindings": [binding()]}
    result = asyncio.run(srv.api_put_new_session_defaults(body))
    assert result["defaults"]["pluginBindings"] == [binding()]
    assert json.loads(config.CONFIG_FILE.read_text())["new_session_defaults"]["pluginBindings"] == [binding()]
    empty = asyncio.run(srv.api_put_new_session_defaults({**body, "pluginBindings": []}))
    assert empty["defaults"]["pluginBindings"] == []


def test_failed_config_write_keeps_old_plugin_view_and_file(manager, monkeypatch):
    session = sess.create(name="failed-plugin-write", adapter="codex")
    srv._set_plugin_config(session.adapter_config, [binding(mode="standalone")])
    sess.save(session)
    before_config = copy.deepcopy(session.adapter_config)
    path = sess.SESSION_DIR / f"{session.id}.json"
    before_file = path.read_bytes()
    def fail_replace(*args):
        raise OSError("isolated write failure")
    monkeypatch.setattr(sess.os, "replace", fail_replace)
    with pytest.raises(OSError, match="isolated write failure"):
        asyncio.run(srv.api_update_session(session.id, {"pluginBindings": [binding(mode="pan_native")], "expectedPluginRevision": 1}))
    assert session.adapter_config == before_config
    assert srv._session_plugin_fields(session)["pluginBindings"] == [binding(mode="standalone")]
    assert path.read_bytes() == before_file


def test_pending_write_is_not_visible_and_stale_concurrent_editor_is_rejected(manager, monkeypatch):
    async def run():
        session = sess.create(name="concurrent-plugin-save", adapter="codex")
        srv._set_plugin_config(session.adapter_config, [binding(mode="standalone")])
        sess.save(session)
        entered, release = threading.Event(), threading.Event()
        original_replace = sess.os.replace
        def slow_replace(source, target):
            if str(target).endswith(f"{session.id}.json"):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release metadata write")
            return original_replace(source, target)
        monkeypatch.setattr(sess.os, "replace", slow_replace)
        first = asyncio.create_task(srv.api_update_session(session.id, {"pluginBindings": [binding(mode="pan_native")], "expectedPluginRevision": 1}))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert session.adapter_config["plugin_bindings"] == [binding(mode="standalone")]
            # An active Worker may publish its native identity/usage while the
            # settings ticket is blocked. The plugin commit must retain it.
            session.adapter_config["cli_session_id"] = "native-progress-during-save"
            session.unread_done_generation += 1
            expected_done_generation = session.unread_done_generation
            second = asyncio.create_task(srv.api_update_session(session.id, {"pluginBindings": [binding()], "expectedPluginRevision": 1}))
            await asyncio.sleep(0)
            assert not second.done()
        finally:
            release.set()
        one, two = await asyncio.gather(first, second)
        assert one["pluginBindings"] == [binding(mode="pan_native")]
        assert "Plugin selection changed" in two["error"]
        assert session.adapter_config["plugin_bindings"] == [binding(mode="pan_native")]
        assert session.adapter_config["cli_session_id"] == "native-progress-during-save"
        assert session.unread_done_generation == expected_done_generation
    asyncio.run(run())
