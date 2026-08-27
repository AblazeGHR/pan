"""OpenCode adapter 模型解析与 TTL 缓存测试。

覆盖：
- 多段模型名解析（两 / 三 / 四段），对应真实 `opencode models` 输出
  （73 行 = 26 两段 + 38 三段 + 9 四段）
- 杂行（注释 / 分组标题 / 空行 / 表头）过滤，不被误收为模型
- `_parse_models_from_opencode` 与真实 subprocess 输出格式对齐（mock 验证）
- `supported_models` 优先级：config.json 白名单 > CLI 自动识别 > 内置默认值
- TTL 缓存：有效期内不重拉、超时后自动重拉
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.adapters.opencode.adapter import (  # noqa: E402
    OpencodeAdapter,
    _MODEL_LINE_RE,
    _parse_models_from_opencode,
)
import packages.core.adapters.opencode.adapter as adapter_module  # noqa: E402


def _reset_cache():
    """重置 class 级缓存，保证每个测试从干净状态开始。"""
    OpencodeAdapter._cached_models = None
    OpencodeAdapter._cached_models_ts = 0.0


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """默认让 config 无 models 白名单；个别测试按需覆盖。"""
    _reset_cache()
    monkeypatch.setattr(
        OpencodeAdapter,
        "_opencode_config",
        property(lambda self: {"models": []}),
    )
    yield
    _reset_cache()


# ── 正则：多段模型名 ──


def test_line_re_matches_two_segment():
    assert _MODEL_LINE_RE.match("opencode/big-pickle")
    assert _MODEL_LINE_RE.match("moonshotai-cn/kimi-k2.6")
    assert _MODEL_LINE_RE.match("moonshotai/kimi-k2-0711-preview")


def test_line_re_matches_three_segment():
    assert _MODEL_LINE_RE.match("siliconflow-cn/deepseek-ai/DeepSeek-R1")
    assert _MODEL_LINE_RE.match("siliconflow-cn/ByteDance-Seed/Seed-OSS-36B-Instruct")
    assert _MODEL_LINE_RE.match("siliconflow-cn/deepseek-ai/DeepSeek-V3.1-Terminus")


def test_line_re_matches_four_segment():
    assert _MODEL_LINE_RE.match("siliconflow-cn/Pro/deepseek-ai/DeepSeek-R1")
    assert _MODEL_LINE_RE.match("siliconflow-cn/Pro/moonshotai/Kimi-K2.6")


def test_line_re_rejects_noise():
    # 单段 = provider 分组标题 / 表头 / 工具输出
    assert not _MODEL_LINE_RE.match("providers")
    assert not _MODEL_LINE_RE.match("Model")
    assert not _MODEL_LINE_RE.match("opencode")
    # 含空格（前后缩进 / 中间空格）
    assert not _MODEL_LINE_RE.match("  opencode/big-pickle  ")
    assert not _MODEL_LINE_RE.match("opencode / big-pickle")
    # 冒号 / 方括号 / 斜杠开头等杂行
    assert not _MODEL_LINE_RE.match("opencode: big-pickle")
    assert not _MODEL_LINE_RE.match("[opencode]")
    assert not _MODEL_LINE_RE.match("/opencode/big-pickle")
    # 空段 / 尾斜杠
    assert not _MODEL_LINE_RE.match("opencode/")
    assert not _MODEL_LINE_RE.match("/opencode")


# ── _parse_models_from_opencode（mock subprocess） ──

RAW_OUTPUT = (
    "opencode/big-pickle\n"
    "moonshotai/kimi-k2.6\n"
    "siliconflow-cn/deepseek-ai/DeepSeek-R1\n"
    "siliconflow-cn/Pro/moonshotai/Kimi-K2.6\n"
    "\n"
    "# 注释：下面的 provider 分组\n"
    "// 行注释\n"
    "providers:\n"
    "  (some group title)\n"
    "Model\n"
)


def test_parse_mixed_output_filters_noise(monkeypatch):
    class FakeResult:
        stdout = RAW_OUTPUT
        stderr = ""

    monkeypatch.setattr(adapter_module.subprocess, "run", lambda *a, **k: FakeResult())
    models = _parse_models_from_opencode()
    assert models == [
        "opencode/big-pickle",
        "moonshotai/kimi-k2.6",
        "siliconflow-cn/deepseek-ai/DeepSeek-R1",
        "siliconflow-cn/Pro/moonshotai/Kimi-K2.6",
    ]


def test_parse_prefers_stdout_over_stderr_only(monkeypatch):
    class FakeResult:
        stdout = ""
        stderr = "opencode/big-pickle\n"

    monkeypatch.setattr(adapter_module.subprocess, "run", lambda *a, **k: FakeResult())
    assert _parse_models_from_opencode() == ["opencode/big-pickle"]


def test_parse_subprocess_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise OSError("opencode not found")

    monkeypatch.setattr(adapter_module.subprocess, "run", boom)
    assert _parse_models_from_opencode() == []


# ── supported_models 优先级 ──


def test_supported_models_prefers_config_whitelist(monkeypatch):
    monkeypatch.setattr(
        OpencodeAdapter,
        "_opencode_config",
        property(lambda self: {"models": ["custom/model-a", "custom/model-b"]}),
    )
    monkeypatch.setattr(
        adapter_module, "_parse_models_from_opencode", lambda: ["should/not/be-used"]
    )
    a = OpencodeAdapter()
    assert a.supported_models == ["custom/model-a", "custom/model-b"]


def test_supported_models_falls_back_to_cli(monkeypatch):
    cli = ["provider/one", "provider/org/two", "provider/region/org/three"]
    monkeypatch.setattr(adapter_module, "_parse_models_from_opencode", lambda: cli)
    assert OpencodeAdapter().supported_models == cli


def test_supported_models_falls_back_to_builtin(monkeypatch):
    monkeypatch.setattr(adapter_module, "_parse_models_from_opencode", lambda: [])
    a = OpencodeAdapter()
    assert a.supported_models == list(adapter_module._BUILTIN_MODELS)


# ── TTL 缓存 ──


def test_cache_within_ttl_does_not_reparse(monkeypatch):
    calls: list[int] = []

    def fake_parse():
        calls.append(1)
        return ["provider/model-one"]

    monkeypatch.setattr(adapter_module, "_parse_models_from_opencode", fake_parse)
    a = OpencodeAdapter()
    assert a.supported_models == ["provider/model-one"]
    assert a.supported_models == ["provider/model-one"]
    # TTL 有效期内第二次访问命中缓存，不再重新拉取
    assert len(calls) == 1


def test_cache_refreshes_after_ttl(monkeypatch):
    calls: list[int] = []

    def fake_parse():
        calls.append(1)
        return ["provider/model-%d" % len(calls)]

    monkeypatch.setattr(adapter_module, "_parse_models_from_opencode", fake_parse)
    a = OpencodeAdapter()
    assert a.supported_models == ["provider/model-1"]
    assert len(calls) == 1

    # 把时间戳拨回 TTL 之前，模拟缓存过期 → 下次访问应重新拉取
    OpencodeAdapter._cached_models_ts = (
        time.monotonic() - adapter_module._MODEL_CACHE_TTL - 1
    )
    assert a.supported_models == ["provider/model-2"]
    assert len(calls) == 2


def test_builtin_fallback_is_cached_within_ttl(monkeypatch):
    calls: list[int] = []

    def fake_parse():
        calls.append(1)
        return []  # CLI 拉取失败

    monkeypatch.setattr(adapter_module, "_parse_models_from_opencode", fake_parse)
    a = OpencodeAdapter()
    assert a.supported_models == list(adapter_module._BUILTIN_MODELS)
    assert a.supported_models == list(adapter_module._BUILTIN_MODELS)
    # 回退结果同样进 TTL 缓存，不会反复调用 CLI
    assert len(calls) == 1


def test_config_whitelist_change_picked_up_after_ttl(monkeypatch):
    config: dict = {"models": ["custom/model-a"]}

    monkeypatch.setattr(
        OpencodeAdapter,
        "_opencode_config",
        property(lambda self: config),
    )
    a = OpencodeAdapter()
    assert a.supported_models == ["custom/model-a"]

    # TTL 内改配置：读到的仍是缓存旧值
    config["models"] = ["custom/model-b"]
    assert a.supported_models == ["custom/model-a"]

    # 缓存过期后重新读 config → 新白名单生效
    OpencodeAdapter._cached_models_ts = (
        time.monotonic() - adapter_module._MODEL_CACHE_TTL - 1
    )
    assert a.supported_models == ["custom/model-b"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
