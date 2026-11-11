"""Owned real HTTP regression for Illegal behavior and cold persistence."""
from test_legal_offline_http import runtime, durable, paused, kill_owned_provider
from test_real_queue_http_e2e import _create, _enqueue, _received, _wait_for


def test_settings_override_survives_cold_restart(runtime):
    target = _create(runtime, "behavior-cold")
    sid = target["id"]
    assert runtime.request("GET", f"/api/sessions/{sid}")["behavior"]["syncWithGlobal"] is True
    runtime.request("PUT", "/api/settings/ui", json={"behavior": {"autoLockIllegalQueue": False}})
    assert runtime.request("GET", f"/api/sessions/{sid}")["behavior"]["autoLockIllegalQueue"] is False
    runtime.request("PATCH", f"/api/sessions/{sid}", json={"behavior": {"syncWithGlobal": False, "autoLockIllegalQueue": True, "expectedRevision": 0}})
    conflict = runtime.request("PATCH", f"/api/sessions/{sid}", json={"behavior": {"syncWithGlobal": False, "autoLockIllegalQueue": False, "expectedRevision": 0}})
    assert conflict["code"] == "behavior_revision_conflict"
    runtime.stop()
    runtime.start()
    behavior = runtime.request("GET", f"/api/sessions/{sid}")["behavior"]
    assert behavior["syncWithGlobal"] is False
    assert behavior["autoLockIllegalQueue"] is True
    assert behavior["globalAutoLockIllegalQueue"] is False
    assert behavior["revision"] == 1
    synced = runtime.request("PATCH", f"/api/sessions/{sid}", json={"behavior": {"syncWithGlobal": True, "autoLockIllegalQueue": True, "expectedRevision": 1}})
    assert synced["behavior"]["syncWithGlobal"] is True
    assert synced["behavior"]["autoLockIllegalQueue"] is False


def test_disabled_policy_does_not_lock_preincident_queue(runtime):
    runtime.request("PUT", "/api/settings/ui", json={"behavior": {"autoLockIllegalQueue": False}})
    target = _create(runtime, "real-fifo-behavior-disabled")
    sid = target["id"]
    _enqueue(runtime, sid, "hold-A", "active")
    active = _wait_for(lambda: _received(runtime, cwd=target["workdir"], text="hold-A"))[0]
    paused(runtime, sid, True)
    item = _enqueue(runtime, sid, "preincident", "preincident")["item"]["queueItemId"]
    kill_owned_provider(runtime, active["pid"], target["workdir"])
    state = _wait_for(lambda: (value if (value := durable(runtime, sid))["illegal_batches"] else None))
    row = next(row for row in state["queue_pending"] if row["id"] == item)
    assert row.get("queueLockSource") is None
    assert runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"] is True
    new = _enqueue(runtime, sid, "after-disabled", "after-disabled")["item"]
    assert new.get("queueLockSource") is None
    manual = runtime.request("POST", f"/api/sessions/{sid}/queue", json={"text": "manual", "clientMessageId": "manual", "locked": True})["item"]
    assert next(row for row in durable(runtime, sid)["queue_pending"] if row["id"] == manual["id"])["queueLockSource"] == "manual"
    runtime.request("PUT", "/api/settings/ui", json={"behavior": {"autoLockIllegalQueue": True}})
    locked = _enqueue(runtime, sid, "after-enabled", "after-enabled")["item"]
    assert next(row for row in durable(runtime, sid)["queue_pending"] if row["id"] == locked["id"])["queueLockSource"] == "illegal"
    runtime.request("PUT", "/api/settings/ui", json={"behavior": {"autoLockIllegalQueue": False}})
    rows = {row["id"]: row for row in durable(runtime, sid)["queue_pending"]}
    assert rows[locked["id"]]["queueLockSource"] == "illegal"
    assert rows[manual["id"]]["queueLockSource"] == "manual"
    assert durable(runtime, sid)["queue_paused"] is True
    runtime.request("PATCH", f"/api/sessions/{sid}", json={"behavior": {
        "syncWithGlobal": False, "autoLockIllegalQueue": True, "expectedRevision": 0}})
    overridden = _enqueue(runtime, sid, "override-enabled", "override-enabled")["item"]
    assert next(row for row in durable(runtime, sid)["queue_pending"] if row["id"] == overridden["id"])["queueLockSource"] == "illegal"
    runtime.request("PATCH", f"/api/sessions/{sid}", json={"behavior": {
        "syncWithGlobal": True, "autoLockIllegalQueue": True, "expectedRevision": 1}})
    resynced = _enqueue(runtime, sid, "resynced-disabled", "resynced-disabled")["item"]
    assert next(row for row in durable(runtime, sid)["queue_pending"] if row["id"] == resynced["id"]).get("queueLockSource") is None


