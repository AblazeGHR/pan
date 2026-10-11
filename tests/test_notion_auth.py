"""Tests for scripts/notion_auth.py — 临时承载服务的隔离性与清理。

覆盖两个曾真实发生的缺陷：

1. **承载服务不含 notion**：早期版本只起裸 ``cbc --serve``，依赖用户全局
   ``~/.codebuddy/mcp.json`` 里的 notion 条目。全局条目被清掉（为避免泄漏进每个
   Pan worker）后，``/internal/mcp/oauth/authorize`` 永远返回
   "No authorization URL available"，首次/``--force`` 授权直接失效。
   回归点：生成的临时配置必须**只含 notion**、argv 必须自带该配置并绕过批准门。

2. **临时目录不清理**：授权失败时会话留下 ``notion-auth-*`` 目录。
   回归点：无论成功/失败/异常，``tmpdir`` 都必须被删除。

Hermetic：不起真实 cbc 进程、不联网、不动真实凭证文件。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import notion_auth as na  # noqa: E402


def test_build_serve_plan_isolates_notion():
    """临时配置只含 notion；argv 自带配置并绕过项目级批准门。"""
    port = na._free_port()
    argv, tmpdir = na._build_serve_plan(port)
    try:
        cfg_path = tmpdir / ".mcp.json"
        assert cfg_path.is_file(), "临时目录里必须生成 .mcp.json"

        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        servers = cfg["mcpServers"]
        assert list(servers) == ["notion"], f"必须只含 notion，实际 {list(servers)}"
        assert servers["notion"]["url"] == na.SERVER_URL
        assert servers["notion"]["type"] == na.SERVER_TYPE
        # 不带 env/headers——托管版靠 OAuth，静态凭证会绕开 OAuth provider
        assert "env" not in servers["notion"]
        assert "headers" not in servers["notion"]

        assert "--serve" in argv
        assert str(port) in argv
        assert "--host" in argv and "127.0.0.1" in argv
        assert "--auth" in argv and "none" in argv
        # 绕过项目级批准门：否则状态停在 pendingApproval，不连接就拿不到授权 URL
        assert "--settings" in argv
        settings = json.loads(argv[argv.index("--settings") + 1])
        assert settings.get("enableAllProjectMcpServers") is True

        # 不依赖全局配置：argv 里不应出现真实 HOME 下的 mcp.json
        global_cfg = str(Path.home() / ".codebuddy" / "mcp.json")
        assert global_cfg not in " ".join(argv)
    finally:
        na.shutil.rmtree(tmpdir, ignore_errors=True)


def test_build_serve_plan_cwd_is_isolated():
    """cwd 必须是临时目录，不能是真实 HOME 或当前工作目录。"""
    _, tmpdir = na._build_serve_plan(na._free_port())
    try:
        assert tmpdir.is_dir()
        assert tmpdir != Path.home()
        assert "notion-auth-" in tmpdir.name
    finally:
        na.shutil.rmtree(tmpdir, ignore_errors=True)


def test_authorize_cbc_cleans_up_temp_dir_on_failure(monkeypatch):
    """取不到授权 URL 时必须返回 1 且删掉临时目录（不留 notion-auth-* 垃圾）。"""
    captured = {}

    class _FakeProc:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["cwd"] = kwargs.get("cwd")

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(na.subprocess, "Popen", _FakeProc)
    monkeypatch.setattr(na, "_wait_http_ready", lambda port, timeout: True)
    monkeypatch.setattr(na, "_cbc_fetch_authorization_url",
                        lambda port: (_ for _ in ()).throw(
                            RuntimeError("No authorization URL available")))
    monkeypatch.setattr(na, "cbc_read_credentials", lambda: None)  # 走完整流程

    rc = na.authorize_cbc(timeout=5, open_browser=False, force=False)
    assert rc == 1
    assert captured.get("cwd"), "Popen 必须收到 cwd"
    assert not Path(captured["cwd"]).exists(), "失败后临时目录必须已删除"


def test_authorize_cbc_skips_when_token_valid(monkeypatch):
    """凭证仍有效时直接返回 0，且绝不能起承载进程。"""
    monkeypatch.setattr(na, "cbc_read_credentials",
                        lambda: {"accessToken": "x",
                                 "expiresAt": na.time.time() * 1000 + 3600_000})

    def _boom(*a, **kw):
        raise AssertionError("凭证有效时不应启动承载服务")

    monkeypatch.setattr(na.subprocess, "Popen", _boom)
    assert na.authorize_cbc(timeout=5, open_browser=False, force=False) == 0


def test_clear_cbc_tokens_keeps_client_id(monkeypatch, tmp_path):
    """--force 清 token 必须连 refreshToken 一起清，否则 cbc 静默刷新、拿不到 URL。"""
    cred_path = tmp_path / ".credentials.json"
    key = na.cbc_server_key()
    cred_path.write_text(json.dumps({"mcpOAuth": {key: {
        "serverName": na.SERVER_NAME, "serverUrl": na.SERVER_URL,
        "accessToken": "a", "refreshToken": "r", "expiresAt": 1,
        "clientId": "keep-me",
    }}}), encoding="utf-8")
    monkeypatch.setattr(na, "CBC_CRED_PATH", cred_path)

    na._clear_cbc_tokens()
    entry = json.loads(cred_path.read_text(encoding="utf-8"))["mcpOAuth"][key]
    assert entry["accessToken"] == ""
    assert entry["refreshToken"] == ""
    assert entry["expiresAt"] == 0
    assert entry["clientId"] == "keep-me", "clientId 必须保留（否则要重新注册）"


def test_authorize_cbc_spawn_failure_releases_resources(monkeypatch, tmp_path):
    """Popen 启动失败也要关闭日志、删除目录，并传播同一个异常。"""
    monkeypatch.setattr(na, "cbc_read_credentials", lambda: None)
    monkeypatch.setattr(na, "_free_port", lambda: 12345)
    monkeypatch.setattr(na, "_cbc_argv", lambda: ["fake-cbc"])
    monkeypatch.setattr(na.tempfile, "tempdir", str(tmp_path))
    captured = {}
    failure = OSError("hermetic spawn failure")

    def fail_spawn(argv, **kwargs):
        captured["log"] = kwargs["stdout"]
        captured["cwd"] = Path(kwargs["cwd"])
        assert captured["cwd"].is_dir()
        assert not captured["log"].closed
        raise failure

    monkeypatch.setattr(na.subprocess, "Popen", fail_spawn)
    try:
        with pytest.raises(OSError) as raised:
            na.authorize_cbc(timeout=5, open_browser=False, force=False)
        assert raised.value is failure
        assert captured["log"].closed, "启动失败必须关闭日志句柄"
        assert not captured["cwd"].exists(), "启动失败必须删除临时目录"
    finally:
        # 基线红测试也不能留下句柄或临时目录。
        if "log" in captured:
            captured["log"].close()
        if "cwd" in captured:
            na.shutil.rmtree(captured["cwd"], ignore_errors=True)


def test_authorize_cbc_timeout_kills_and_reaps_before_cleanup(monkeypatch, tmp_path):
    """terminate 超时后 kill + wait 回收，随后才关闭日志、删除目录。"""
    monkeypatch.setattr(na, "cbc_read_credentials", lambda: None)
    monkeypatch.setattr(na, "_free_port", lambda: 12345)
    monkeypatch.setattr(na, "_cbc_argv", lambda: ["fake-cbc"])
    monkeypatch.setattr(na.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(na, "_wait_http_ready", lambda port, timeout: False)
    events = []
    captured = {}

    class FakeProc:
        def __init__(self, argv, **kwargs):
            captured["log"] = kwargs["stdout"]
            captured["cwd"] = Path(kwargs["cwd"])

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            assert not captured["log"].closed
            assert captured["cwd"].is_dir()
            events.append(("wait", timeout))
            if timeout is not None:
                assert timeout == 10
                raise subprocess.TimeoutExpired("fake-cbc", timeout)
            assert events[-2] == "kill", "必须先 kill 再无超时 wait 回收"
            return 0

        def kill(self):
            events.append("kill")

    monkeypatch.setattr(na.subprocess, "Popen", FakeProc)
    assert na.authorize_cbc(timeout=5, open_browser=False, force=False) == 1
    assert events == ["terminate", ("wait", 10), "kill", ("wait", None)]
    assert captured["log"].closed
    assert not captured["cwd"].exists()
