"""``GET /api/platform`` 与“不支持平台”契约的 HTTP 级测试。

两个层面：

1. 真实平台：``/api/platform`` 只返回平台名 + 能力布尔值，无需 user key /
   实例身份，也不泄露路径、账号或版本。
2. 模拟不支持平台（monkeypatch 平台层）：Terminal / Rewind / main lifecycle
   返回稳定的 ``501 unsupported_platform``，不是 500、不是半启动。

模拟只覆盖“平台判定”这一层：真实 POSIX 运行证据由 ubuntu/macOS CI 的
``python -c "import packages.web.server"`` 与单元子集提供，本文件不伪造。
"""

import asyncio
import sys

import httpx
import pytest

import packages.web.server as srv
from packages.core import platform as platform_layer

CAPABILITY_KEYS = {
    "job.runtimeOwner",
    "terminal.native",
    "terminal.detach",
    "rewind",
    "service.lifecycle",
    "processOwner",
    "process.hiddenDesktop",
    "secretStore",
}


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=srv.app), base_url="http://isolated",
    )


def _unsupported(monkeypatch):
    """把平台层钉在“只支持 Windows 能力集之外的平台”。"""
    monkeypatch.setattr(platform_layer, "is_windows", lambda value=None: False)
    monkeypatch.setattr(
        platform_layer, "supports", lambda name, platform=None: False,
    )


def test_platform_endpoint_is_read_only_and_minimal():
    async def run():
        async with _client() as client:
            # 不带任何 key / cookie / 实例身份头：与其它只读 summary 同级。
            response = await client.get("/api/platform")
        assert response.status_code == 200, response.text
        return response.json()

    payload = asyncio.run(run())
    assert set(payload) == {"platform", "capabilities"}
    assert payload["platform"] == platform_layer.platform_name(sys.platform)
    assert set(payload["capabilities"]) == CAPABILITY_KEYS
    assert all(isinstance(value, bool) for value in payload["capabilities"].values())
    # 不泄露路径 / 账号：所有字符串值只有平台名与能力名。
    for value in payload.values():
        if isinstance(value, str):
            assert "/" not in value and "\\" not in value and "@" not in value


def test_platform_endpoint_matches_capability_matrix():
    async def run():
        async with _client() as client:
            response = await client.get("/api/platform")
        return response.json()["capabilities"]

    assert asyncio.run(run()) == platform_layer.capabilities()


def test_rewind_returns_unsupported_platform(monkeypatch):
    _unsupported(monkeypatch)

    async def run():
        async with _client() as client:
            return await client.post(
                "/api/sessions/sess-1/history/msg_1/rewind", json={"scope": 1},
            )

    response = asyncio.run(run())
    assert response.status_code == 501, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unsupported_platform"
    # 稳定契约：只有静态 code/message，不回显路径或异常文本。
    assert set(body["error"]) <= {"code", "message"}
    assert "/" not in body["error"]["message"]


@pytest.mark.parametrize("operation", ["exit", "restart"])
def test_main_lifecycle_actions_return_unsupported_platform(monkeypatch, operation):
    _unsupported(monkeypatch)

    async def run():
        async with _client() as client:
            return await client.post("/api/system-actions/main-lifecycle", json={"operation": operation})

    response = asyncio.run(run())
    assert response.status_code == 501, response.text
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "unsupported_platform"


@pytest.mark.parametrize("status_fn", ["_main_exit_status", "_main_restart_status"])
def test_main_lifecycle_status_marks_unsupported(monkeypatch, tmp_path, status_fn):
    monkeypatch.setattr(srv, "_PROJECT_DIR", tmp_path)
    _unsupported(monkeypatch)
    status = getattr(srv, status_fn)()
    assert status["available"] is False
    assert status["code"] == "unsupported_platform"
    assert "Windows" in status["reason"]


def test_terminal_api_marks_non_windows_as_unsupported(monkeypatch):
    """terminal_api 的平台门是独立实现（build_runtime 注入 platform）。"""
    from packages.web import terminal_api

    runtime = terminal_api.build_runtime(env={}, platform="linux", host="127.0.0.1")
    assert runtime.state == terminal_api.STATE_DISABLED
    assert runtime.disabled_reason == "platform-not-windows"
    with pytest.raises(terminal_api.GateRejected) as rejected:
        runtime.check_ready()
    assert rejected.value.status_code == 501
    assert rejected.value.code == "unsupported_platform"

    # 绑定形态禁用仍是 503：平台不支持与配置禁用必须可区分。
    remote = terminal_api.build_runtime(env={}, platform="win32", host="0.0.0.0")
    with pytest.raises(terminal_api.GateRejected) as rejected_remote:
        remote.check_ready()
    assert rejected_remote.value.status_code == 503
    assert rejected_remote.value.code == "terminal-disabled"
