"""Production browser coverage for the current canonical Jobs UI."""

import socket
import threading
import time
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from packages.core.host_server import PanServer
from tests.test_job_final_http_service import h, USER


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
                page.get_by_role("button", name="Settings", exact=True).click()
                page.get_by_label("Local user key", exact=True).fill(h.automation.auth.key())
                page.get_by_role("button", name="Log in", exact=True).click()
                expect(page.get_by_role("button", name="Log out", exact=True)).to_be_visible()
                page.get_by_role("button", name="All Jobs", exact=True).click()
                page.get_by_role("button", name="New Job", exact=True).click()
                expect(page.get_by_role("heading", name="Create a Job")).to_be_visible()
                assert page.get_by_role("dialog").count() == 0
                page.get_by_role("button", name="Cancel", exact=True).click()
                expect(page.get_by_role("heading", name="Create a Job")).to_have_count(0)
                page.get_by_role("button", name="New Job", exact=True).click()
                page.get_by_label("Job configuration type").select_option("action")
                name = page.get_by_label("Name", exact=True)
                expect(name).to_have_value("")
                assert not name.evaluate("e => e.required")
                expect(name).to_have_attribute("placeholder", "Blank uses server-generated job-N")
                assert not page.get_by_text("Advanced definition JSON", exact=True).locator("..").evaluate("e => e.open")
                action = page.get_by_label("Action for step 1", exact=True)
                expect(action.locator("option[value=test_message]")).to_have_count(1, timeout=10000)
                action.select_option("test_message")
                page.get_by_label("sessionId value", exact=True).fill("owned-test-session")
                page.get_by_label("text value", exact=True).fill("Owned browser draft")
                writes, receipts, lookups = [], [], []
                page.on("request", lambda request: lookups.append(request.url) if request.method == "GET" and "/api/operations?" in request.url else None)

                def lose_reply(route):
                    if route.request.method != "POST":
                        route.continue_()
                        return
                    writes.append(route.request.post_data_json)
                    response = route.fetch()
                    assert response.status == 201, response.text()
                    receipts.append(response.json())
                    route.abort()

                page.route("**/api/jobs", lose_reply)
                page.get_by_role("button", name="Create Action Job", exact=True).click()
                query = page.get_by_role("button", name="Query original operation", exact=True)
                expect(query).to_be_visible()
                expect(page.get_by_role("button", name="Create Action Job", exact=True)).to_have_count(0)
                query.click()
                expect(page.get_by_test_id("jobs-list-scroll").get_by_text("job-1", exact=True)).to_be_visible()
                assert len(writes) == len(receipts) == 1
                from urllib.parse import parse_qs, urlsplit
                assert [parse_qs(urlsplit(url).query) for url in lookups] == [{"operation": ["create"], "requestKey": [writes[0]["requestKey"]]}]
                assert "name" not in writes[0]["definition"]
                receipt = receipts[0]
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