def test_production_chromium_behavior_settings(runtime, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    target = _create(runtime, "behavior-browser")
    sid = target["id"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.goto(runtime.base_url + "/react/", wait_until="networkidle")
            page.get_by_title("App settings", exact=True).click()
            page.get_by_role("tab", name="Behavior", exact=True).click()
            toggle = page.get_by_test_id("illegal-queue-behavior-section").get_by_role("switch")
            expect(toggle).to_have_attribute("aria-checked", "true")
            toggle.click()
            _wait_for(lambda: runtime.request("GET", "/api/settings/ui").get("behavior", {}).get("autoLockIllegalQueue") is False)
            page.reload(wait_until="networkidle")
            page.get_by_title("App settings", exact=True).click()
            page.get_by_role("tab", name="Behavior", exact=True).click()
            expect(page.get_by_test_id("illegal-queue-behavior-section").get_by_role("switch")).to_have_attribute("aria-checked", "false")
            page.keyboard.press("Escape")
            card = page.locator(f'[data-session-card-id="{sid}"]')
            card.get_by_role("button", name="Session actions", exact=True).click()
            page.get_by_role("button", name="Details", exact=True).click()
            page.get_by_role("tab", name="Behavior", exact=True).click()
            sync = page.get_by_role("checkbox", name="Sync with global behavior")
            lock = page.get_by_role("checkbox", name="Automatically lock new messages while Illegal")
            expect(sync).to_be_checked()
            expect(lock).not_to_be_checked()
            other = browser.new_page(viewport={"width": 1440, "height": 960})
            other.goto(runtime.base_url + "/react/", wait_until="networkidle")
            other.get_by_title("App settings", exact=True).click()
            other.get_by_role("tab", name="Behavior", exact=True).click()
            other_toggle = other.get_by_test_id("illegal-queue-behavior-section").get_by_role("switch")
            expect(other_toggle).to_have_attribute("aria-checked", "false")
            other.bring_to_front()
            other_toggle.click()
            expect(other_toggle).to_have_attribute("aria-checked", "true")
            page.bring_to_front()
            expect(lock).to_be_checked()
            other.bring_to_front()
            other_toggle.click()
            expect(other_toggle).to_have_attribute("aria-checked", "false")
            page.bring_to_front()
            expect(lock).not_to_be_checked()
            sync.click()
            expect(sync).not_to_be_checked()
            expect(lock).to_be_enabled()
            lock.click()
            _wait_for(lambda: runtime.request("GET", f"/api/sessions/{sid}")["behavior"]["autoLockIllegalQueue"] is True)
            other.bring_to_front()
            other_toggle.click()
            expect(other_toggle).to_have_attribute("aria-checked", "true")
            other_toggle.click()
            expect(other_toggle).to_have_attribute("aria-checked", "false")
            page.bring_to_front()
            expect(lock).to_be_checked()
            sync.click()
            expect(sync).to_be_checked()
            _wait_for(lambda: runtime.request("GET", f"/api/sessions/{sid}")["behavior"]["syncWithGlobal"] is True)
            expect(lock).not_to_be_checked()
            page.screenshot(path=str(tmp_path / "behavior-production.png"))
        finally:
            browser.close()
