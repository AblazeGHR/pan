"""Real authenticated browser: local once/anchor use the Pan zone, not Tokyo."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from packages.jobs.window import command_window_options


def test_local_once_gap_fold_override_and_anchor_are_resolved_by_pan(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    repo = Path(__file__).resolve().parents[1]
    evidence = repo / "evidence/time-creator-rev4"
    evidence.mkdir(exist_ok=True)
    root = tmp_path / "profile"
    with socket.socket() as owned:
        owned.bind(("127.0.0.1", 0))
        port = owned.getsockname()[1]
    assert port not in (8767, 8768)
    with (evidence / "local-browser-server.log").open("wb") as log:
        child = subprocess.Popen([sys.executable, "-m", "tests.support.scheduler_management_server",
            "--root", str(root), "--port", str(port), "--time-test-host-zone", "America/New_York"],
            cwd=repo, stdout=log, stderr=subprocess.STDOUT, **command_window_options())
        try:
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=base, timeout=10) as client:
                for _ in range(150):
                    assert child.poll() is None
                    try:
                        if client.get("/test/ready").status_code == 200: break
                    except httpx.TransportError: pass
                    time.sleep(.1)
                key = (root / "automation/user.key").read_text().strip()
                token = client.post("/api/automation/login", json={"key": key}).json()["token"]
                client.headers["Authorization"] = "Bearer " + token
                def sources(): return client.get("/api/event-creators").json()["items"]
                with sync_playwright() as playwright:
                    launch = {}
                    if os.name == "nt":
                        installed = sorted((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob("chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"))
                        if installed: launch["executable_path"] = str(installed[-1])
                    browser = playwright.chromium.launch(headless=True, **launch)
                    try:
                        context = browser.new_context(timezone_id="Asia/Tokyo", viewport={"width": 1440, "height": 1050})
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base + "/react/automation?tab=events")
                        assert page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "Asia/Tokyo"
                        page.get_by_label("Local user key", exact=True).fill(key)
                        page.get_by_role("button", name="Log in", exact=True).click()
                        def new(name, kind="once"):
                            page.get_by_role("button", name="New Scheduler", exact=True).click()
                            page.get_by_label("Scheduler name", exact=True).fill(name)
                            page.get_by_label("Time rule type", exact=True).select_option(kind)
                        def save(name):
                            page.get_by_role("button", name="Save Scheduler", exact=True).click()
                            expect(page.get_by_role("article", name="Scheduler " + name, exact=True)).to_be_visible()
                            return next(s for s in sources() if s["name"] == name)
                        new("Pan local once")
                        field = page.get_by_label("Scheduled date and time", exact=True)
                        field.fill("2027-01-02 09:30:12.123456789")
                        expect(page.get_by_text("Resolved instant: 2027-01-02T14:30:12.123456789Z · America/New_York", exact=True)).to_be_visible()
                        assert field.input_value() == "2027-01-02 09:30:12.123456789"
                        assert save("Pan local once")["config"]["rules"][0]["at"] == "2027-01-02T14:30:12.123456789Z"
                        new("Override once")
                        page.get_by_label("Scheduled date and time timezone override", exact=True).fill("Asia/Singapore")
                        page.get_by_label("Scheduled date and time", exact=True).fill("2027-01-02 09:30:12.123456789")
                        expect(page.get_by_text("Resolved instant: 2027-01-02T01:30:12.123456789Z · Asia/Singapore", exact=True)).to_be_visible()
                        assert save("Override once")["config"]["rules"][0]["at"] == "2027-01-02T01:30:12.123456789Z"
                        new("Chosen fold once")
                        field = page.get_by_label("Scheduled date and time", exact=True)
                        field.fill("2027-03-14 02:30:00.123456789")
                        expect(page.get_by_role("alert")).to_contain_text("does not exist")
                        assert not field.evaluate("e => e.checkValidity()")
                        page.get_by_role("button", name="Save Scheduler", exact=True).click()
                        expect(page.get_by_role("region", name="Scheduler editor")).to_be_visible()
                        assert len(sources()) == 2
                        field.fill("2027-11-07 01:30:00.123456789")
                        first = page.get_by_role("button", name="2027-11-07T01:30:00.123456789-04:00 · 2027-11-07T05:30:00.123456789Z", exact=True)
                        second = page.get_by_role("button", name="2027-11-07T01:30:00.123456789-05:00 · 2027-11-07T06:30:00.123456789Z", exact=True)
                        expect(first).to_be_visible()
                        expect(second).to_be_visible()
                        assert not field.evaluate("e => e.checkValidity()")
                        page.screenshot(path=str(evidence / "local-fold-choice.png"), full_page=True)
                        second.click()
                        assert field.evaluate("e => e.checkValidity()")
                        assert save("Chosen fold once")["config"]["rules"][0]["at"] == "2027-11-07T06:30:00.123456789Z"
                        new("Local elapsed anchor", "interval")
                        page.get_by_label("Anchor date and time", exact=True).fill("2027-01-02 09:30:12.123456789")
                        expect(page.get_by_text("Resolved instant: 2027-01-02T14:30:12.123456789Z · America/New_York", exact=True)).to_be_visible()
                        page.get_by_label("Interval seconds", exact=True).fill("0.000000123456789")
                        spec = save("Local elapsed anchor")["config"]["rules"][0]
                        assert spec["anchor"] == "2027-01-02T14:30:12.123456789Z"
                        assert spec["seconds"] == "0.000000123456789"
                        assert not errors
                        (evidence / "local-browser-result.json").write_text(json.dumps({
                            "authenticated": True, "productionBundle": True, "chromium": browser.version,
                            "fakePanHostZone": "America/New_York", "browserZone": "Asia/Tokyo",
                            "ordinaryLocalOnce": True, "override": True, "gapBlocked": True,
                            "foldTwoActualInstantsAndExplicitSecondChoice": True,
                            "fractionalAnchor": True, "createdSources": len(sources()), "pageErrors": errors,
                            "ownedChildWaitedInFinally": True}, indent=2))
                    finally:
                        browser.close()
        finally:
            child.terminate()
            child.wait(timeout=15)
