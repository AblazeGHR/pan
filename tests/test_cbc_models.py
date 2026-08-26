"""CbcAdapter 模型解析与 TTL 缓存测试。

覆盖：
- `_parse_models_from_cbc_help` 与真实 `cbc --help` 输出格式对齐（mock 验证）
- `supported_models` 优先级：config.json 白名单 > cbc --help 解析 > 内置默认值
- TTL 缓存：有效期内不重拉、超时后自动重拉（对齐 opencode adapter 的
  _MODEL_CACHE_TTL 修复，cbc CLI 侧模型变更无需重启服务即可刷新）
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters.cbc.adapter import (  # noqa: E402
    CbcAdapter,
    _MODEL_CACHE_TTL,
    _parse_models_from_cbc_help,
)
import packages.core.adapters.cbc.adapter as adapter_module  # noqa: E402


def _reset_cache():
    """重置 class 级缓存，保证每个测试从干净状态开始。"""
    CbcAdapter._cached_models = None
    CbcAdapter._cached_models_ts = 0.0


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """默认让 config 无 models 白名单；个别测试按需覆盖。

    默认把 `_parse_models_from_cbc_help` 置空，避免测试环境真的去解析 cbc shim
    / 跑 `cbc --help` 子进程。
    """
    _reset_cache()
    monkeypatch.setattr(
        CbcAdapter,
        "_cbc_config",
        property(lambda self: {"models": []}),
    )
    monkeypatch.setattr(adapter_module, "_parse_models_from_cbc_help", lambda *a, **k: [])
    yield
    _reset_cache()


# ── _parse_models_from_cbc_help（mock subprocess） ──


def test_parse_cbc_help_output(monkeypatch):
    class FakeResult:
        stdout = "Currently supported: (glm-5.2, glm-5.1, kimi-k2.7)\n"
        stderr = ""

    monkeypatch.setattr(adapter_module.subprocess, "run", lambda *a, **k: FakeResult())
    assert _parse_models_from_cbc_help(["cbc"]) == ["glm-5.2", "glm-5.1", "kimi-k2.7"]


def test_parse_cbc_help_prefers_stdout_over_stderr_only(monkeypatch):
    class FakeResult:
        stdout = ""
        stderr = "Currently supported: (deepseek-v4-pro)\n"

    monkeypatch.setattr(adapter_module.subprocess, "run", lambda *a, **k: FakeResult())
    assert _parse_models_from_cbc_help(["cbc"]) == ["deepseek-v4-pro"]


def test_parse_cbc_help_subprocess_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise OSError("cbc not found")

    monkeypatch.setattr(adapter_module.subprocess, "run", boom)
    assert _parse_models_from_cbc_help(["cbc"]) == []


def test_parse_cbc_help_no_match_returns_empty(monkeypatch):
    class FakeResult:
        stdout = "No models listed.\n"
        stderr = ""

    monkeypatch.setattr(adapter_module.subprocess, "run", lambda *a, **k: FakeResult())
    assert _parse_models_from_cbc_help(["cbc"]) == []


# ── supported_models 优先级 ──


def test_supported_models_prefers_config_whitelist(monkeypatch):
    monkeypatch.setattr(
        CbcAdapter,
        "_cbc_config",
        property(lambda self: {"models": ["custom/model-a", "custom/model-b"]}),
    )
    monkeypatch.setattr(
        adapter_module,
        "_parse_models_from_cbc_help",
        lambda *a, **k: ["should/not/be-used"],
    )
    a = CbcAdapter()
    assert a.supported_models == ["custom/model-a", "custom/model-b"]


def test_supported_models_falls_back_to_cli(monkeypatch):
    cli = ["glm-5.2", "glm-5.1"]
    monkeypatch.setattr(
        adapter_module, "_parse_models_from_cbc_help", lambda *a, **k: cli
    )
    assert CbcAdapter().supported_models == cli


def test_supported_models_falls_back_to_builtin(monkeypatch):
    a = CbcAdapter()
    assert a.supported_models == list(CbcAdapter._BUILTIN_MODELS)


# ── TTL 缓存 ──


def test_cache_within_ttl_does_not_reparse(monkeypatch):
    calls: list[int] = []

    def fake_parse(*a, **k):
        calls.append(1)
        return ["provider/model-one"]

    monkeypatch.setattr(adapter_module, "_parse_models_from_cbc_help", fake_parse)
    a = CbcAdapter()
    assert a.supported_models == ["provider/model-one"]
    assert a.supported_models == ["provider/model-one"]
    # TTL 有效期内第二次访问命中缓存，不再重新拉取
    assert len(calls) == 1


def test_cache_refreshes_after_ttl(monkeypatch):
    calls: list[int] = []

    def fake_parse(*a, **k):
        calls.append(1)
        return ["provider/model-%d" % len(calls)]

    monkeypatch.setattr(adapter_module, "_parse_models_from_cbc_help", fake_parse)
    a = CbcAdapter()
    assert a.supported_models == ["provider/model-1"]
    assert len(calls) == 1

    # 把时间戳拨回 TTL 之前，模拟缓存过期 → 下次访问应重新拉取
    CbcAdapter._cached_models_ts = time.monotonic() - _MODEL_CACHE_TTL - 1
    assert a.supported_models == ["provider/model-2"]
    assert len(calls) == 2


def test_builtin_fallback_is_cached_within_ttl(monkeypatch):
    calls: list[int] = []

    def fake_parse(*a, **k):
        calls.append(1)
        return []  # CLI 拉取失败

    monkeypatch.setattr(adapter_module, "_parse_models_from_cbc_help", fake_parse)
    a = CbcAdapter()
    assert a.supported_models == list(CbcAdapter._BUILTIN_MODELS)
    assert a.supported_models == list(CbcAdapter._BUILTIN_MODELS)
    # 回退结果同样进 TTL 缓存，不会反复调用 CLI
    assert len(calls) == 1


def test_config_whitelist_change_picked_up_after_ttl(monkeypatch):
    config: dict = {"models": ["custom/model-a"]}

    monkeypatch.setattr(
        CbcAdapter,
        "_cbc_config",
        property(lambda self: config),
    )
    a = CbcAdapter()
    assert a.supported_models == ["custom/model-a"]

    # TTL 内改配置：读到的仍是缓存旧值
    config["models"] = ["custom/model-b"]
    assert a.supported_models == ["custom/model-a"]

    # 缓存过期后重新读 config → 新白名单生效
    CbcAdapter._cached_models_ts = time.monotonic() - _MODEL_CACHE_TTL - 1
    assert a.supported_models == ["custom/model-b"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
