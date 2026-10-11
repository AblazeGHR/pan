"""Disposable real API/static-assets fixture for network list measurements."""
import importlib.util
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location("network_fixture", Path(__file__).with_name("server.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
assert fixture.PORT == 5190


def seed():
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    for index in range(int(os.environ.get("PAN_NETWORK_SESSIONS", "200"))):
        fixture.session_store.create(
            f"Network Session {index:04d}", adapter="codex",
            workdir=str(fixture.WORKDIR), history=[
                {"role": "assistant", "content": f"Network history row {i}\n" +
                 "def verify_window():\n    return '历史分页保持顺序与正文完整'\n" * 90}
                for i in range(50)
            ] if index == 0 else [
                {"role": "user", "content": f"任务 {index}: 分析加载速度与列表同步，检查错误恢复和请求时序。" * 8},
                {"role": "assistant", "content": f"结果 {index}: 已检查组件订阅、网络传输和数据一致性；下一步执行验证。" * 8},
            ],
        )


install = fixture._install_test_isolation


def isolate(server):
    install(server)
    if os.environ.get("PAN_NETWORK_COMPRESS") == "0":
        from packages.web import network_json
        from starlette.responses import JSONResponse
        network_json.NetworkJSONResponse.__call__ = JSONResponse.__call__


fixture._seed_sessions = seed
fixture._install_test_isolation = isolate
fixture.main()
