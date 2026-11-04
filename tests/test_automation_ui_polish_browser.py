"""Owned production-bundle Automation UI: real HTTP auth, canonical lists/forms, phone/desktop.

The fixture is a synthetic profile on a dynamic non-8767/8768 port. It never reads a
user profile, provider account or practical data root. No request is mocked: the
browser exercises the production React bundle against the canonical HTTP service.
"""
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from packages.jobs.final_service import object_ref
from packages.jobs.window import command_window_options
from packages.scheduler.providers import time_definition
from tests.test_job_final_creators import config, interval

WIDTHS = (1440, 390, 320)
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "automation-ui-polish-e2e"


def _ready(child, client):
    for _ in range(150):
        assert child.poll() is None, "owned server exited"
        try:
            if client.get("/test/ready").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise AssertionError("owned server did not become ready")


def _start(root):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    assert port not in (8767, 8768)
    root.mkdir(parents=True, exist_ok=True)
    log = (OUT / "server.log").open("wb")
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.support.scheduler_management_server",
         "--root", str(root), "--port", str(port), "--seed-time-template-target"],
        cwd=Path(__file__).resolve().parents[1],
        stdout=log, stderr=subprocess.STDOUT, **command_window_options(),
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=15)
    try:
        _ready(child, client)
        key = (root / "automation/user.key").read_text(encoding="utf8").strip()
        token = client.post("/api/automation/login", json={"key": key}).json()["token"]
        client.headers["Authorization"] = "Bearer " + token
        return child, log, client, f"http://127.0.0.1:{port}", key
    except BaseException:
        child.terminate()
        child.wait(timeout=15)
        client.close()
        log.close()
        raise


def _seed(client):
    """Synthetic but real canonical objects: draft/enabled Jobs, Scheduler, Trigger, Run."""
    actions = client.get("/api/jobs/actions").json()["items"]
    message = next(item for item in actions if item["id"] == "product_session_message")
    ref = object_ref("action", message)

    def job(name, text):
        result = client.post("/api/jobs", json={
            "definition": {
                "name": name,
                "steps": [{"stepId": "step0", "actionRef": ref,
                           "arguments": {"sessionId": {"from": "literal", "value": "owned-session"},
                                         "text": {"from": "literal", "value": text}},
                           "completion": {"contract": "durable-queue-accepted"}}],
                "retention": {"policyRef": "test"},
            },
            "expectedRevision": 0, "requestKey": "seed-" + uuid.uuid4().hex,
        })
        assert result.status_code == 201, result.text
        return result.json()["result"]["object"]

    def control(kind, item, operation):
        result = client.post(f"/api/{kind}/{item['id']}/operations", json={
            "operation": operation,
            "expectedRevision": item.get("configRevision", item.get("revision")),
            "expectedStateRevision": item["stateRevision"],
            "requestKey": uuid.uuid4().hex, "parameters": {},
        })
        assert result.status_code == 200, result.text
        return result.json()["result"]["object"]

    draft = job("Draft migration rehearsal", "draft")
    enabled = control("jobs", job("Nightly session digest", "digest"), "enable")
    control("jobs", job("Weekly summary", "summary"), "enable")

    provider = client.get("/api/event-creators/providers").json()["items"][0]
    creator_definition = time_definition("Weekday morning tick", config([interval()]))
    creator_definition = {
        **{key: creator_definition[key] for key in (
            "providerRef", "configSchemaRef", "config", "eventTypes", "requiredCapabilities")},
        "name": "Weekday morning tick",
        "description": "Synthetic time source for the owned fixture",
    }
    assert provider["providerRef"]["id"] == "scheduler"
    result = client.post("/api/event-creators", json={
        "definition": creator_definition, "expectedRevision": 0,
        "requestKey": "seed-" + uuid.uuid4().hex,
    })
    assert result.status_code == 201, result.text
    creator = control("event-creators", result.json()["result"]["object"], "enable")

    result = client.post("/api/triggers", json={
        "definition": {"name": "Morning digest subscription",
                       "eventTypes": creator["eventTypes"],
                       "creatorSelector": {"mode": "ids", "ids": [creator["id"]]},
                       "targetJobId": enabled["id"]},
        "expectedRevision": 0, "requestKey": "seed-" + uuid.uuid4().hex,
    })
    assert result.status_code == 201, result.text
    control("triggers", result.json()["result"]["object"], "enable")

    result = client.post(f"/api/jobs/{enabled['id']}/runs", json={
        "expectedRevision": enabled["configRevision"],
        "requestKey": "seed-" + uuid.uuid4().hex,
        "input": {}, "cause": {"type": "manual"},
    })
    assert result.status_code == 202, result.text
    assert draft["name"] == "Draft migration rehearsal"


