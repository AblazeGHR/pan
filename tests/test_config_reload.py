"""POST /api/config/reload 配置热重载端点测试。

覆盖：
- adapter 模型缓存失效：TTL 缓存内改 config.json 白名单，热重载后立即生效
  （cbc 有 TTL、kimi 无 TTL 两种路径都覆盖）
- 端点返回各 adapter 新旧模型数量对比（modelsBefore / modelsAfter）
- worker 配置重载：reload 后模块级 _WORKER_* 变量读出新值
- plugin manifest 列表重载：新增/移除 plugin_manifests 条目生效；
  坏 manifest 中止并保留旧状态（reload_plugin_paths 原子性）
- memory.enabled 开关重载：reload 后模块级 _MEMORY_ENABLED 读出新值
- scope 过滤：adapters / worker / plugin / memory / all（默认 all）
- 幂等：重复调用结果一致
- 异常路径：invalidate 抛异常 → reloaded:false + errors（不 500）；未知 scope

全部走 tmp config + class 级缓存复位，绝不触碰真实 config.json / 端口。
"""

import asyncio
import json
import sys
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.core.config as config  # noqa: E402
import packages.core.worker as worker  # noqa: E402
import packages.web.server as srv  # noqa: E402
from packages.core.adapters import get_adapter  # noqa: E402
from packages.core.adapters.cbc.adapter import CbcAdapter  # noqa: E402
from packages.core.adapters.claude.adapter import ClaudeAdapter  # noqa: E402
from packages.core.adapters.codex.adapter import CodexAdapter  # noqa: E402
from packages.core.adapters.kimi.adapter import KimiAdapter  # noqa: E402
from packages.core.adapters.opencode.adapter import OpencodeAdapter  # noqa: E402
from packages.core.character import CharacterManager  # noqa: E402

_ADAPTER_CLASSES = [CbcAdapter, KimiAdapter, OpencodeAdapter, ClaudeAdapter, CodexAdapter]

# 5 个 adapter 全部给 models 白名单：supported_models 不触达 CLI 子进程解析。
BASE_CONFIG = {
    "port": 8999,
    "cbc": {"models": ["cbc-m1", "cbc-m2"]},
    "kimi": {"models": ["kimi-m1"]},
    "opencode": {"models": ["oc-m1"]},
    "claude": {"models": ["claude-m1"]},
    "codex": {"models": ["codex-m1"]},
    "worker": {"timeout_sec": 111, "task_timeout_sec": 222, "idle_sec": 333},
}


def _write_config(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")


def _write_plugin_manifest(path: Path, template_name: str) -> str:
    """写一个最小可用 plugin manifest，返回其路径字符串。"""
    path.write_text(json.dumps({
        "session_templates": [
            {"name": template_name, "system_prompt": f"{template_name} prompt"}
        ],
        "character_templates": [],
        "mcp_servers": [],
        "command_routes": [],
    }), encoding="utf-8")
    return str(path)


def _reset_model_caches():
    """复位 5 个 adapter 的 class 级模型缓存（测试前后各一次）。"""
    for cls in _ADAPTER_CLASSES:
        cls._cached_models = None
        if hasattr(cls, "_models_cached_at"):
            cls._models_cached_at = 0.0
        if hasattr(cls, "_cached_models_ts"):
            cls._cached_models_ts = 0.0


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """tmp config + worker 模块级变量登记恢复 + 模型缓存复位。

    scope=all 会触达 plugin/memory 分支：config 里显式给一个指向 tmp
    manifest 的 plugin_manifests，并给 srv 一个加载它的 tmp manager，
    避免读到真实仓库的 manifest.json。
    """
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    manifest_a = _write_plugin_manifest(plugins_dir / "a.json", "pa")

    cfg = dict(BASE_CONFIG)
    cfg["plugin_manifests"] = [manifest_a]
    cfg_path = tmp_path / "config.json"
    _write_config(cfg_path, cfg)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_path)
    # reload 会覆盖 worker 模块级变量：登记当前值，teardown 自动恢复。
    # _MEMORY_ENABLED 例外：import srv 时 load_memory_config() 读的是真实
    # config.json（可能 enabled=false），测试需要已知起始值 False 才可断言。
    for attr in ("_WORKER_TIMEOUT_SEC", "_WORKER_TASK_TIMEOUT_SEC", "_WORKER_IDLE_SEC"):
        monkeypatch.setattr(worker, attr, getattr(worker, attr))
    monkeypatch.setattr(worker, "_MEMORY_ENABLED", False)
    # plugin 分支需要已初始化的 manager（生产中 lifespan 保证，测试里手动给）
    mgr = CharacterManager(str(tmp_path / "data"))
    mgr.load_manifest([manifest_a])
    monkeypatch.setattr(srv, "_character_manager", mgr)
    _reset_model_caches()
    yield cfg_path
    _reset_model_caches()


# ── adapter 模型缓存失效 ──


def test_reload_picks_up_ttl_adapter_whitelist_change():
    """cbc（有 TTL）：TTL 内改 config.json 读旧缓存，热重载后立即生效。"""
    cbc = get_adapter("cbc")
    assert cbc.supported_models == ["cbc-m1", "cbc-m2"]  # 预热进缓存

    cfg = dict(BASE_CONFIG)
    cfg["cbc"] = {"models": ["cbc-new"]}
    _write_config(config.CONFIG_FILE, cfg)
    # TTL 内：仍是旧缓存（这正是热重载要解决的问题）
    assert cbc.supported_models == ["cbc-m1", "cbc-m2"]

    r = asyncio.run(srv.api_config_reload({"scope": "adapters"}))
    assert r["reloaded"] is True
    entry = next(e for e in r["adapters"] if e["name"] == "cbc")
    assert entry["modelsBefore"] == 2
    assert entry["modelsAfter"] == 1
    assert cbc.supported_models == ["cbc-new"]


def test_reload_picks_up_permanent_cache_adapter_change():
    """kimi（无 TTL，读一次不再刷新）：只有热重载能刷新。"""
    kimi = get_adapter("kimi")
    assert kimi.supported_models == ["kimi-m1"]

    cfg = dict(BASE_CONFIG)
    cfg["kimi"] = {"models": ["kimi-new"]}
    _write_config(config.CONFIG_FILE, cfg)
    assert kimi.supported_models == ["kimi-m1"]  # 旧缓存不会自行过期

    r = asyncio.run(srv.api_config_reload({"scope": "adapters"}))
    assert r["reloaded"] is True
    assert kimi.supported_models == ["kimi-new"]


def test_reload_reports_all_adapters_before_after():
    """scope=adapters 覆盖全部 5 个已注册 adapter，配置未变时前后数量一致。"""
    for name in ("cbc", "kimi", "opencode", "claude", "codex"):
        get_adapter(name).supported_models  # 预热

    r = asyncio.run(srv.api_config_reload({"scope": "adapters"}))
    assert r["reloaded"] is True
    assert {e["name"] for e in r["adapters"]} == {"cbc", "kimi", "opencode", "claude", "codex"}
    for e in r["adapters"]:
        assert e["modelsBefore"] == e["modelsAfter"]


# ── worker 配置重载 ──


def test_reload_worker_updates_module_globals():
    cfg = dict(BASE_CONFIG)
    cfg["worker"] = {"timeout_sec": 777, "task_timeout_sec": 888, "idle_sec": 999}
    _write_config(config.CONFIG_FILE, cfg)

    r = asyncio.run(srv.api_config_reload({"scope": "worker"}))
    assert r["reloaded"] is True
    assert r["worker"]["after"] == {
        "timeout_sec": 777.0,
        "task_timeout_sec": 888.0,
        "idle_sec": 999.0,
    }
    assert worker._WORKER_TIMEOUT_SEC == 777.0
    assert worker._WORKER_TASK_TIMEOUT_SEC == 888.0
    assert worker._WORKER_IDLE_SEC == 999.0


# ── scope / 幂等 / 异常路径 ──


def test_reload_default_scope_all_includes_everything():
    r = asyncio.run(srv.api_config_reload())
    assert r["reloaded"] is True
    assert "adapters" in r
    assert "worker" in r
    assert "plugin" in r
    assert "memory" in r
    assert r["plugin"]["applied"] is True
    assert "frontend" in r["requiresRestart"]


def test_reload_worker_scope_skips_adapters():
    r = asyncio.run(srv.api_config_reload({"scope": "worker"}))
    assert "adapters" not in r
    assert "worker" in r
    assert "plugin" not in r
    assert "memory" not in r


