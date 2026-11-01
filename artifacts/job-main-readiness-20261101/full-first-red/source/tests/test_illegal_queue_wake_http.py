"""Real local HTTP/CLI proof for sticky Illegal and explicit wake ordering."""
import json
import sys
import time

import psutil
import pytest

from test_legal_offline_http import runtime, durable, paused, kill_owned_provider
from test_real_queue_http_e2e import _create, _enqueue, _received, _wait_for, _spawn


def illegal(runtime):
    s = _create(runtime, "real-fifo-sticky")
    sid = s["id"]
    _enqueue(runtime, sid, "hold-A", "active")
    running = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="hold-A"))[0]
    paused(runtime, sid, True)
    _enqueue(runtime, sid, "old backlog", "old")
    kill_owned_provider(runtime, running["pid"], s["workdir"])
    _wait_for(lambda: durable(runtime, sid)["illegal_batches"])
    return s


@pytest.mark.parametrize("action", ["wake", "manual-unlock", "start", "safe"])
def test_sticky_messages_wait_for_explicit_action_and_wake_precedes_backlog(runtime, action):
    s = illegal(runtime)
    sid = s["id"]
    extra = _enqueue(runtime, sid, "继续", "normal-continue")["item"]["id"]
    notice = runtime.request("POST", "/api/notify", json={"targetSessionId": sid,
        "text": "plugin later notice", "source": "automation"})
    assert notice["ok"]
    before = durable(runtime, sid)
    assert len(before["queue_pending"]) == 3
    assert all(row.get("queueLockSource") == "illegal" for row in before["queue_pending"])
    init_count = runtime.log_path.read_text(encoding="utf-8").count('"kind": "init"')
    paused(runtime, sid, False)
    # Test harness watchdog ticks every 0.1s; repeated ticks cannot spawn or unlock.
    time.sleep(.6)
    assert runtime.log_path.read_text(encoding="utf-8").count('"kind": "init"') == init_count
    assert runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"]
    assert not _received(runtime, cwd=s["workdir"], text="继续")
    if action == "wake":
        runtime.request("PUT", "/api/settings/session-lifecycle", json={"wakeMessage": "custom wake"})
        paused(runtime, sid, True)
        response = runtime.request("POST", "/api/sessions/wake", json={"sessionIds": [sid], "clientMessageId": "wake-1"})
        assert response["ok"]
        snapshot = durable(runtime, sid)
        assert snapshot["queue_pending"][0]["text"] == "custom wake"
        assert snapshot["queue_pending"][0].get("queueLockSource") is None
        assert all(row.get("queueLockSource") == "illegal" for row in snapshot["queue_pending"][1:])
        assert runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"]
        paused(runtime, sid, False)
    elif action == "manual-unlock":
        runtime.request("PATCH", f"/api/sessions/{sid}/queue/{extra}/lock", json={"locked": False})
    elif action == "start":
        _spawn(runtime, sid)
    else:
        result = runtime.request("POST", f"/api/sessions/{sid}/legality/mark-safe-offline")
        assert result["status"] == "updated"
    _wait_for(lambda: not runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"])
    _wait_for(lambda: not durable(runtime, sid)["queue_pending"])
    received = [row for row in _received(runtime, cwd=s["workdir"]) if row["text"] != "hold-A"]
    if action == "wake":
        assert received[0]["text"] == "custom wake", received
    assert any("plugin later notice" in row["text"] for row in received)
    assert len([row for row in received if row["text"] == "继续"]) == 1


def test_pan_and_long_job_crash_notification_stays_locked_until_wake(runtime, tmp_path):
    s = _create(runtime, "real-fifo-job-crash-sticky")
    sid = s["id"]
    _enqueue(runtime, sid, "hold-A", "active")
    active = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="hold-A"))[0]
    job = runtime.request("POST", "/api/background-jobs", json={
            "name": "Validate isolated terminal notice",
        "targetSessionId": sid, "creatorSessionId": sid,
        "argv": [sys.executable, "-u", "-c", "import time; print('JOB_LONG_STARTED', flush=True); time.sleep(86400)"],
        "cwd": s["workdir"], "label": "isolated shutdown sticky lock"})
    jid = job["jobId"]
    running = _wait_for(lambda: (value if (value := runtime.request("GET", f"/api/background-jobs/{jid}")).get("status") == "running" else None))
    job_pid = running.get("runnerPid") or running.get("pid")
    assert isinstance(job_pid, int) and job_pid != active["pid"], running
    roots = [psutil.Process(pid) for pid in {job_pid, active["pid"], runtime.process.pid}]
    owned = {}
    for root in roots:
        for proc in [root, *root.children(recursive=True)]:
            owned[proc.pid] = (proc, proc.create_time())
    # All identified processes belong to this harness; issue hard kills in the
    # same barrier release, without graceful Exit API or remote providers.
    import concurrent.futures
    import threading
    barrier = threading.Barrier(len(owned))
    def crash(entry):
        proc, created = entry
        barrier.wait(timeout=10)
        try:
            if abs(proc.create_time() - created) < .01:
                proc.kill()
        except psutil.NoSuchProcess:
            pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(owned)) as pool:
        list(pool.map(crash, owned.values()))
    runtime.stop()
    runtime.start()
    failed = _wait_for(lambda: (value if (value := runtime.request("GET", f"/api/background-jobs/{jid}")).get("status") == "failed" else None))
    event_id = f"{jid}:terminal"
    pending = _wait_for(lambda: next((row for row in durable(runtime, sid)["queue_pending"] if row.get("eventId") == event_id), None))
    assert pending["queueLockSource"] == "illegal"
    assert pending["deliveryState"] == "queued"
    assert runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"]
    time.sleep(.6)
    assert not any(jid in row["text"] for row in _received(runtime, cwd=s["workdir"]))
    before = durable(runtime, sid)
    result = runtime.request("POST", "/api/sessions/wake", json={"sessionIds": [sid], "clientMessageId": "wake-job-crash"})
    assert result["ok"]
    _wait_for(lambda: any(jid in row["text"] for row in _received(runtime, cwd=s["workdir"])))
    # The fixture CLI can log the read before Pan's original-ID receipt write
    # commits. Wait for that durable boundary without resending the message.
    _wait_for(lambda: durable(runtime, sid)["queue_delivery_ledger"][pending["id"]]["deliveryState"] == "sent_to_cli",
              label="original notification receipt persisted")
    after = durable(runtime, sid)
    receipt = after["queue_delivery_ledger"][pending["id"]]
    assert receipt["deliveryState"] == "sent_to_cli"
    received = [row for row in _received(runtime, cwd=s["workdir"]) if row["text"] != "hold-A"]
    assert received[0]["text"] == "继续"
    evidence = {"sessionId": sid, "jobId": jid, "port": runtime.base_url,
                "failedJob": failed, "beforeWake": before, "afterWake": after, "received": received}
    (tmp_path / "sticky-job-crash-result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("STICKY_JOB_CRASH", jid, pending["id"], str(tmp_path))


def test_browser_behavior_wake_setting_and_separate_action(runtime, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    s = illegal(runtime)
    sid = s["id"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.goto(runtime.base_url + "/react/", wait_until="networkidle")
            page.get_by_title("App settings", exact=True).click()
            page.get_by_role("tab", name="Behavior", exact=True).click()
            field = page.get_by_role("textbox", name="Wake message", exact=True)
            field.fill("Browser custom wake")
            page.get_by_text("Startup preference", exact=True).click()
            _wait_for(lambda: runtime.request("GET", "/api/settings/session-lifecycle")["wakeMessage"] == "Browser custom wake")
            page.screenshot(path=str(tmp_path / "wake-behavior.png"))
            page.keyboard.press("Escape")
            card = page.locator(f'[data-session-card-id="{sid}"]')
            card.click()
            page.locator("#send-queue-button").click()
            _enqueue(runtime, sid, "live late message", "browser-late")
            lock = page.get_by_role("button", name="Unlock queued message 2", exact=True)
            expect(lock.get_by_text("i", exact=True)).to_be_visible()
            card.get_by_role("button", name="Session actions", exact=True).click()
            page.get_by_role("button", name="Select", exact=True).click()
            page.get_by_role("button", name="Actions", exact=True).click()
            expect(page.get_by_role("menuitem", name="群发", exact=True)).to_be_visible()
            page.get_by_role("menuitem", name="Wake", exact=True).click()
            _wait_for(lambda: durable(runtime, sid)["queue_pending"][0].get("wakeMessage"))
            rows = durable(runtime, sid)["queue_pending"]
            assert rows[0]["text"] == "Browser custom wake"
            assert rows[0].get("queueLockSource") is None
            assert rows[1]["queueLockSource"] == "illegal"
            expect(page.get_by_role("button", name="Lock queued message 1", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Unlock queued message 2", exact=True)).to_be_visible()
            assert runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"]
            paused(runtime, sid, False)
            _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="Browser custom wake"))
            page.screenshot(path=str(tmp_path / "wake-completed.png"))
        finally:
            browser.close()
