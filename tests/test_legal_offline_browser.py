"""Chromium observes real Illegal/recovery WebSocket patches without refresh."""
import json
from playwright.sync_api import sync_playwright, expect

from test_legal_offline_http import runtime, paused, kill_owned_provider
from test_real_queue_http_e2e import _create, _enqueue, _received, _spawn, _wait_for


def test_browser_illegal_dot_recovers_on_successor_idle_without_reload(runtime, tmp_path):
    session = _create(runtime, "real-fifo-browser")
    sid = session["id"]
    _enqueue(runtime, sid, "hold-A", "active")
    active = _wait_for(lambda: _received(runtime, cwd=session["workdir"], text="hold-A"))[0]
    paused(runtime, sid, True)
    _enqueue(runtime, sid, "B", "queued")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            frames = []
            page.on("websocket", lambda socket: socket.on("framereceived", lambda frame: frames.append(frame)))
            page.goto(runtime.base_url + "/react/", wait_until="networkidle")
            card = page.locator(f'[data-session-card-id="{sid}"]')
            expect(card).to_be_visible()
            card.click()
            illegal = card.get_by_label("Illegal offline: previous execution did not finalize safely")
            expect(illegal).to_have_count(0)
            kill_owned_provider(runtime, active["pid"], session["workdir"])
            try:
                expect(illegal).to_be_visible(timeout=10000)
            except AssertionError:
                print("BROWSER_DIAG_API", runtime.request("GET", f"/api/sessions/{sid}"))
                print("BROWSER_DIAG_DOT", card.inner_html()[:2500])
                print("BROWSER_DIAG_WS", json.dumps([json.loads(frame) for frame in frames
                      if 'queue.snapshot' in frame or 'worker.crashed' in frame], ensure_ascii=False))
                raise
            expect(page.get_by_test_id("topbar").get_by_label("Illegal offline: previous execution did not finalize safely")).to_be_visible()
            card.screenshot(path=str(tmp_path / "illegal.png"))
            _spawn(runtime, sid)
            expect(illegal).to_have_count(0, timeout=10000)
            expect(card.locator('span[title="idle"].bg-success')).to_be_visible()
            card.screenshot(path=str(tmp_path / "recovered.png"))
            assert len(_received(runtime, cwd=session["workdir"], text="B")) == 0
            print("LEGAL_BROWSER", runtime.base_url, sid, str(tmp_path), "Chromium", browser.version)
        finally:
            browser.close()


def test_browser_illegal_lock_icon_and_manual_unlock(runtime, tmp_path):
    session = _create(runtime, "illegal-icon-browser")
    sid = session["id"]
    paused(runtime, sid, True)
    _enqueue(runtime, sid, "locked-icon-preview", "pending")
    runtime.request("POST", f"/api/sessions/{sid}/legality/mark-illegal")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            page.goto(runtime.base_url + "/react/", wait_until="networkidle")
            page.locator(f'[data-session-card-id="{sid}"]').click()
            page.locator("#send-queue-button").click()
            lock = page.get_by_role("button", name="Unlock queued message 1", exact=True)
            expect(lock).to_be_visible()
            expect(lock).to_have_attribute("title", "This message is locked because the Worker entered Illegal status. You can unlock it manually, or mark the Session as safely offline to unlock it automatically.")
            expect(lock.get_by_text("i", exact=True)).to_be_visible()
            lock.screenshot(path=str(tmp_path / "illegal-lock-icon.png"), scale="css")
            lock.click()
            expect(page.get_by_role("button", name="Lock queued message 1", exact=True)).to_be_visible()
        finally:
            browser.close()
