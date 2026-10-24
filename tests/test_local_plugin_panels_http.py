"""Owned real HTTP + production Chromium acceptance for local web-app panels.

Uses the built React bundle, a copied example plugin, an isolated port/data root
and a real local build command. No existing Pan service, profile or port
8767/8768 is touched.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_real_queue_http_e2e import (
    PORT, ROOT, _PanRuntime, _port_is_free,
)


EVIDENCE = ROOT / "artifacts" / "local-plugin-panels-20261024"


def _installed_chromium() -> str | None:
    """Use the production Chromium already installed on this machine.

    The pinned Python Playwright may expect a different revision than the
    browser cache; launching an installed full Chromium build is still the
    production engine, not a mocked DOM.
    """
    roots = []
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    roots.append(Path.home() / "AppData" / "Local" / "ms-playwright")
    for root in roots:
        for candidate in sorted(root.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True):
            return str(candidate)
    return None


def _copy_example(target: Path) -> Path:
    shutil.copytree(ROOT / "examples" / "plugin-webapp", target,
                    ignore=shutil.ignore_patterns("dist", "__pycache__"))
    return target


def _open_entry(page, name: str) -> None:
    """Page the view-entry strip until the plugin entry is on screen."""
    for _ in range(10):
        link = page.get_by_role("link", name=name, exact=True).first
        if link.count() and link.is_visible():
            link.click()
            return
        following = page.get_by_role("button", name="下一组界面入口")
        if following.is_disabled():
            break
        following.click()
    raise AssertionError(f"view entry {name!r} was not reachable")


@pytest.mark.timeout(180)
def test_local_panel_build_enable_serve_disable_unbuild_and_failure(tmp_path):
    assert PORT not in {8767, 8768}, "run via scripts/run_tests_isolated.py"
    data = tmp_path / "owned-local-panel"
    data.mkdir()
    plugin_dir = _copy_example(tmp_path / "local-console")
    manifest = plugin_dir / "manifest.json"
    (data / "config.json").write_text(json.dumps({
        "plugin_manifests": [str(manifest)],
        "scheduler": {"enabled": False},
    }), encoding="utf-8")

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  text=True, encoding="utf-8").strip()
    runtime = _PanRuntime(data)
    evidence = {"sourceSha": sha, "port": PORT, "profile": "<owned temporary data root>",
                "pluginDir": "<owned temporary example copy>", "browser": {}, "http": {}}
    page_errors: list[str] = []
    try:
        runtime.start()
        before_mcp = runtime.request("GET", "/api/mcp/servers")
        before_sources = runtime.request("GET", "/api/plugins/message-sources")
        assert all(server["id"] != "local-console" for server in before_mcp["servers"])
        assert runtime.request("GET", "/api/plugin-panels")["panels"][0]["status"] == "unbuilt"
        assert runtime.request("GET", "/api/plugins/panels")["panels"] == []

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception:
                executable = _installed_chromium()
                assert executable, "no production Chromium is installed for this Playwright revision"
                browser = playwright.chromium.launch(headless=True, executable_path=executable)
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(runtime.base_url + "/react/")
            page.get_by_title("App settings", exact=True).click()
            dialog = page.get_by_role("dialog", name="App Settings")
            dialog.get_by_role("tab", name="Plugins", exact=True).click()
            region = dialog.get_by_role("region", name="Local plugin panels")
            expect(region.get_by_text("Local Console", exact=True)).to_be_visible()
            expect(region.get_by_text("Not built", exact=True)).to_be_visible()
            expect(region.get_by_role("button", name="Enable", exact=True)).to_be_disabled()
            page.screenshot(path=str(EVIDENCE / "01-unbuilt.png"), full_page=True)

            region.get_by_role("button", name="Build", exact=True).click()
            expect(region.get_by_text("Built", exact=True)).to_be_visible(timeout=30_000)
            expect(region.get_by_text(re.compile(r"built dist"))).to_be_visible()
            expect(region.get_by_role("button", name="Enable", exact=True)).to_be_enabled()
            page.screenshot(path=str(EVIDENCE / "02-built.png"), full_page=True)

            region.get_by_role("button", name="Enable", exact=True).click()
            expect(region.get_by_text("Enabled", exact=True)).to_be_visible(timeout=15_000)
            dialog.get_by_role("button", name="Close", exact=True).click()
            _open_entry(page, "Local Console")
            frame = page.frame_locator('iframe[title="Local Console"]')
            expect(frame.get_by_role("heading", name="Local Console")).to_be_visible(timeout=15_000)
            frame.get_by_role("button", name="Call /api/health").click()
            expect(frame.get_by_text(re.compile(r"ok · Pan "))).to_be_visible(timeout=10_000)
            page.screenshot(path=str(EVIDENCE / "03-panel.png"), full_page=True)

            # A failing rebuild must keep the enabled build serving.
            (plugin_dir / "build.mjs").write_text(
                "console.error('intentional build failure');\nprocess.exit(3);\n", encoding="utf-8")
            page.get_by_title("App settings", exact=True).click()
            dialog = page.get_by_role("dialog", name="App Settings")
            dialog.get_by_role("tab", name="Plugins", exact=True).click()
            region = dialog.get_by_role("region", name="Local plugin panels")
            region.get_by_role("button", name="Rebuild", exact=True).click()
            expect(region.get_by_text("Build failed · previous build kept", exact=True)).to_be_visible(timeout=30_000)
            expect(region.get_by_text(re.compile(r"intentional build failure"))).to_be_visible()
            page.screenshot(path=str(EVIDENCE / "04-failed-retained.png"), full_page=True)
            retained = page.request.get(runtime.base_url + "/panel-assets/local-console/console/")
            assert retained.status == 200
            assert b"Local Console" in retained.body()

            # The retained build can still be opened after the failure.
            dialog.get_by_role("button", name="Close", exact=True).click()
            _open_entry(page, "Local Console")
            expect(page.frame_locator('iframe[title="Local Console"]')
                   .get_by_role("heading", name="Local Console")).to_be_visible(timeout=15_000)

            page.get_by_title("App settings", exact=True).click()
            dialog = page.get_by_role("dialog", name="App Settings")
            dialog.get_by_role("tab", name="Plugins", exact=True).click()
            region = dialog.get_by_role("region", name="Local plugin panels")
            region.get_by_role("button", name="Disable", exact=True).click()
            expect(region.get_by_text("Build failed · previous build kept", exact=True)).to_be_visible(timeout=15_000)
            expect(region.get_by_role("button", name="Disable", exact=True)).to_be_disabled()

            region.get_by_role("button", name="Unbuild", exact=True).click()
            expect(region.get_by_text("Not built", exact=True)).to_be_visible(timeout=15_000)
            expect(region.get_by_role("button", name="Enable", exact=True)).to_be_disabled()
            page.screenshot(path=str(EVIDENCE / "05-unbuilt.png"), full_page=True)
            dialog.get_by_role("button", name="Close", exact=True).click()
            page.reload()
            expect(page.get_by_role("link", name="Local Console", exact=True)).to_have_count(0)
            removed = page.request.get(runtime.base_url + "/panel-assets/local-console/console/")
            assert removed.status == 404
            assert not page_errors, page_errors

            evidence["browser"] = {"build": True, "enable": True, "iframeApi": True,
                                   "failedRebuildRetained": True, "disable": True,
                                   "unbuild": True, "pageErrors": page_errors}
            browser.close()

        after_mcp = runtime.request("GET", "/api/mcp/servers")
        after_sources = runtime.request("GET", "/api/plugins/message-sources")
        assert after_mcp == before_mcp, "panel lifecycle changed Session MCP catalog"
        assert after_sources == before_sources, "panel lifecycle changed plugin message sources"
        assert runtime.request("GET", "/api/plugins/panels")["panels"] == []
        assert runtime.request("GET", "/api/plugin-panels")["panels"][0]["status"] == "unbuilt"
        evidence["http"] = {"mcpCatalogUnchanged": True, "messageSourcesUnchanged": True,
                            "retainedBuildStatus": 200, "unbuiltAssetStatus": 404,
                            "sourceFilesPreserved": (plugin_dir / "build.mjs").is_file()
                            and (plugin_dir / "src" / "app.js").is_file()}
        assert evidence["http"]["sourceFilesPreserved"]
        (EVIDENCE / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    finally:
        runtime.stop()
        evidence["portReleased"] = _port_is_free(PORT)
        assert evidence["portReleased"]
        log = (data / "pan.log").read_text(encoding="utf-8", errors="replace")
        for path, replacement in ((str(data), "<owned-data>"), (str(plugin_dir), "<owned-plugin>"),
                                  (str(ROOT), "<repo>")):
            log = log.replace(path, replacement)
        (EVIDENCE / "server.log").write_text(log, encoding="utf-8")
