"""Regression tests for the tracked/default Pan manifest."""

import json
import sys

from packages.core.manifest_loader import REPO_ROOT, load_manifests


def test_default_root_manifest_loads_pan_and_pan_qq_without_config_file(monkeypatch):
    """The root manifest alone must provide both built-in MCP servers.

    A new user loading just ``manifest.json`` (without hunting down
    packages/mcp/manifest.json) gets a working Pan MCP server bound to the
    runtime interpreter.
    """
    monkeypatch.delenv("PAN_PYTHON", raising=False)
    manifest_path = REPO_ROOT / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    config = load_manifests(["manifest.json"])
    servers = {server.name: server for server in config.mcp_servers}

    assert {entry["name"] for entry in data["mcp_servers"]} == {"pan", "pan-qq"}
    assert set(servers) == {"pan", "pan-qq"}
    assert servers["pan"].command == sys.executable
    assert servers["pan"].args == ["-m", "packages.mcp.server"]
    assert servers["pan"].cwd == str(REPO_ROOT)
    assert servers["pan-qq"].args == ["-m", "packages.qq.mcp"]
    assert servers["pan-qq"].cwd == str(REPO_ROOT)


def test_root_manifest_pan_respects_pan_python_override(monkeypatch):
    """${PAN_PYTHON} on the root declaration honors an explicit interpreter."""
    monkeypatch.setenv("PAN_PYTHON", "portable-python")
    config = load_manifests(["manifest.json"])
    pan = next(server for server in config.mcp_servers if server.name == "pan")
    assert pan.command == "portable-python"


def test_root_and_package_manifests_deduplicate_builtin_mcp_servers(monkeypatch):
    """Explicitly loading the package manifest must not create duplicates."""
    monkeypatch.delenv("PAN_PYTHON", raising=False)
    config = load_manifests(["manifest.json", "packages/mcp/manifest.json"])

    assert [server.name for server in config.mcp_servers].count("pan") == 1
    assert [server.name for server in config.mcp_servers].count("pan-qq") == 1
    # The later package manifest wins by the documented loader rule, while
    # still resolving to the same shared interpreter and repo-root cwd as the
    # root declaration — so the combined catalog matches the root-only config.
    pan = next(server for server in config.mcp_servers if server.name == "pan")
    assert pan.command == sys.executable
    assert pan.args == ["-m", "packages.mcp.server"]
    assert pan.cwd == str(REPO_ROOT)
