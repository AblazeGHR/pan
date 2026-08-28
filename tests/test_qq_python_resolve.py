"""QQ bot 独立解释器解析链测试（main._resolve_qq_python）。

覆盖优先级链：PAN_QQ_PYTHON 环境变量 > config.json qq.python > 平台默认
（_QQ_DEFAULT_PYTHON）。全部走 tmp config + monkeypatch，绝不 spawn 子进程、
绝不触碰真实 config.json / 8768/8767 端口。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.core.config as config  # noqa: E402
import main as pan_main  # noqa: E402


def _write_config(p, qq_section):
    p.write_text(json.dumps({"qq": qq_section}), encoding="utf-8")


@pytest.fixture
def isolate(tmp_path, monkeypatch):
    """tmp config + 清掉 PAN_QQ_PYTHON + 平台默认换成哨兵值（跨平台稳定）。"""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(pan_main, "_QQ_DEFAULT_PYTHON", "<platform-default>")
    monkeypatch.delenv("PAN_QQ_PYTHON", raising=False)
    return cfg_path


def test_default_config_qq_python_empty():
    """DEFAULT_CONFIG 的 qq.python 默认为空串（兜底语义）。"""
    assert config.DEFAULT_CONFIG["qq"]["python"] == ""


def test_env_overrides_config(isolate, monkeypatch):
    """env 有值 > config 有值：取 env。"""
    _write_config(isolate, {"python": "E:/cfg-python.exe"})
    monkeypatch.setenv("PAN_QQ_PYTHON", "E:/env-python.exe")
    assert pan_main._resolve_qq_python() == "E:/env-python.exe"


def test_config_overrides_default(isolate):
    """无 env、config 有值：取 config。"""
    _write_config(isolate, {"python": "E:/cfg-python.exe"})
    assert pan_main._resolve_qq_python() == "E:/cfg-python.exe"


def test_empty_config_value_falls_back_to_default(isolate):
    """无 env、config qq.python 为空串（默认）：退回平台默认。"""
    _write_config(isolate, {"python": ""})
    assert pan_main._resolve_qq_python() == "<platform-default>"


def test_missing_qq_section_falls_back_to_default(isolate):
    """config.json 没有 qq 节（旧配置）：deep-merge 出默认空串 → 平台默认。"""
    isolate.write_text(json.dumps({"port": 8999}), encoding="utf-8")
    assert pan_main._resolve_qq_python() == "<platform-default>"


def test_missing_config_file_falls_back_to_default(isolate):
    """config.json 不存在：load_config 返回纯默认 → 平台默认。"""
    assert not isolate.exists()
    assert pan_main._resolve_qq_python() == "<platform-default>"


def test_whitespace_config_value_ignored(isolate):
    """config qq.python 为纯空白：按空处理，退回平台默认。"""
    _write_config(isolate, {"python": "   "})
    assert pan_main._resolve_qq_python() == "<platform-default>"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
