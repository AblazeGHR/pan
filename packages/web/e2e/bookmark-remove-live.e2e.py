"""Real Chromium E2E for live bookmark removal.

Layer contract
--------------
* Real FastAPI routes, real uvicorn server, real Session and bookmark sidecars
  on a disposable runtime root, real `/ws` fan-out, real Chromium.
* Only response TIMING and one HTTP failure are injected, by the test-only
  launcher's ASGI middleware.  The app computes the old bookmark envelope at
  request time and holds it; the failure is a real HTTP 500 before the store
  is touched.  Both therefore exercise the production client paths.
* Never 8767/8768, never the practical data root.

Scenarios
---------
1. Desktop: delete -> body tag, row flag and rail marker disappear in the same
   mount (sentinel survives; no Session switch, no reload); persistence read
   confirms the sidecar changed.
2. Dual client + delayed old list + stale WS: client A's cold bookmark GET is
   held (it already holds the removed id); client B deletes; A hides from the
   real WS/reconcile before the old snapshot lands; the old snapshot and an
   older-revision broadcast must not resurrect it.
3. Failure: a real HTTP 500 delete restores the optimistic state and shows a
   toast; a retry then succeeds and converges on both clients.
4. 390px mobile: the same live removal for the body tag and the drawer rail.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
PYTHON = os.environ.get("PAN_E2E_PYTHON", "E:/software/miniforge/python.exe")
# Reuse the Chromium already installed for the repository's Playwright tree;
# this Python Playwright build ships an older default revision, so an explicit
# executable path avoids downloading/installing a second browser.
CHROMIUM = os.environ.get(
    "PAN_E2E_CHROMIUM",
    str(Path(os.environ.get("LOCALAPPDATA", ""))
        / "ms-playwright/chromium-1243/chrome-win64/chrome.exe"),
)
EVIDENCE = Path(os.environ.get(
    "PAN_E2E_EVIDENCE",
    str(Path(tempfile.gettempdir()) / f"bookmark-remove-live-e2e-{int(time.time())}"),
)).resolve()
EVIDENCE.mkdir(parents=True, exist_ok=True)

CHECKS: list[dict] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    CHECKS.append({"name": name, "ok": bool(condition), "detail": detail})
    if condition:
        print(f"[e2e] ok: {name}")
        return
    raise AssertionError(f"{name} :: {detail}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def http_json(url: str, payload: dict | None = None, method: str | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data is not None else "GET"),
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_until(predicate, timeout: float, interval: float = 0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def control(base: str, payload: dict) -> dict:
    return http_json(f"{base}/__e2e/control", payload)


def state(base: str) -> dict:
    return control(base, {"action": "state"})


def start_server(port: int) -> tuple[subprocess.Popen, Path]:
    runtime = EVIDENCE / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    log = open(EVIDENCE / "server.log", "w", encoding="utf-8")
    env = os.environ.copy()
    for name in ("PAN_PORT", "PAN_ROOT", "PAN_API_URL", "PAN_AGENT_SESSION_ID"):
        env.pop(name, None)
    env.update({
        "PAN_E2E_RUNTIME": str(runtime),
        "PAN_E2E_PORT": str(port),
        "PYTHONPATH": str(ROOT),
    })
    process = subprocess.Popen(
        [PYTHON, str(ROOT / "packages/web/e2e/bookmark-remove-live.server.py")],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    return process, runtime


def wait_health(base: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_json(f"{base}/api/health")
            return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"isolated Pan server did not become healthy at {base}")


def row_for(page: Page, message_id: str):
    gutter = page.locator(f'[data-message-bookmark-gutter="{message_id}"]')
    return gutter.locator(
        'xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " message-row ")][1]'
    )


def tag_for(page: Page, message_id: str):
    return row_for(page, message_id).locator('[data-message-bookmark-tag]')


def marker_for(page: Page, message_id: str):
    return page.locator(f'.message-navigation-marker[data-message-id="{message_id}"]')


def wait_tag(page: Page, message_id: str, present: bool, timeout: int = 8_000) -> None:
    tag_for(page, message_id).wait_for(
        state="visible" if present else "detached", timeout=timeout,
    )


def wait_marker(page: Page, message_id: str, present: bool, timeout: int = 8_000) -> None:
    marker_for(page, message_id).wait_for(
        state="visible" if present else "detached", timeout=timeout,
    )


def open_rail(page: Page) -> None:
    handle = page.locator('.message-navigation-dock__handle')
    if handle.count() and handle.first.is_visible():
        handle.first.click()
    page.wait_for_selector('.message-navigation-marker', state="visible", timeout=15_000)


def remove_via_menu(page: Page, message_id: str) -> None:
    row_for(page, message_id).locator(
        f'[data-message-bookmark-gutter="{message_id}"]').click()
    page.get_by_role("menuitem", name="Remove bookmark").click()


def instrument(page: Page, label: str) -> None:
    def on_request(request):
        if "/bookmarks" in request.url:
            print(f"[e2e][{label}] request {request.method} {request.url}", flush=True)

    def on_finished(request):
        if "/bookmarks" in request.url:
            response = request.response()
            print(f"[e2e][{label}] done {request.method} {response.status if response else '?'}",
                  flush=True)

    def on_ws(ws):
        print(f"[e2e][{label}] ws {ws.url}", flush=True)

        def on_frame(payload):
            text = payload if isinstance(payload, str) else payload.decode("utf-8", "ignore")
            if "bookmarks.changed" in text:
                print(f"[e2e][{label}] ws frame {text}", flush=True)

        ws.on("framereceived", on_frame)

    page.on("request", on_request)
    page.on("requestfinished", on_finished)
    page.on("websocket", on_ws)


def open_session(browser: Browser, base: str, session_id: str, *, mobile: bool = False, label: str = ""):
    if mobile:
        context = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True,
            has_touch=True, device_scale_factor=2,
        )
    else:
        context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.set_default_timeout(15_000)
    instrument(page, label or "page")
    page.goto(f"{base}/react/?session={session_id}", wait_until="domcontentloaded")
    page.wait_for_selector('[data-message-bookmark-gutter]', timeout=25_000)
    return context, page


def add_bookmark(base: str, session_id: str, message_id: str) -> dict:
    return http_json(
        f"{base}/api/sessions/{session_id}/bookmarks/{message_id}",
        {}, method="PUT",
    )


def bookmarks(base: str, session_id: str) -> dict:
    return http_json(f"{base}/api/sessions/{session_id}/bookmarks")


def run() -> None:
    port = free_port()
    assert port not in (8767, 8768), f"owned port must not be a Pan port (got {port})"
    base = f"http://127.0.0.1:{port}"
    server, _runtime = start_server(port)
    summary: dict = {"port": port, "base": base, "evidence": str(EVIDENCE)}
    try:
        wait_health(base)
        fixtures = json.loads((EVIDENCE / "runtime" / "fixtures.json").read_text(encoding="utf-8"))
        session_id = fixtures["sessionId"]
        target = fixtures["targetId"]
        keep = fixtures["keepId"]
        summary.update(fixtures)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=CHROMIUM)
            try:
                # ── 1. Desktop: same-mount removal, no switch/refresh ──────
                context, page = open_session(browser, base, session_id, label="desktop")
                page.evaluate("window.__bookmarkSentinel = 'same-mount'")
                wait_tag(page, target, True)
                wait_tag(page, keep, True)
                open_rail(page)
                wait_marker(page, target, True)
                wait_marker(page, keep, True)
                check(
                    "desktop starts with two bookmarked body rows and two rail markers",
                    page.locator('[data-message-bookmark-tag]').count() == 2
                    and page.locator('.message-navigation-marker[data-kind="bookmark"]').count() == 2,
                )
                page.screenshot(path=str(EVIDENCE / "desktop-before-remove.png"), full_page=True)

                with page.expect_response(
                    lambda response: response.request.method == "DELETE"
                    and "/bookmarks/" in response.url,
                    timeout=8_000,
                ) as desktop_delete:
                    remove_via_menu(page, target)
                check("desktop delete reached the real HTTP endpoint",
                      desktop_delete.value.status == 200, desktop_delete.value.status)
                wait_tag(page, target, False, timeout=5_000)
                wait_marker(page, target, False, timeout=5_000)
                check(
                    "desktop removal clears the body tag and the rail marker immediately",
                    row_for(page, target).get_attribute("data-bookmarked") == "false"
                    and tag_for(page, keep).count() == 1
                    and marker_for(page, keep).count() == 1,
                )
                check(
                    "desktop removal did not reload or switch Session",
                    page.evaluate("window.__bookmarkSentinel") == "same-mount"
                    and page.url.startswith(f"{base}/react")
                    and tag_for(page, keep).count() == 1,
                    page.url,
                )
                check(
                    "desktop removal persisted exactly the target",
                    target not in bookmarks(base, session_id)["bookmarks"]
                    and keep in bookmarks(base, session_id)["bookmarks"],
                )
                page.screenshot(path=str(EVIDENCE / "desktop-after-remove.png"), full_page=True)
                context.close()

                # ── 2. Dual client + delayed old list + stale WS ───────────
                add_bookmark(base, session_id, target)
                control(base, {"action": "delay_next_bookmarks_get", "ms": 2500})
                context_a, page_a = open_session(browser, base, session_id, label="client-a")
                wait_tag(page_a, target, True)
                page_a.evaluate("window.__bookmarkSentinel = 'client-a'")
                held = wait_until(lambda: state(base)["heldGet"], 5.0)
                check("client A's cold bookmark GET is parked with the old envelope", bool(held))

                context_b, page_b = open_session(browser, base, session_id, label="client-b")
                wait_tag(page_b, target, True)
                with page_b.expect_response(
                    lambda response: response.request.method == "DELETE"
                    and "/bookmarks/" in response.url,
                    timeout=8_000,
                ) as delete_info:
                    remove_via_menu(page_b, target)
                check("client B's delete reached the real HTTP endpoint",
                      delete_info.value.status == 200, delete_info.value.status)
                wait_tag(page_b, target, False, timeout=2_000)

                # Client A's cold load was issued before the delete and its
                # OLD complete snapshot is still parked.  A converges from the
                # real WS delta plus the coalesced reconcile (no Session
                # switch, no reload); the held snapshot must not leave it
                # resurrected once it finally lands.
                wait_tag(page_a, target, False, timeout=6_000)
                released = wait_until(lambda: not state(base)["heldGet"], 8.0)
                check("the old bookmark list response was released", bool(released))
                page_a.wait_for_timeout(500)
                check(
                    "the delayed old list does not resurrect client A's bookmark",
                    tag_for(page_a, target).count() == 0
                    and row_for(page_a, target).get_attribute("data-bookmarked") == "false"
                    and page_a.evaluate("window.__bookmarkSentinel") == "client-a",
                )

                control(base, {
                    "action": "broadcast_stale_add", "sessionId": session_id,
                    "messageIds": [target], "revision": 1,
                })
                page_a.wait_for_timeout(400)
                check(
                    "an older-revision WS add cannot resurrect the bookmark",
                    tag_for(page_a, target).count() == 0
                    and tag_for(page_b, target).count() == 0,
                )
                page_a.screenshot(path=str(EVIDENCE / "dual-client-after-stale.png"), full_page=True)

                # ── 3. Real HTTP failure: toast, rollback, retry ───────────
                add_bookmark(base, session_id, target)
                wait_tag(page_b, target, True)
                wait_tag(page_a, target, True)
                control(base, {"action": "fail_next_bookmark_delete"})
                with page_b.expect_response(
                    lambda response: response.request.method == "DELETE"
                    and "/bookmarks/" in response.url,
                    timeout=8_000,
                ) as failed_delete:
                    remove_via_menu(page_b, target)
                check("the injected failure returned a real HTTP 500",
                      failed_delete.value.status == 500, failed_delete.value.status)
                toast = page_b.locator('[role="alert"]')
                toast.first.wait_for(state="visible", timeout=5_000)
                check(
                    "a failed delete surfaces the real error in a toast",
                    "书签删除失败（E2E 注入）" in (toast.first.text_content() or ""),
                    toast.first.text_content(),
                )
                wait_tag(page_b, target, True, timeout=5_000)
                check(
                    "the failed delete restores the optimistic row and keeps the sidecar",
                    row_for(page_b, target).get_attribute("data-bookmarked") == "true"
                    and target in bookmarks(base, session_id)["bookmarks"],
                )
                page_b.screenshot(path=str(EVIDENCE / "desktop-failed-delete.png"), full_page=True)

                with page_b.expect_response(
                    lambda response: response.request.method == "DELETE"
                    and "/bookmarks/" in response.url,
                    timeout=8_000,
                ) as retry_delete:
                    remove_via_menu(page_b, target)
                check("the retry returned HTTP 200",
                      retry_delete.value.status == 200, retry_delete.value.status)
                wait_tag(page_b, target, False, timeout=5_000)
                wait_tag(page_a, target, False, timeout=5_000)
                check(
                    "retry after the failure succeeds and converges both clients",
                    target not in bookmarks(base, session_id)["bookmarks"]
                    and tag_for(page_a, target).count() == 0,
                )
                context_a.close()
                context_b.close()

                # ── 4. 390px mobile: live removal without reload ──────────
                add_bookmark(base, session_id, target)
                context_m, page_m = open_session(browser, base, session_id, mobile=True, label="mobile")
                wait_tag(page_m, target, True)
                page_m.evaluate("window.__bookmarkSentinel = 'mobile'")
                page_m.get_by_test_id("mobile-message-navigation-toggle").click()
                wait_marker(page_m, target, True)
                page_m.get_by_test_id("mobile-message-navigation-toggle").click()
                with page_m.expect_response(
                    lambda response: response.request.method == "DELETE"
                    and "/bookmarks/" in response.url,
                    timeout=8_000,
                ) as mobile_delete:
                    remove_via_menu(page_m, target)
                check("mobile delete reached the real HTTP endpoint",
                      mobile_delete.value.status == 200, mobile_delete.value.status)
                wait_tag(page_m, target, False, timeout=5_000)
                page_m.get_by_test_id("mobile-message-navigation-toggle").click()
                wait_marker(page_m, target, False, timeout=5_000)
                check(
                    "390px mobile removes the body tag and drawer rail marker live",
                    page_m.evaluate("window.__bookmarkSentinel") == "mobile"
                    and target not in bookmarks(base, session_id)["bookmarks"]
                    and row_for(page_m, target).get_attribute("data-bookmarked") == "false",
                )
                page_m.screenshot(path=str(EVIDENCE / "mobile-after-remove.png"), full_page=True)
                context_m.close()
            finally:
                browser.close()
        summary["checks"] = CHECKS
        (EVIDENCE / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"[e2e] PASS ({len(CHECKS)} checks) evidence={EVIDENCE}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        (EVIDENCE / "summary.json").write_text(
            json.dumps({"checks": CHECKS, "failed": True}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise
