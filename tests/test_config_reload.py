"""POST /api/config/reload 配置热重载端点测试。

覆盖：
- adapter 模型缓存失效：TTL 缓存内改 config.json 白名单，热重载后立即生效
  （cbc 有 TTL、kimi 无 TTL 两种路径都覆盖）
- 端点返回各 adapter 新旧模型数量对比（modelsBefore / modelsAfter）
- worker 配置重载：reload 后模块级 _WORKER_* 变量读出新值
- scope 过滤：adapters / worker / all（默认 all）
- 幂等：重复调用结果一致
- 异常路径：invalidate 抛异常 → reloaded:false + errors（不 500）；未知 scope

全部走 tmp config + class 级缓存复位，绝不触碰真实 config.json / 端口。
"""

import asyncio
import json
import sys
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
    """tmp config + worker 模块级变量登记恢复 + 模型缓存复位。"""
    cfg_path = tmp_path / "config.json"
    _write_config(cfg_path, BASE_CONFIG)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_path)
    # reload 会覆盖 worker 模块级变量：登记当前值，teardown 自动恢复
    for attr in ("_WORKER_TIMEOUT_SEC", "_WORKER_TASK_TIMEOUT_SEC", "_WORKER_IDLE_SEC"):
        monkeypatch.setattr(worker, attr, getattr(worker, attr))
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


def test_reload_default_scope_all_includes_both():
    r = asyncio.run(srv.api_config_reload())
    assert r["reloaded"] is True
    assert "adapters" in r
    assert "worker" in r


def test_reload_worker_scope_skips_adapters():
    r = asyncio.run(srv.api_config_reload({"scope": "worker"}))
    assert "adapters" not in r
    assert "worker" in r


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
