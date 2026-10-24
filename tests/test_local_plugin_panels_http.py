"""Owned real HTTP + production Chromium acceptance for local web-app panels.

Uses the built React bundle, a real Vite + React example, an isolated port/data
root and an owned plugin copy. No existing Pan service, profile or port 8767/8768
is touched.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import psutil
import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_real_queue_http_e2e import (
    PORT, ROOT, _PanRuntime, _port_is_free, _wait_for,
)


EVIDENCE = ROOT / "artifacts" / "local-plugin-panels-20261024"
WEB_MODULES = ROOT / "packages" / "web" / "node_modules"


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


def _copy_react_example(target: Path) -> Path:
    """Copy the example and link the checkout's installed packages.

    The junction gives the copied plugin its own node_modules without copying
    or mutating the shared dependency tree; Vite/React are already installed.
    """
    shutil.copytree(ROOT / "examples" / "plugin-react-panel", target,
                    ignore=shutil.ignore_patterns("dist", "node_modules", "__pycache__"))
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(target / "node_modules"), str(WEB_MODULES)],
        capture_output=True)
    assert created.returncode == 0, created.stderr.decode("utf-8", "replace")
    return target


def _remove_react_example(target: Path) -> None:
    junction = target / "node_modules"
    if junction.exists() and os.path.isjunction(junction):
        os.rmdir(junction)  # A junction is a link; rmtree must not follow it.
    shutil.rmtree(target, ignore_errors=True)


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


def _owned_processes(root: Path) -> list[int]:
    needle = str(root).lower()
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(process.info["cmdline"] or []).lower()
        except psutil.Error:
            continue
        if needle in command:
            found.append(process.pid)
    return found


@pytest.mark.timeout(180)
def test_local_panel_build_enable_serves_immutable_react_builds_and_lifecycle(tmp_path):
    assert PORT not in {8767, 8768}, "run via scripts/run_tests_isolated.py"
    data = tmp_path / "owned-local-panel"
    data.mkdir()
    plugin_dir = _copy_react_example(tmp_path / "react-console")
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
        assert all(server["id"] != "react-console" for server in before_mcp["servers"])
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
            expect(region.get_by_text("React Console", exact=True)).to_be_visible()
            expect(region.get_by_text("Not built", exact=True)).to_be_visible()
            expect(region.get_by_role("button", name="Enable", exact=True)).to_be_disabled()
            page.screenshot(path=str(EVIDENCE / "01-unbuilt.png"), full_page=True)

            region.get_by_role("button", name="Build", exact=True).click()
            expect(region.get_by_text("Built", exact=True)).to_be_visible(timeout=30_000)
            expect(region.get_by_text(re.compile(r"react panel built"))).to_be_visible()
            expect(region.get_by_role("button", name="Enable", exact=True)).to_be_enabled()
            page.screenshot(path=str(EVIDENCE / "02-built.png"), full_page=True)

            region.get_by_role("button", name="Enable", exact=True).click()
            expect(region.get_by_text("Enabled", exact=True)).to_be_visible(timeout=15_000)
            dialog.get_by_role("button", name="Close", exact=True).click()
            _open_entry(page, "React Console")
            frame = page.frame_locator('iframe[title="React Console"]')
            expect(frame.get_by_role("heading", name="React Console")).to_be_visible(timeout=15_000)
            frame.get_by_role("button", name="Call /api/health").click()
            expect(frame.get_by_text(re.compile(r"ok · Pan "))).to_be_visible(timeout=10_000)
            build_a = runtime.request("GET", "/api/plugin-panels")["panels"][0]["activeBuild"]
            asset_a = page.locator('iframe[title="React Console"]').get_attribute("src")
            assert build_a in asset_a, (build_a, asset_a)
            page.screenshot(path=str(EVIDENCE / "03-react-panel.png"), full_page=True)

            # Rebuild while the old page is open. Its lazy chunk was never
            # requested, so requesting it after the rebuild exercises A's URL.
            (plugin_dir / "src" / "lazy.jsx").write_text("export default 'Bundle B';\n", encoding="utf-8")
            page.get_by_title("App settings", exact=True).click()
            dialog = page.get_by_role("dialog", name="App Settings")
            dialog.get_by_role("tab", name="Plugins", exact=True).click()
            region = dialog.get_by_role("region", name="Local plugin panels")
            region.get_by_role("button", name="Rebuild", exact=True).click()

            def rebuilt():
                value = runtime.request("GET", "/api/plugin-panels")["panels"][0]
                return value if value["status"] == "built" and value["activeBuild"] != build_a else None

            state = _wait_for(rebuilt, timeout=30, label="rebuild B published")
            build_b = state["activeBuild"]
            expect(region.get_by_text("Enabled", exact=True)).to_be_visible(timeout=30_000)
            expect(region.get_by_text(re.compile(r"react panel built"))).to_be_visible()
            assert build_b != build_a
            dialog.get_by_role("button", name="Close", exact=True).click()
            assert page.locator('iframe[title="React Console"]').get_attribute("src") == asset_a
            frame.get_by_role("button", name="Load lazy chunk").click()
            expect(frame.get_by_text("Bundle A", exact=True)).to_be_visible(timeout=10_000)
            page.screenshot(path=str(EVIDENCE / "04-old-chunk-after-rebuild.png"), full_page=True)

            # A newly opened page resolves the new active build and its chunk.
            page.goto(runtime.base_url + "/react/plugins/react-console/console")
            frame = page.frame_locator('iframe[title="React Console"]')
            expect(frame.get_by_role("heading", name="React Console")).to_be_visible(timeout=15_000)
            asset_b = page.locator('iframe[title="React Console"]').get_attribute("src")
            assert build_b in asset_b and asset_b != asset_a, (asset_a, asset_b)
            frame.get_by_role("button", name="Load lazy chunk").click()
            expect(frame.get_by_text("Bundle B", exact=True)).to_be_visible(timeout=10_000)
            page.screenshot(path=str(EVIDENCE / "05-new-build.png"), full_page=True)

            # A failing rebuild keeps the active B build and its URL.
            (plugin_dir / "build.mjs").write_text(
                "console.error('intentional build failure');\nprocess.exit(3);\n", encoding="utf-8")
            page.get_by_title("App settings", exact=True).click()
            dialog = page.get_by_role("dialog", name="App Settings")
            dialog.get_by_role("tab", name="Plugins", exact=True).click()
            region = dialog.get_by_role("region", name="Local plugin panels")
            region.get_by_role("button", name="Rebuild", exact=True).click()
            expect(region.get_by_text("Build failed · previous build kept", exact=True)).to_be_visible(timeout=30_000)
            expect(region.get_by_text(re.compile(r"intentional build failure"))).to_be_visible()
            page.screenshot(path=str(EVIDENCE / "06-failed-retained.png"), full_page=True)
            retained = page.request.get(runtime.base_url + asset_b)
            assert retained.status == 200 and b"React Console" in retained.body()
            dialog.get_by_role("button", name="Close", exact=True).click()
            assert page.locator('iframe[title="React Console"]').get_attribute("src") == asset_b
            frame.get_by_role("button", name="Load lazy chunk").click()
            expect(frame.get_by_text("Bundle B", exact=True)).to_be_visible(timeout=10_000)

            # Disable removes serving; Unbuild drops only host copies.
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
            page.screenshot(path=str(EVIDENCE / "07-unbuilt.png"), full_page=True)
            dialog.get_by_role("button", name="Close", exact=True).click()
            page.reload()
            expect(page.get_by_role("link", name="React Console", exact=True)).to_have_count(0)
            removed = page.request.get(runtime.base_url + asset_a)
            assert removed.status == 404
            assert not page_errors, page_errors

            evidence["browser"] = {"build": True, "enable": True, "iframeApi": True,
                                   "oldLazyChunkAfterRebuild": True, "newPageUsesNewBuild": True,
                                   "failedRebuildRetained": True, "disable": True,
                                   "unbuild": True, "pageErrors": page_errors}
            evidence["builds"] = {"a": build_a, "b": build_b, "assetA": asset_a, "assetB": asset_b,
                                  "oldLazy": "Bundle A", "newLazy": "Bundle B"}
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
                            and (plugin_dir / "src" / "lazy.jsx").is_file()}
        assert evidence["http"]["sourceFilesPreserved"]
    finally:
        runtime.stop()
        leftovers = _owned_processes(tmp_path)
        evidence["portReleased"] = _port_is_free(PORT)
        evidence["ownedDescendantsAfterStop"] = leftovers
        assert evidence["portReleased"]
        assert not leftovers, f"owned build/server descendants survived: {leftovers}"
        log = (data / "pan.log").read_text(encoding="utf-8", errors="replace")
        for path, replacement in ((str(data), "<owned-data>"), (str(plugin_dir), "<owned-plugin>"),
                                  (str(ROOT), "<repo>")):
            log = log.replace(path, replacement)
        (EVIDENCE / "server.log").write_text(log, encoding="utf-8")
        _remove_react_example(plugin_dir)
        (EVIDENCE / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
