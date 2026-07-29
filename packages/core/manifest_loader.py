"""Manifest Loader — loads external plugin manifest.json files.

Each plugin declares characters, tools, and configuration in a manifest.json
at its root. The loader resolves paths, deduplicates by name, and returns
canonical config for Pan Core.

Usage::

    from packages.core.manifest_loader import load_manifests

    config = load_manifests(["D:/project/RuleWhisper/pan_plugin"])
    for profile in config.profiles:
        print(profile.name, profile.system_prompt[:60])
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Data types
# ------------------------------------------------------------------ #

@dataclass
class McpServer:
    """MCP server declaration from a manifest."""
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Profile:
    """Character creation template from a manifest."""
    name: str
    adapter: str = "cbc"
    model: str | None = None
    permission_mode: str | None = None
    system_prompt: str = ""
    mcp_servers: list[str] = field(default_factory=list)
    memory_dir: str | None = None  # Path to character's memory .md files
    source_manifest: str = ""      # Which manifest.json defined this profile


@dataclass
class CommandRoute:
    """QQ Bot command routing rule from a manifest."""
    prefixes: list[str] = field(default_factory=list)
    target: str = ""  # URL to forward matching messages to


@dataclass
class ManifestConfig:
    """Aggregated config from all loaded manifests."""
    profiles: list[Profile] = field(default_factory=list)
    mcp_servers: list[McpServer] = field(default_factory=list)
    command_routes: list[CommandRoute] = field(default_factory=list)

    def get_profile(self, name: str) -> Profile | None:
        for p in self.profiles:
            if p.name == name:
                return p
        return None


# ------------------------------------------------------------------ #
#  Loader
# ------------------------------------------------------------------ #

def load_manifests(plugin_paths: list[str]) -> ManifestConfig:
    """Load and merge manifest.json files from multiple plugin directories.

    Each *plugin_paths* entry can be:
    - A directory containing `manifest.json` (e.g. ``D:/project/RuleWhisper/pan_plugin``)
    - A path to a `.json` file directly

    Later-loaded entries override earlier ones by name (dedup).
    """
    config = ManifestConfig()

    for raw_path in plugin_paths:
        p = Path(raw_path)
        if not p.exists():
            log.warning("Plugin path not found, skipping: %s", raw_path)
            continue

        manifest_path = p if p.suffix == ".json" else p / "manifest.json"
        if not manifest_path.exists():
            log.warning("No manifest.json at %s, skipping", raw_path)
            continue

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Failed to parse %s: %s", manifest_path, exc)
            continue

        plugin_dir = str(manifest_path.parent.resolve())
        _merge_manifest(config, data, plugin_dir)

    return config


def _merge_manifest(config: ManifestConfig, data: dict, plugin_dir: str) -> None:
    """Merge a single manifest's data into *config* with dedup."""
    # Profiles — dedup by name
    for raw in data.get("profiles", []):
        profile = _parse_profile(raw, plugin_dir)
        _dedup_append(config.profiles, profile, key=lambda p: p.name)

    # MCP servers — dedup by name
    for raw in data.get("mcp_servers", []):
        srv = _parse_mcp_server(raw, plugin_dir)
        _dedup_append(config.mcp_servers, srv, key=lambda s: s.name)

    # Command routes — append all (no dedup needed; prefixes may overlap intentionally)
    for raw in data.get("command_routes", []):
        route = CommandRoute(
            prefixes=list(raw.get("prefix", raw.get("prefixes", []))),
            target=raw.get("target", ""),
        )
        config.command_routes.append(route)


def _parse_profile(raw: dict, plugin_dir: str) -> Profile:
    """Parse a profile entry from manifest.json."""
    memory_dir = raw.get("memory_dir")
    if memory_dir and not Path(memory_dir).is_absolute():
        memory_dir = str(Path(plugin_dir) / memory_dir)

    return Profile(
        name=raw.get("name", ""),
        adapter=raw.get("adapter", "cbc"),
        model=raw.get("model"),
        permission_mode=raw.get("permission_mode"),
        system_prompt=raw.get("system_prompt", ""),
        mcp_servers=list(raw.get("mcp_servers", [])),
        memory_dir=memory_dir,
        source_manifest=plugin_dir,
    )


def _parse_mcp_server(raw: dict, plugin_dir: str) -> McpServer:
    """Parse an MCP server entry, resolving ${PLUGIN_DIR}."""
    srv = McpServer(name=raw.get("name", ""))
    if "command" in raw:
        srv.command = raw["command"]
    if "args" in raw:
        srv.args = [
            arg.replace("${PLUGIN_DIR}", plugin_dir) if isinstance(arg, str) else str(arg)
            for arg in raw["args"]
        ]
    if "env" in raw:
        srv.env = {
            k: v.replace("${PLUGIN_DIR}", plugin_dir) if isinstance(v, str) else str(v)
            for k, v in raw["env"].items()
        }
    return srv


def _dedup_append(lst: list, item, key) -> None:
    """Replace existing item with same key, otherwise append."""
    k = key(item)
    for i, existing in enumerate(lst):
        if key(existing) == k:
            lst[i] = item
            return
    lst.append(item)