def test_reload_idempotent():
    r1 = asyncio.run(srv.api_config_reload({"scope": "all"}))
    r2 = asyncio.run(srv.api_config_reload({"scope": "all"}))
    assert r1["reloaded"] is True
    assert r2["reloaded"] is True
    assert r1["worker"]["after"] == r2["worker"]["before"] == r2["worker"]["after"]


def test_reload_unknown_scope():
    r = asyncio.run(srv.api_config_reload({"scope": "bogus"}))
    assert r["reloaded"] is False
    assert "bogus" in r["error"]


def test_reload_invalidate_failure_reported_not_500(monkeypatch):
    """单个 adapter invalidate 抛异常：reloaded:false + errors，其余照常刷新。"""

    def boom(cls):
        raise RuntimeError("boom")

    monkeypatch.setattr(CbcAdapter, "invalidate_models_cache", classmethod(boom))
    r = asyncio.run(srv.api_config_reload({"scope": "adapters"}))
    assert r["reloaded"] is False
    assert any("cbc" in e and "boom" in e for e in r["errors"])
    # 其他 adapter 不受影响，仍完成刷新
    kimi_entry = next(e for e in r["adapters"] if e["name"] == "kimi")
    assert kimi_entry["modelsAfter"] is not None


# ── plugin manifest 列表热重载 ──


def _read_cfg():
    return json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))


