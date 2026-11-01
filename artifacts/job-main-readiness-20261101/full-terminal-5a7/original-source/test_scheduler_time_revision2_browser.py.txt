"""Owned authenticated HTTP + production Chromium native time input proof."""
import json
import sqlite3
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from packages.jobs.window import command_window_options


def test_native_time_inputs_persist_host_override_once_and_passive_template(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    repo = Path(__file__).resolve().parents[1]
    evidence = repo / "evidence/time-creator-rev4"
    evidence.mkdir(exist_ok=True)
    root = tmp_path / "profile"
    with socket.socket() as owned:
        owned.bind(("127.0.0.1", 0))
        port = owned.getsockname()[1]
    assert port not in (8767, 8768)
    with (evidence / "browser-server.log").open("wb") as log:
        child = subprocess.Popen([sys.executable, "-m", "tests.support.scheduler_management_server",
                                  "--root", str(root), "--port", str(port), "--seed-time-template-target"], cwd=repo,
                                 stdout=log, stderr=subprocess.STDOUT, **command_window_options())
        try:
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=base, timeout=10) as client:
                for _ in range(150):
                    assert child.poll() is None, "Owned server exited"
                    try:
                        if client.get("/test/ready").status_code == 200: break
                    except httpx.TransportError: pass
                    time.sleep(.1)
                key = (root / "automation/user.key").read_text().strip()
                token = client.post("/api/automation/login", json={"key": key}).json()["token"]
                client.headers["Authorization"] = "Bearer " + token
                with sync_playwright() as playwright:
                    launch = {}
                    if os.name == "nt":
                        installed = sorted((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob("chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"))
                        if installed: launch["executable_path"] = str(installed[-1])
                    browser = playwright.chromium.launch(headless=True, **launch)
                    try:
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base + "/react/automation?tab=events")
                        page.get_by_label("Local user key", exact=True).fill(key)
                        page.get_by_role("button", name="Log in", exact=True).click()
                        page.get_by_role("button", name="New Scheduler", exact=True).click()
                        page.get_by_label("Scheduler name", exact=True).fill("Host calendar")
                        page.get_by_label("Time rule type", exact=True).select_option("daily")
                        page.get_by_label("Time of day", exact=True).fill("09:00:12")
                        expect(page.get_by_label("Timezone", exact=True)).to_have_value("")
                        assert page.get_by_label("DST policy", exact=True).count() == 0
                        page.get_by_role("button", name="Save Scheduler", exact=True).click()
                        expect(page.get_by_role("article", name="Scheduler Host calendar", exact=True)).to_be_visible()
                        host = next(c for c in client.get("/api/event-creators").json()["items"] if c["name"] == "Host calendar")
                        rule = host["config"]["rules"][0]
                        assert rule["type"] == "daily" and "timezone" not in rule and "dstPolicy" not in rule
                        page.get_by_role("button", name="Edit Host calendar", exact=True).click()
                        page.get_by_label("Timezone", exact=True).fill("America/New_York")
                        page.get_by_role("button", name="Save Scheduler", exact=True).click()
                        expect(page.get_by_role("region", name="Scheduler editor")).to_have_count(0)
                        explicit = client.get("/api/event-creators/" + host["id"]).json()
                        assert explicit["config"]["rules"][0]["timezone"] == "America/New_York"
                        page.get_by_role("button", name="New Scheduler", exact=True).click()
                        page.get_by_label("Scheduler name", exact=True).fill("Offset once")
                        page.get_by_label("Time rule type", exact=True).select_option("once")
                        field = page.get_by_label("Scheduled date and time", exact=True)
                        field.fill("2027-11-07T01:30:00.123456789-04:00")
                        expect(page.get_by_text("Resolved instant: 2027-11-07T01:30:00.123456789-04:00", exact=True)).to_be_visible()
                        assert field.evaluate("e => e.checkValidity()")
                        page.get_by_role("button", name="Save Scheduler", exact=True).click()
                        expect(page.get_by_role("article", name="Scheduler Offset once", exact=True)).to_be_visible()
                        once = next(c for c in client.get("/api/event-creators").json()["items"] if c["name"] == "Offset once")
                        assert once["config"]["rules"][0]["at"] == "2027-11-07T01:30:00.123456789-04:00"
                        with sqlite3.connect((root / "canonical/event-creators/creators.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
                            stored = json.loads(db.execute("SELECT document FROM creators WHERE id=?", (once["id"],)).fetchone()[0])
                        assert stored["cursor"]["rules"][once["config"]["rules"][0]["ruleId"]]["nextFireAt"] == "2027-11-07T05:30:00.123456789Z"
                        page.get_by_role("link", name="Canonical Jobs", exact=True).click()
                        page.get_by_role("button", name="JobTemplates", exact=True).click()
                        page.get_by_role("button", name="创建 JobTemplates", exact=True).click()
                        page.get_by_label("名称", exact=True).fill("Native time template")
                        page.get_by_label("Job name", exact=True).fill("Scheduled message")
                        page.get_by_role("button", name="Add step", exact=True).click()
                        page.get_by_label("Action for step 1", exact=True).select_option("product_session_message")
                        page.get_by_label("sessionId value", exact=True).fill("owned-session")
                        page.get_by_label("text value", exact=True).fill("owned-message")
                        page.get_by_role("button", name="Add Template Scheduler", exact=True).click()
                        page.get_by_label("Time of day", exact=True).fill("10:00:07")
                        page.get_by_role("button", name="保存定义", exact=True).click()
                        expect(page.get_by_role("form", name="编辑 JobTemplates", exact=True)).to_have_count(0)
                        template = next(t for t in client.get("/api/job-templates").json()["items"] if t["name"] == "Native time template")
                        composition = template["composition"]
                        assert composition["creators"][0]["definition"]["config"]["rules"][0]["time"] == "10:00:07"
                        assert composition["triggers"][0]["creatorKeys"] == [composition["creators"][0]["key"]]
                        assert template["state"] == "draft"
                        assert len(client.get("/api/event-creators").json()["items"]) == 2
                        page.get_by_role("button", name="Publish Native time template", exact=True).click()
                        expect(page.get_by_role("button", name="Instantiate Native time template", exact=True)).to_be_visible()
                        page.get_by_role("button", name="Instantiate Native time template", exact=True).click()
                        assert not page.get_by_label("Activate future time events", exact=True).is_checked()
                        page.get_by_role("button", name="Create from time Template", exact=True).click()
                        expect(page.get_by_role("region", name="Instantiate time Template")).to_have_count(0)
                        jobs = client.get("/api/jobs").json()["items"]
                        assert len(jobs) == 1 and jobs[0]["state"] == "disabled"
                        assert len(client.get(f"/api/jobs/{jobs[0]['id']}/runs").json()["items"]) == 0
                        assert len(client.get("/api/event-creators").json()["items"]) == 3
                        assert len(client.get("/api/triggers").json()["items"]) == 1
                        assert not errors
                        page.screenshot(path=str(evidence / "native-template.png"), full_page=True)
                        (evidence / "browser-result.json").write_text(json.dumps({"productionBundle": True,
                            "authenticated": True, "chromiumVersion": browser.version,
                            "hostDefault": True, "override": True, "offsetOnceFullFraction": True,
                            "explicitOffsetOncePreserved": True, "passiveTemplateNativeFields": True,
                            "nativePublishAndInstantiateDisabledGraph": True,
                            "pageErrors": errors, "ownedServerStoppedInFinally": True}, indent=2))
                    finally:
                        browser.close()
        finally:
            child.terminate()
            child.wait(timeout=15)
