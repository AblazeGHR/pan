"""main._spawn_wechat_bot 测试（微信子进程 spawn / 跳过）。

覆盖：
- wechat.enabled=False → 不 spawn
- enabled=True → 调 subprocess.Popen，断言 cwd / PYTHONPATH / 可执行文件
  （默认 sys.executable：微信插件只依赖项目主环境已有的 httpx/fastapi/uvicorn/mcp，
  不需要 QQ 那套独立解释器解析链）
- 已有 pid 文件且进程存活 → 跳过 spawn（防重复 spawn）
- PAN_WECHAT_PYTHON 环境变量可覆盖解释器

全部走 tmp config + monkeypatch，绝不真 spawn 子进程、绝不 bind 端口、绝不
触碰真实 data/wechat_bot.pid / 8768 端口。
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.core.config as config  # noqa: E402
import main as pan_main  # noqa: E402


class _FakePopen:
    """打桩的 subprocess.Popen：记录调用实参，wait 立即抛超时（模拟仍在跑）。"""
    calls = []

    def __init__(self, args, cwd=None, env=None):
        self.args = list(args)
        self.cwd = cwd
        self.env = env or {}
        self.pid = 77777
        _FakePopen.calls.append(self)

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(self.args, timeout)

    def poll(self):
        return None


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """tmp config + 重定向 pid 文件 + 静默健康检查 + 打桩 Popen。"""
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg)
    # pid 文件指向 tmp，避免触碰真实 data/wechat_bot.pid
    monkeypatch.setattr(pan_main, "_WECHAT_PID_FILE", tmp_path / "wechat_bot.pid")
    # 健康检查线程直接 no-op，避免真等宽限期
    monkeypatch.setattr(pan_main, "_wechat_health_check", lambda: None)
    # 只打桩 main 模块内的 subprocess.Popen（不动全局）
    monkeypatch.setattr(pan_main.subprocess, "Popen", _FakePopen)
    monkeypatch.delenv("PAN_WECHAT_PYTHON", raising=False)
    _FakePopen.calls.clear()
    yield cfg
    pan_main._wechat_proc = None


def test_disabled_skips_spawn(iso):
    """wechat.enabled=False → 不 spawn（默认即关）。"""
    iso.write_text('{"wechat": {"enabled": false}}', encoding="utf-8")
    pan_main._spawn_wechat_bot()
    assert _FakePopen.calls == []


def test_enabled_spawns_with_project_python(iso):
    """enabled=True → Popen 被调用，cwd / PYTHONPATH / 解释器正确。"""
    iso.write_text('{"wechat": {"enabled": true}}', encoding="utf-8")
    pan_main._spawn_wechat_bot()

    assert len(_FakePopen.calls) == 1
    call = _FakePopen.calls[0]
    # 默认解释器 = 当前 Pan 进程（sys.executable），非 QQ 的 miniforge
    assert call.args[0] == sys.executable
    assert call.args[1] == str(pan_main._WECHAT_BOT_PY)
    assert call.cwd == str(pan_main._WECHAT_DIR)
    # 子进程不继承父 sys.path → 显式注入 PYTHONPATH=项目根
    assert call.env.get("PYTHONPATH") == str(pan_main._PROJECT_ROOT)
    # pid 文件已写（stop 脚本据此清理）
    assert pan_main._WECHAT_PID_FILE.exists()


def test_existing_alive_pid_skips_spawn(iso, monkeypatch):
    """已有 pid 文件且进程存活 → 跳过 spawn（防重复 spawn）。"""
    iso.write_text('{"wechat": {"enabled": true}}', encoding="utf-8")
    pan_main._WECHAT_PID_FILE.write_text("55555", encoding="utf-8")
    monkeypatch.setattr(pan_main, "_is_pid_alive", lambda pid: True)
    pan_main._spawn_wechat_bot()
    assert _FakePopen.calls == []


def test_pan_wechat_python_env_overrides(iso, monkeypatch):
    """PAN_WECHAT_PYTHON 环境变量可覆盖解释器（仅此一个出口）。"""
    iso.write_text('{"wechat": {"enabled": true}}', encoding="utf-8")
    monkeypatch.setenv("PAN_WECHAT_PYTHON", "E:/custom/python.exe")
    pan_main._spawn_wechat_bot()
    assert _FakePopen.calls[0].args[0] == "E:/custom/python.exe"