def _no_overflow(page, aria):
    found = page.evaluate("""(aria) => {
      const root = document.querySelector(`section[aria-label="${aria}"]`);
      if (!root) return [{error: 'missing root'}];
      return [root, ...root.querySelectorAll('*')].flatMap((el) => {
        const overflow = getComputedStyle(el).overflowX;
        if (overflow === 'auto' || overflow === 'scroll') return [];
        return el.scrollWidth > el.clientWidth + 1
          ? [{tag: el.tagName, cls: String(el.className).slice(0, 70),
              width: el.clientWidth, scroll: el.scrollWidth}]
          : [];
      }).slice(0, 8);
    }""", aria)
    assert found == [], f"{aria} horizontal overflow: {found}"


def _touch_targets(page, aria, minimum):
    small = page.evaluate("""([aria, minimum]) => {
      const root = document.querySelector(`section[aria-label="${aria}"]`);
      return [...root.querySelectorAll('button')]
        .filter((el) => el.offsetParent !== null)
        .map((el) => ({text: el.textContent.trim().slice(0, 50),
                       height: Math.round(el.getBoundingClientRect().height)}))
        .filter((item) => item.height < minimum);
    }""", [aria, minimum])
    assert small == [], f"{aria} controls below {minimum}px: {small}"


def _login(page, key):
    page.get_by_label("Local user key", exact=True).fill(key)
    page.get_by_role("button", name="Log in", exact=True).click()