def test_refresh_codex_official_models_replaces_whitelist(monkeypatch):
    """Official visible entries replace only codex.models and refresh caches."""
    get_adapter("codex").supported_models  # warm the permanent/TTL cache
    cfg_before = _read_cfg()
    cfg_before["codex"]["model"] = "codex-m1"
    cfg_before["ui"] = {"language": "zh"}
    _write_config(config.CONFIG_FILE, cfg_before)

    output = json.dumps({"models": [
        {"slug": "official-a", "display_name": "A", "visibility": "list"},
        {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
        {"slug": "official-b", "display_name": "B", "visibility": None},
    ]})

    def fake_run(args, **kwargs):
        assert args == ["codex", "debug", "models"]
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    result = asyncio.run(srv.api_codex_refresh_official_models())

    assert result == {
        "ok": True,
        "before": ["codex-m1"],
        "after": ["official-a", "official-b"],
    }
    saved = _read_cfg()
    assert saved["codex"]["models"] == ["official-a", "official-b"]
    assert saved["codex"]["model"] == "codex-m1"
    assert saved["ui"] == {"language": "zh"}


def test_refresh_codex_official_models_accepts_bare_array(monkeypatch):
    """兼容顶层为数组的 catalog 输出（防御 codex CLI 格式回退）。"""
    output = json.dumps([{"slug": "m1", "display_name": "M1", "visibility": "list"}])

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    result = asyncio.run(srv.api_codex_refresh_official_models())
    assert result["after"] == ["m1"]


def test_refresh_codex_official_models_reports_command_failure(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="not logged in")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    with pytest.raises(srv.HTTPException) as exc:
        asyncio.run(srv.api_codex_refresh_official_models())
    assert exc.value.status_code == 502
    assert "not logged in" in str(exc.value.detail)


def test_refresh_codex_official_models_reports_invalid_json(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="not json", stderr="")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    with pytest.raises(srv.HTTPException) as exc:
        asyncio.run(srv.api_codex_refresh_official_models())
    assert exc.value.status_code == 502
    assert "invalid codex model catalog" in str(exc.value.detail)


def test_reload_plugin_picks_up_added_manifest():
    """新增 plugin_manifests 条目：reload 后新 manifest 的模板生效。"""
    plugins_dir = config.CONFIG_FILE.parent / "plugins"
    a_path = str(plugins_dir / "a.json")
    b_path = _write_plugin_manifest(plugins_dir / "b.json", "pb")

    mgr = srv._character_manager
    assert [t.name for t in mgr.list_session_templates()] == ["pa"]

    cfg = _read_cfg()
    cfg["plugin_manifests"] = [a_path, b_path]
    _write_config(config.CONFIG_FILE, cfg)

    r = asyncio.run(srv.api_config_reload({"scope": "plugin"}))
    assert r["reloaded"] is True
    p = r["plugin"]
    assert p["applied"] is True
    assert p["before"] == [a_path]
    assert p["after"] == [a_path, b_path]
    assert p["sessionTemplates"] == 2
    assert p["mcpServers"] == 0
    assert p["characters"] == 0
    assert p["commandRoutes"] == 0
    assert [t.name for t in mgr.list_session_templates()] == ["pa", "pb"]


def test_reload_plugin_removed_manifest_drops_templates():
    """移除条目：reload 后该 manifest 的模板不再出现。

    这是 reload_manifest 做不到的——它只重读已注册路径；列表变化必须
    走 reload_plugin_paths。
    """
    plugins_dir = config.CONFIG_FILE.parent / "plugins"
    a_path = str(plugins_dir / "a.json")
    b_path = _write_plugin_manifest(plugins_dir / "b.json", "pb")

    cfg = _read_cfg()
    cfg["plugin_manifests"] = [a_path, b_path]
    _write_config(config.CONFIG_FILE, cfg)
    r = asyncio.run(srv.api_config_reload({"scope": "plugin"}))
    assert r["plugin"]["sessionTemplates"] == 2

    cfg["plugin_manifests"] = [a_path]  # 移除 b
    _write_config(config.CONFIG_FILE, cfg)
    r = asyncio.run(srv.api_config_reload({"scope": "plugin"}))
    assert r["reloaded"] is True
    assert r["plugin"]["applied"] is True
    assert r["plugin"]["after"] == [a_path]
    assert r["plugin"]["sessionTemplates"] == 1
    assert [t.name for t in srv._character_manager.list_session_templates()] == ["pa"]


def test_reload_plugin_broken_manifest_keeps_old_state():
    """新列表里有坏 manifest：中止、报错，旧 paths + 旧 config 原样保留。"""
    plugins_dir = config.CONFIG_FILE.parent / "plugins"
    a_path = str(plugins_dir / "a.json")
    broken = plugins_dir / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    cfg = _read_cfg()
    cfg["plugin_manifests"] = [a_path, str(broken)]
    _write_config(config.CONFIG_FILE, cfg)

    mgr = srv._character_manager
    r = asyncio.run(srv.api_config_reload({"scope": "plugin"}))
    assert r["reloaded"] is False
    assert r["plugin"]["applied"] is False
    assert r["plugin"]["errors"]
    assert any("broken" in e for e in r["errors"])
    # 旧状态完整保留
    assert mgr._plugin_paths == [a_path]
    assert [t.name for t in mgr.list_session_templates()] == ["pa"]


def test_reload_plugin_scope_in_all():
    """scope=all 的 plugin 分支与 scope=plugin 行为一致。"""
    r = asyncio.run(srv.api_config_reload({"scope": "all"}))
    assert r["reloaded"] is True
    assert r["plugin"]["applied"] is True
    assert r["plugin"]["before"] == r["plugin"]["after"]
    assert r["plugin"]["sessionTemplates"] == 1


# ── memory.enabled 开关热重载 ──


def test_reload_memory_enabled_toggle():
    cfg = _read_cfg()
    cfg["memory"] = {"enabled": True}
    _write_config(config.CONFIG_FILE, cfg)

    r = asyncio.run(srv.api_config_reload({"scope": "memory"}))
    assert r["reloaded"] is True
    assert r["memory"]["before"] == {"enabled": False}
    assert r["memory"]["after"] == {"enabled": True}
    assert worker._MEMORY_ENABLED is True

    # 改回 false 后再 reload 恢复
    cfg["memory"] = {"enabled": False}
    _write_config(config.CONFIG_FILE, cfg)
    r = asyncio.run(srv.api_config_reload({"scope": "memory"}))
    assert r["memory"]["before"] == {"enabled": True}
    assert r["memory"]["after"] == {"enabled": False}
    assert worker._MEMORY_ENABLED is False


def test_reload_memory_default_false_when_key_missing():
    """config 无 memory 段：默认关闭，reload 幂等。"""
    r = asyncio.run(srv.api_config_reload({"scope": "memory"}))
    assert r["reloaded"] is True
    assert r["memory"]["before"] == {"enabled": False}
    assert r["memory"]["after"] == {"enabled": False}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
