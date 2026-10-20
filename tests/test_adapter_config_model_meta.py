"""API 契约：模型列表端点附加可选倍率字段（modelCreditMultipliers）。

覆盖：
- /api/adapter/config 与 /api/models 对 cbc 返回倍率表（有数据/无数据）
- 其他 adapter（kimi）无该能力时返回空表，旧 API 形状不变
- models/default 等原字段保持纯模型 ID，不受附加字段影响
- 倍率读取移入线程：慢读期间 event loop 仍能推进其他协程
"""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters.cbc import adapter as cbc_adapter_module  # noqa: E402
from packages.core.adapters.cbc.adapter import CbcAdapter  # noqa: E402
from packages.core.adapters.kimi.adapter import KimiAdapter  # noqa: E402
from packages.web import server  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_models(monkeypatch):
    """隔离 CLI/配置读取：模型列表用固定值，倍率由各测试可控注入。"""
    CbcAdapter._cached_multipliers = None
    CbcAdapter._cached_multipliers_ts = 0.0
    monkeypatch.setattr(
        CbcAdapter, "supported_models",
        property(lambda self: ["glm-5.3-flash", "step-5-preview"]),
    )
    monkeypatch.setattr(
        KimiAdapter, "supported_models",
        property(lambda self: ["kimi-k2.6"]),
    )
    monkeypatch.setattr(
        KimiAdapter, "default_model",
        property(lambda self: "kimi-k2.6"),
    )
    yield
    CbcAdapter._cached_multipliers = None
    CbcAdapter._cached_multipliers_ts = 0.0


def test_adapter_config_cbc_includes_multipliers(monkeypatch):
    monkeypatch.setattr(
        cbc_adapter_module, "_fetch_credit_multipliers",
        lambda: {"glm-5.3-flash": 0.08, "step-5-preview": 0.0},
    )
    result = asyncio.run(server.api_adapter_config(adapter="cbc"))
    assert result["modelCreditMultipliers"] == {
        "glm-5.3-flash": 0.08,
        "step-5-preview": 0.0,
    }
    # models 仍是纯模型 ID 列表，原字段形态不变
    assert result["models"] == ["glm-5.3-flash", "step-5-preview"]
    assert result["adapter"] == "cbc"


def test_adapter_config_cbc_without_reliable_source_returns_empty(monkeypatch):
    monkeypatch.setattr(cbc_adapter_module, "_fetch_credit_multipliers", lambda: {})
    result = asyncio.run(server.api_adapter_config(adapter="cbc"))
    assert result["modelCreditMultipliers"] == {}
    assert result["models"] == ["glm-5.3-flash", "step-5-preview"]


def test_adapter_config_other_adapter_returns_empty():
    # 其他 adapter 无倍率能力：getattr 缺省 {}，旧 API 形状对它们完全不变
    result = asyncio.run(server.api_adapter_config(adapter="kimi"))
    assert result["modelCreditMultipliers"] == {}
    assert result["adapter"] == "kimi"
    assert result["models"] == ["kimi-k2.6"]


def test_models_endpoint_includes_multipliers(monkeypatch):
    monkeypatch.setattr(
        cbc_adapter_module, "_fetch_credit_multipliers",
        lambda: {"glm-5.3-flash": 0.08},
    )
    result = asyncio.run(server.api_models(adapter="cbc"))
    assert result["models"] == ["glm-5.3-flash", "step-5-preview"]
    assert result["default"]  # 原字段保留
    assert result["modelCreditMultipliers"] == {"glm-5.3-flash": 0.08}


def test_models_endpoint_other_adapter_returns_empty():
    result = asyncio.run(server.api_models(adapter="kimi"))
    assert result["modelCreditMultipliers"] == {}
    assert result["models"] == ["kimi-k2.6"]


def test_adapter_config_stays_responsive_during_slow_multiplier_read(monkeypatch):
    """慢读（线程内 sleep）期间，event loop 仍能推进其他协程。

    回归目标：倍率读取曾在 async handler 内同步执行，会卡住同实例其他
    请求/WS；现在经 asyncio.to_thread 隔离，loop 响应性不受影响。
    """

    async def scenario():
        ticked = asyncio.Event()
        stop = asyncio.Event()

        async def ticker():
            while not stop.is_set():
                await asyncio.sleep(0.01)
                ticked.set()

        def slow_fetch():
            time.sleep(0.3)  # 在线程中执行，不应阻塞 loop
            return {"glm-5.3-flash": 0.08}

        monkeypatch.setattr(cbc_adapter_module, "_fetch_credit_multipliers", slow_fetch)
        tick_task = asyncio.create_task(ticker())
        try:
            handler = asyncio.create_task(server.api_adapter_config(adapter="cbc"))
            # loop 阻塞时这行会超时；通过且 handler 未完成 → 慢读期间 loop 仍在推进
            await asyncio.wait_for(ticked.wait(), timeout=2.0)
            responsive = not handler.done()
            result = await handler
        finally:
            stop.set()
            await tick_task
        return responsive, result

    responsive, result = asyncio.run(scenario())
    assert responsive
    assert result["modelCreditMultipliers"] == {"glm-5.3-flash": 0.08}
    assert result["models"] == ["glm-5.3-flash", "step-5-preview"]


def test_models_endpoint_stays_responsive_during_slow_multiplier_read(monkeypatch):
    async def scenario():
        ticked = asyncio.Event()
        stop = asyncio.Event()

        async def ticker():
            while not stop.is_set():
                await asyncio.sleep(0.01)
                ticked.set()

        def slow_fetch():
            time.sleep(0.3)
            return {"glm-5.3-flash": 0.08}

        monkeypatch.setattr(cbc_adapter_module, "_fetch_credit_multipliers", slow_fetch)
        tick_task = asyncio.create_task(ticker())
        try:
            handler = asyncio.create_task(server.api_models(adapter="cbc"))
            await asyncio.wait_for(ticked.wait(), timeout=2.0)
            responsive = not handler.done()
            result = await handler
        finally:
            stop.set()
            await tick_task
        return responsive, result

    responsive, result = asyncio.run(scenario())
    assert responsive
    assert result["modelCreditMultipliers"] == {"glm-5.3-flash": 0.08}


def test_adapter_config_multiplier_read_failure_does_not_break_response(monkeypatch):
    def boom():
        raise RuntimeError("cache unreadable")

    monkeypatch.setattr(cbc_adapter_module, "_fetch_credit_multipliers", boom)
    result = asyncio.run(server.api_adapter_config(adapter="cbc"))
    # 读取异常被吞掉，附加字段为空，主响应形状不变
    assert result["modelCreditMultipliers"] == {}
    assert result["models"] == ["glm-5.3-flash", "step-5-preview"]