def test_owned_automation_ui_real_http_desktop_mobile_states(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    dist = Path(__file__).resolve().parents[1] / "packages/web/dist/index.html"
    assert dist.is_file(), "build the React bundle before this owned browser test"
    OUT.mkdir(parents=True, exist_ok=True)

    child, log, client, url, key = _start(tmp_path / "owned-profile")
    stopped = False
    try:
        _seed(client)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                # Unauthorized state: real 401 request path, no login UI mutation.
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url + "/react/automation/canonical")
                expect(page.get_by_text("请登录以加载 canonical 数据。", exact=True)).to_be_visible()
                expect(page.get_by_label("Local user key", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="创建 Jobs", exact=True)).to_have_count(0)
                page.screenshot(path=str(OUT / "unauthorized-canonical-1440.png"), full_page=True)
                assert errors == []
                page.close()

                for width in WIDTHS:
                    page = browser.new_page(viewport={"width": width, "height": 1000})
                    errors, dialogs = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
                    page.goto(url + "/react/automation")
                    _login(page, key)
                    expect(page.get_by_role("heading", name="Automation", exact=True)).to_be_visible()
                    expect(page.get_by_role("button", name="编辑 Nightly session digest", exact=True)).to_be_visible()
                    expect(page.get_by_role("article", name="Jobs Nightly session digest", exact=True)).to_be_visible()
                    page.wait_for_timeout(200)
                    _no_overflow(page, "Automation management")
                    _touch_targets(page, "Automation management", 44 if width < 640 else 36)
                    page.screenshot(path=str(OUT / f"automation-jobs-{width}.png"), full_page=True)

                    if width == 1440:
                        # Keyboard tabs: arrows move selection and content together.
                        page.get_by_role("tab", name="Job", exact=True).focus()
                        page.keyboard.press("ArrowRight")
                        expect(page.get_by_role("tab", name="Trigger", exact=True)).to_have_attribute(
                            "aria-selected", "true")
                        expect(page.get_by_role("heading", name="Canonical Triggers", exact=True)).to_be_visible()

                        # Trigger detail form opens with the canonical target selector.
                        page.get_by_role("button", name="编辑 Morning digest subscription", exact=True).click()
                        expect(page.get_by_role("form", name="编辑 Triggers", exact=True)).to_be_visible()
                        expect(page.get_by_label("Target canonical Job", exact=True)).to_be_visible()
                        page.get_by_role("button", name="关闭编辑", exact=True).click()
                        expect(page.get_by_role("form", name="编辑 Triggers", exact=True)).to_have_count(0)

                        # Enable review remains explicit and cancellation is reachable.
                        page.get_by_role("tab", name="Job", exact=True).click()
                        page.get_by_role("button", name="Enable Draft migration rehearsal", exact=True).click()
                        expect(page.get_by_role("region", name="Review before enabling", exact=True)).to_be_visible()
                        confirm = page.get_by_role("button", name="Confirm enable", exact=True)
                        expect(confirm).to_be_disabled()
                        page.get_by_label(
                            "I reviewed this definition, Actions, parameters and source references."
                        ).check()
                        expect(confirm).to_be_enabled()
                        page.get_by_role("button", name="Cancel enable review", exact=True).click()
                        expect(page.get_by_role("region", name="Review before enabling", exact=True)).to_have_count(0)

                        # Event Register list/detail and retained raw rule detail.
                        page.get_by_role("tab", name="Event Register", exact=True).click()
                        expect(page.get_by_role("article", name="Scheduler Weekday morning tick", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="Morning digest subscription", exact=True)).to_be_visible()
                        page.get_by_role("button", name="Edit Weekday morning tick", exact=True).click()
                        expect(page.get_by_label("Scheduler name", exact=True)).to_have_value("Weekday morning tick")
                        expect(page.get_by_label("Scheduler impact", exact=True).get_by_text(
                            "1 referencing subscribers", exact=True)).to_be_visible()
                        page.screenshot(path=str(OUT / "automation-event-editor-1440.png"), full_page=True)
                        page.get_by_role("button", name="Close Scheduler editor", exact=True).click()
                        page.screenshot(path=str(OUT / "automation-events-1440.png"), full_page=True)

                        # Canonical page: kind switching, list/detail, empty state, Runs panel.
                        page.get_by_role("link", name="Canonical Jobs", exact=True).click()
                        expect(page.get_by_role("heading", name="Canonical Jobs", exact=True, level=1)).to_be_visible()
                        expect(page.get_by_role("article", name="Jobs Nightly session digest", exact=True)).to_be_visible()
                        _no_overflow(page, "Canonical management")
                        _touch_targets(page, "Canonical management", 36)
                        page.screenshot(path=str(OUT / "canonical-jobs-1440.png"), full_page=True)

                        for label, first in (("Triggers", "Triggers Morning digest subscription"),
                                             ("EventCreators", "EventCreators Weekday morning tick")):
                            page.get_by_role("button", name=label, exact=True).click()
                            expect(page.get_by_role("article", name=first, exact=True)).to_be_visible()
                        page.get_by_role("button", name="JobTemplates", exact=True).click()
                        expect(page.get_by_text("暂无 canonical JobTemplates。", exact=True)).to_be_visible()
                        page.screenshot(path=str(OUT / "canonical-empty-templates-1440.png"), full_page=True)

                        page.get_by_role("button", name="Jobs", exact=True).click()
                        page.get_by_role("button", name="编辑 Nightly session digest", exact=True).click()
                        expect(page.get_by_role("form", name="编辑 Jobs", exact=True)).to_be_visible()
                        page.get_by_text("Advanced definition JSON", exact=True).click()
                        expect(page.locator("textarea[spellcheck=false]")).to_be_visible()
                        page.screenshot(path=str(OUT / "canonical-editor-1440.png"), full_page=True)
                        page.get_by_role("button", name="关闭编辑", exact=True).click()
                        expect(page.get_by_role("form", name="编辑 Jobs", exact=True)).to_have_count(0)
                        assert dialogs and all(
                            message == "Discard this definition draft?" for message in dialogs
                        )

                        page.get_by_role("button", name="Runs Nightly session digest", exact=True).click()
                        expect(page.get_by_role("heading", name="Runs · Nightly session digest", exact=True)).to_be_visible()
                        expect(page.get_by_text("不可确认的结果必须按原身份查询，不能自动重试。", exact=True)).to_be_visible()
                        page.get_by_role("button", name="clone-and-run", exact=True).click()
                        expect(page.get_by_role("form", name="Run operation", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="确认操作", exact=True)).to_be_visible()
                        page.get_by_role("button", name="返回", exact=True).click()
                        expect(page.get_by_role("form", name="Run operation", exact=True)).to_have_count(0)
                    else:
                        page.get_by_role("tab", name="Event Register", exact=True).click()
                        expect(page.get_by_role("article", name="Scheduler Weekday morning tick", exact=True)).to_be_visible()
                        _no_overflow(page, "Automation management")
                        page.screenshot(path=str(OUT / f"automation-events-{width}.png"), full_page=True)
                        page.get_by_role("link", name="Canonical Jobs", exact=True).click()
                        expect(page.get_by_role("article", name="Jobs Nightly session digest", exact=True)).to_be_visible()
                        _no_overflow(page, "Canonical management")
                        _touch_targets(page, "Canonical management", 44)
                        page.wait_for_timeout(200)
                        page.screenshot(path=str(OUT / f"canonical-jobs-{width}.png"), full_page=True)
                    assert errors == []
                    page.close()

                # Real transport error after all requests succeeded: stop the owned server,
                # then ask the loaded production page to refresh its canonical list.
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(url + "/react/automation/canonical")
                _login(page, key)
                expect(page.get_by_role("button", name="刷新列表", exact=True)).to_be_visible()
                child.terminate()
                child.wait(timeout=15)
                stopped = True
                page.get_by_role("button", name="刷新列表", exact=True).click()
                expect(page.get_by_role("alert").first).to_be_visible()
                page.screenshot(path=str(OUT / "canonical-error-1440.png"), full_page=True)
                page.close()
            finally:
                browser.close()
    finally:
        client.close()
        if not stopped:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
        log.close()
