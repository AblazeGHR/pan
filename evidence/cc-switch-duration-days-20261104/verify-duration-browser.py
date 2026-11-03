"""Owned production-bundle acceptance for the CC Switch Bridge duration format.

Real FastAPI Pan process on a loopback ephemeral port (never 8767/8768), the
production React dist bundle, and an owned synthetic cc-switch CLI that returns
synthetic quota JSON. No installed cc-switch, real CC Switch account, user
profile, shared service or other worktree is used.

The synthetic CLI adds a 30-second cushion to the requested reset offsets so the
floored minute display stays stable while Chromium paints the panel.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import psutil
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
PYTHON = Path(sys.executable)
MINUTE = 60_000
HOUR = 3_600_000
BUFFER = 30_000
LONG_RESET = 130 * HOUR + 41 * MINUTE  # The reported 130h41min case.
DAY_RESET = 3 * HOUR + 20 * MINUTE
HOUR_RESET = 45 * MINUTE
AGE = 3 * HOUR + BUFFER
forbidden_ports = {8767, 8768}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def installed_chromium() -> str | None:
    roots = []
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    roots.append(Path.home() / "AppData" / "Local" / "ms-playwright")
    for root in roots:
        for candidate in sorted(root.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True):
            return str(candidate)
    return None


def wait_for(predicate, *, timeout: float, label: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
            last = value
        except Exception as exc:  # startup polling only
            last = exc
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {label}: {last!r}")


def write_fixture(root: Path) -> Path:
    """Create the owned synthetic CLI and return its .cmd entry point."""
    script = root / "synthetic_cc_switch.py"
    script.write_text(
        "import json, sys, time\n"
        "from datetime import datetime, timezone\n"
        "args = sys.argv[1:]\n"
        "now = int(time.time() * 1000)\n"
        f"buffer = {BUFFER}\n"
        f"long_reset = {LONG_RESET}\n"
        f"day_reset = {DAY_RESET}\n"
        f"hour_reset = {HOUR_RESET}\n"
        f"age = {AGE}\n"
        "\n"
        "def iso(ms):\n"
        "    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace('+00:00', 'Z')\n"
        "\n"
        "if args == ['--version']:\n"
        "    payload = {'contract': 'pan-cc-switch-gui/v1', 'version': 'synthetic-duration-fixture'}\n"
        "elif args[-2:] == ['list', '--json'] or args[2:5] == ['provider', 'list', '--json']:\n"
        "    payload = {'currentProviderId': 'p-a', 'providers': [\n"
        "        {'id': 'p-a', 'name': 'Synthetic Main', 'apiUrl': 'https://api.example.test/v1'}]}\n"
        "elif args == ['auth', 'status', '--json']:\n"
        "    payload = {'default_account_id': 'acc-a', 'accounts': [\n"
        "        {'id': 'acc-a', 'login': 'synthetic@example.test'}]}\n"
        "elif args[2:5] == ['provider', 'quota', 'p-a']:\n"
        "    payload = {'status': 'ok', 'available': True, 'queriedAt': now - age,\n"
        "        'result': {'type': 'subscription', 'quota': {'success': True, 'tiers': [\n"
        "            {'name': 'long', 'utilization': 70, 'resetsAt': iso(now + buffer + long_reset)},\n"
        "            {'name': 'day', 'utilization': 30, 'resetsAt': iso(now + buffer + day_reset)},\n"
        "            {'name': 'hour', 'utilization': 10, 'resetsAt': iso(now + buffer + hour_reset)}]}}}\n"
        "else:\n"
        "    raise SystemExit('unexpected synthetic command: ' + ' '.join(args))\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "print(json.dumps(payload, ensure_ascii=True))\n",
        encoding="utf-8")
    wrapper = root / "synthetic-cc-switch.cmd"
    wrapper.write_text(
        "@echo off\r\n"
        f'"{PYTHON}" "{script}" %*\r\n',
        encoding="ascii")
    return wrapper


def owned_processes(root: Path) -> list[psutil.Process]:
    needle = str(root).lower()
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(process.info["cmdline"] or []).lower()
            if needle in command:
                found.append(process)
        except psutil.Error:
            continue
    return found


def main() -> int:
    assert EVIDENCE.is_dir()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                     cwd=ROOT, text=True).strip()
    source = ROOT / "packages" / "web" / "src" / "views" / "PluginPanelView.tsx"
    dist = ROOT / "packages" / "web" / "dist"
    assert (dist / "index.html").is_file(), "build the production React bundle first"

    port = free_port()
    assert port not in forbidden_ports, f"ephemeral port {port} collides with a service port"
    temp_root = Path(tempfile.mkdtemp(prefix="pan-cc-duration-"))
    data_root = temp_root / "data"
    data_root.mkdir(parents=True)
    (data_root / "config.json").write_text(json.dumps({
        "scheduler": {"enabled": False},
        "ui": {"theme": "dark"},
    }), encoding="utf-8")
    plugin_data = data_root / "plugins" / "cc-switch-bridge"
    plugin_data.mkdir(parents=True)
    executable = write_fixture(temp_root)
    (plugin_data / "settings.json").write_text(json.dumps({"executable": str(executable)}),
                                               encoding="utf-8")

    log_path = temp_root / "pan.log"
    log = log_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PAN_AGENT_SESSION_ID"] = ""
    env.pop("PAN_CC_SWITCH_CLI", None)
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [str(PYTHON), str(ROOT / "tests" / "support" / "isolated_http_server.py"),
         "--port", str(port), "--data-root", str(data_root)],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    owned: list[tuple[int, float]] = []

    def capture_tree():
        try:
            root = psutil.Process(process.pid)
            members = [root, *root.children(recursive=True)]
        except psutil.Error:
            return
        created = root.create_time()
        for member in members:
            try:
                value = (member.pid, member.create_time())
                if member.pid == root.pid:
                    value = (member.pid, created)
                if value not in owned:
                    owned.append(value)
            except psutil.Error:
                continue

    evidence: dict = {
        "task": "CC Switch Bridge duration display: 130h41min -> 5d 10h 41min",
        "baselineSha": sha,
        "branch": branch,
        "verifiedSource": {"path": str(source.relative_to(ROOT)),
                           "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        "runtime": {"kind": "real isolated FastAPI Pan process, production dist bundle",
                    "port": port, "dataRoot": "<owned temporary root>",
                    "syntheticCli": "<owned temporary synthetic-cc-switch.cmd>",
                    "realCcSwitchOrAccountUsed": False},
        "assertions": [], "screenshots": [], "pageErrors": [], "consoleErrors": [],
        "quotaResponses": [],
    }
    browser = None
    failure = None
    try:
        wait_for(lambda: httpx.get(base + "/api/health", timeout=.5, trust_env=False).status_code == 200,
                 timeout=60, label="isolated Pan health")
        capture_tree()
        panels = httpx.get(base + "/api/plugins/panels", timeout=10, trust_env=False).json()["panels"]
        panel = next(item for item in panels if item.get("pluginId") == "cc-switch-bridge")
        assert panel["path"] == "/plugins/cc-switch-bridge/accounts"

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception:
                executable_path = installed_chromium()
                assert executable_path, "no installed production Chromium for this Playwright revision"
                browser = playwright.chromium.launch(headless=True, executable_path=executable_path)
            context = browser.new_context(viewport={"width": 1280, "height": 1000})
            page = context.new_page()
            page.on("pageerror", lambda error: evidence["pageErrors"].append(str(error)))
            page.on("console", lambda message: evidence["consoleErrors"].append(message.text)
                    if message.type == "error" else None)
            quota_responses = []

            def on_response(response):
                if "/actions/quota" not in response.url:
                    return
                try:
                    quota_responses.append({"url": response.url, "status": response.status,
                                            "capturedAt": int(time.time() * 1000),
                                            "body": response.json()})
                except Exception as exc:  # keep the raw text instead of losing evidence
                    quota_responses.append({"url": response.url, "status": response.status,
                                            "capturedAt": int(time.time() * 1000),
                                            "bodyError": str(exc)})
            page.on("response", on_response)

            page.goto(base + "/react/plugins/cc-switch-bridge/accounts")
            heading = page.get_by_role("heading", name="Synthetic Main")
            expect(heading).to_be_visible(timeout=30_000)

            long_text = page.get_by_text("距现在剩余 5d 10h 41min", exact=False)
            expect(long_text).to_be_visible(timeout=20_000)
            expect(page.get_by_text("距现在剩余 3h 20min", exact=False)).to_be_visible()
            expect(page.get_by_text("距现在剩余 45min", exact=False)).to_be_visible()
            expect(page.get_by_text("距现在已过去 3h 0min", exact=False)).to_be_visible()
            assert page.get_by_text(re.compile(r"130h|2460h")).count() == 0
            article = heading.locator("xpath=ancestor::article")
            expect(article).to_be_visible()
            raw = article.get_by_text(re.compile(r"重置于 .+ · 距现在剩余"))
            assert raw.count() == 3, raw.count()

            evidence["userAgent"] = page.evaluate("navigator.userAgent")
            evidence["assertions"] = [
                "synthetic 130h41min reset renders as 5d 10h 41min",
                "synthetic 3h20min reset keeps the hour/minute form",
                "synthetic 45min reset keeps the minutes-only form",
                "query age (3h) keeps the existing hour/minute form",
                "no 130h/2460h legacy duration text remains",
                "raw reset timestamps remain visible next to every countdown",
            ]
            page.screenshot(path=str(EVIDENCE / "after-130h41min.png"), full_page=True)
            article.screenshot(path=str(EVIDENCE / "after-provider-card.png"))

            captured = list(quota_responses)
            assert captured, "the browser never fetched quota"
            tier_remaining = {}
            for entry in captured:
                body = entry["body"]
                if not isinstance(body, dict):
                    continue
                structured = body.get("structuredContent") or body
                tiers = (((structured.get("quota") or {}).get("tiers")) or [])
                for tier in tiers:
                    raw_time = tier.get("resetsAt")
                    if not raw_time:
                        continue
                    from datetime import datetime
                    parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00")).timestamp() * 1000
                    tier_remaining[tier["name"]] = round((parsed - entry["capturedAt"]) / MINUTE)
            evidence["quotaResponses"] = captured
            evidence["tierRemainingMinutes"] = tier_remaining
            assert 130 * 60 + 41 in tier_remaining.values(), tier_remaining
            assert 3 * 60 + 20 in tier_remaining.values(), tier_remaining
            assert 45 in tier_remaining.values(), tier_remaining

            requests_before = len(quota_responses)
            page.wait_for_timeout(61_000)
            expect(page.get_by_text("距现在剩余 5d 10h 40min", exact=False)).to_be_visible(timeout=10_000)
            assert len(quota_responses) == requests_before, "clock ticks must not issue quota requests"
            evidence["liveTick"] = {"afterSeconds": 61, "displayed": "5d 10h 40min",
                                    "quotaRequestsDuringTick": 0}
            page.screenshot(path=str(EVIDENCE / "after-tick-61s.png"), full_page=True)
            evidence["screenshots"] = [
                {"path": "evidence/cc-switch-duration-days-20261104/after-130h41min.png",
                 "bytes": (EVIDENCE / "after-130h41min.png").stat().st_size},
                {"path": "evidence/cc-switch-duration-days-20261104/after-provider-card.png",
                 "bytes": (EVIDENCE / "after-provider-card.png").stat().st_size},
                {"path": "evidence/cc-switch-duration-days-20261104/after-tick-61s.png",
                 "bytes": (EVIDENCE / "after-tick-61s.png").stat().st_size},
            ]
            browser.close()
            browser = None
    except Exception as exc:
        failure = exc
        evidence["failure"] = repr(exc)
        raise
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        try:
            httpx.post(base + "/__test/stop-owned-runtime", timeout=.5, trust_env=False)
        except httpx.HTTPError:
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        capture_tree()
        for pid, created in reversed(owned):
            try:
                member = psutil.Process(pid)
                if abs(member.create_time() - created) <= 0.01 and member.is_running():
                    member.terminate()
            except psutil.Error:
                continue
        time.sleep(.3)
        for pid, created in reversed(owned):
            try:
                member = psutil.Process(pid)
                if abs(member.create_time() - created) <= 0.01 and member.is_running():
                    member.kill()
            except psutil.Error:
                continue
        log.close()
        leftovers = [process.pid for process in owned_processes(temp_root) if process.is_running()]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            released = probe.connect_ex(("127.0.0.1", port)) != 0
        server_log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        server_log = server_log.replace(str(temp_root), "<owned-temp>").replace(str(ROOT), "<repo>")
        (EVIDENCE / "server.log").write_text(server_log, encoding="utf-8")
        evidence["cleanup"] = {"port": port, "portReleased": released,
                               "ownedLeftovers": leftovers, "dataRootRemoved": True}
        shutil.rmtree(temp_root, ignore_errors=True)
        evidence["result"] = "passed" if failure is None else "failed"
        (EVIDENCE / "evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
        assert released, f"owned port {port} is still listening"
        assert not leftovers, f"owned processes survived: {leftovers}"
    print(json.dumps({"result": evidence["result"], "port": port,
                      "screenshots": evidence.get("screenshots", [])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
