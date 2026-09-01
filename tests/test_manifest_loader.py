"""Regression tests for the tracked/default Pan manifest."""

import json
from pathlib import Path

from packages.core.manifest_loader import REPO_ROOT, load_manifests


def test_default_root_manifest_loads_pan_and_pan_qq_without_config_file():
    """The startup fallback manifest must provide both built-in MCP servers."""
    manifest_path = REPO_ROOT / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    config = load_manifests(["manifest.json"])
    servers = {server.name: server for server in config.mcp_servers}

    assert {entry["name"] for entry in data["mcp_servers"]} == {"pan", "pan-qq"}
    assert set(servers) == {"pan", "pan-qq"}
    assert servers["pan"].command == str(REPO_ROOT / ".venv/Scripts/python")
    assert servers["pan"].args == ["-m", "packages.mcp.server"]
    assert servers["pan"].cwd == str(REPO_ROOT)
    assert servers["pan-qq"].args == ["-m", "packages.qq.mcp"]
    assert servers["pan-qq"].cwd == str(REPO_ROOT)


def test_root_and_package_manifests_deduplicate_builtin_mcp_servers():
    """Explicitly loading the package manifest must not create duplicates."""
    config = load_manifests(["manifest.json", "packages/mcp/manifest.json"])

    assert [server.name for server in config.mcp_servers].count("pan") == 1
    assert [server.name for server in config.mcp_servers].count("pan-qq") == 1
    # The later package manifest wins by the documented loader rule, while
    # still resolving to the same repository shared venv and cwd.
    pan = next(server for server in config.mcp_servers if server.name == "pan")
    assert pan.command == str(REPO_ROOT / ".venv/Scripts/python")
    assert pan.cwd == str(REPO_ROOT)
