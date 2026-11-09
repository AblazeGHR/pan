"""Production browser coverage for the current canonical Jobs UI."""

import socket
import threading
import time
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from packages.core.host_server import PanServer
from tests.test_job_final_http_service import h, job_def, USER


def test_production_blank_name_uses_server_default_and_current_job_editor(h, tmp_path, monkeypatch):
    """The browser uses the English JobEditor; canonical HTTP owns default naming."""
    monkeypatch.setattr("packages.core.logging_setup._resolve_log_path", lambda _: tmp_path / "host-logs/pan.log")
    dist = Path(__file__).resolve().parents[1] / "packages/web/dist"
    assert (dist / "index.html").is_file()

    from types import SimpleNamespace
    from packages.automation.api import install as install_auth
    from packages.jobs.maintenance import install

    install_auth(SimpleNamespace(app=h.app))

    @h.app.get("/api/main/startup-recovery")
    def empty_owned_recovery():
        return {"state": "no_candidates", "candidateSnapshot": []}

    install(h.app)
    h.app.mount("/react/assets", StaticFiles(directory=dist / "assets"))

    @h.app.get("/react/{path:path}")
    def bundle(path):
        return FileResponse(dist / "index.html")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    assert port not in {8767, 8768}
    server = PanServer(uvicorn.Config(h.app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started

        from playwright.sync_api import expect, sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/react/automation/canonical")
                page.get_by_label("Local user key", exact=True).fill(h.automation.auth.key())
                page.get_by_role("button", name="Log in", exact=True).click()
                page.get_by_role("button", name="New Job", exact=True).click()

                editor = page.get_by_role("dialog", name="New Job", exact=True)
                expect(editor).to_be_visible()
                name = editor.get_by_label("Name", exact=True)
                expect(name).to_have_value("")
                expect(page.get_by_text("Advanced definition JSON", exact=True)).to_have_count(0)
                expect(name).to_have_attribute("placeholder", "Blank uses server-generated job-N")
                editor.get_by_role("button", name="Close", exact=True).click()
                expect(editor).to_have_count(0)

                value = job_def(h)
                value.pop("name")
                response = h.client.post(
                    "/api/jobs",
                    json={"definition": value, "expectedRevision": 0, "requestKey": "browser-default-name"},
                )
                assert response.status_code == 201, response.text
                receipt = response.json()
                assert receipt["result"]["object"]["name"] == "job-1"
                assert h.client.get(f"/api/operations/{receipt['operationId']}").status_code == 200

                items = h.service.list("job", actor=USER)["items"]
                assert len(items) == 1 and items[0]["name"] == "job-1" and items[0]["description"] == ""
                assert not h.service.final.get(items[0]["id"])["runs"]
                assert h.provider.calls == []
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(8)
        listener.close()
        assert not thread.is_alive()
