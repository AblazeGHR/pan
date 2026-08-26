"""Manifest Loader — loads external plugin manifest.json files.

Each plugin declares characters, tools, and configuration in a manifest.json
at its root. The loader resolves paths, deduplicates by name, and returns
canonical config for Pan Core.

Usage::

    from packages.core.manifest_loader import load_manifests

    config = load_manifests(["D:/project/RuleWhisper/pan_plugin"])
    for template in config.session_templates:
        print(template.name, template.system_prompt[:60])
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# The three capability flags live nested under ``pan_access``. Manifest files
# may still declare them as top-level fields; _parse_session_template migrates.
_PAN_ACCESS_KEYS = ("restrict_to_managed", "can_claim_unmanaged", "auto_claim_created")


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
    cwd: str | None = None


@dataclass
class SessionTemplate:
    """Session configuration template from a manifest.

    Declares everything needed to open one session: system_prompt / adapter /
    model / permission_mode / mcp_mode / mcp_servers. Contains no character
    bootstrap data (memory/assets live on CharacterTemplate).
    """
    name: str
    # Empty string means "adapter not specified by the manifest" — the caller
    # (e.g. session creation) falls back to the default adapter ("cbc"). This
    # deliberately differs from a template that *explicitly* sets
    # ``adapter: "cbc"``: the frontend unlocks the adapter selector only when
    # the value is empty/None (falsy).
    adapter: str = ""
    model: str | None = None
    permission_mode: str | None = None
    system_prompt: str = ""
    mcp_mode: str = "optional"    # "always" | "optional" | "never"
    mcp_servers: list[str] = field(default_factory=list)
    source_manifest: str = ""      # Which manifest.json defined this template
    # Self-explanatory capability flags (replaces the opaque `role` enum),
    # nested under ``pan_access``; readable via the flat properties below.
    pan_access: dict = field(default_factory=dict)

    @property
    def mcp_locked(self) -> bool:
        """True if mcp_mode prevents toggling MCP on/off."""
        return self.mcp_mode in ("always", "never")

    @property
    def mcp_default(self) -> bool:
        """True if MCP should be enabled by default for this template."""
        return self.mcp_mode == "always"

    @property
    def restrict_to_managed(self) -> bool:
        """Operations on other sessions are gated by `managed`."""
        return bool(self.pan_access.get("restrict_to_managed", False))

    @restrict_to_managed.setter
    def restrict_to_managed(self, value: bool):
        self.pan_access["restrict_to_managed"] = bool(value)

    @property
    def can_claim_unmanaged(self) -> bool:
        """May claim an unclaimed session into `managed`."""
        return bool(self.pan_access.get("can_claim_unmanaged", False))

    @can_claim_unmanaged.setter
    def can_claim_unmanaged(self, value: bool):
        self.pan_access["can_claim_unmanaged"] = bool(value)

    @property
    def auto_claim_created(self) -> bool:
        """Sessions this session creates are auto-claimed."""
        return bool(self.pan_access.get("auto_claim_created", False))

    @auto_claim_created.setter
    def auto_claim_created(self, value: bool):
        self.pan_access["auto_claim_created"] = bool(value)

    @property
    def source_manifest_label(self) -> str:
        """Readable short label of the defining manifest for UI display.

        Relative to the repo root when the manifest lives inside it
        (``packages/mcp/manifest.json``); otherwise just the last directory
        plus ``manifest.json`` (``pan_plugin/manifest.json``). The repo-root
        manifest itself collapses to ``manifest.json``.
        """
        if not self.source_manifest:
            return ""
        src = Path(self.source_manifest).resolve()
        try:
            parts = list(src.relative_to(REPO_ROOT).parts)
        except ValueError:
            parts = [src.name]
        parts.append("manifest.json")
        return "/".join(parts)


@dataclass
class CharacterTemplate:
    """Character creation template from a manifest.

    Bootstraps a character: initial memory + assets, plus references to the
    session_template(s) this character type is designed to run with. Contains
    no system_prompt — session config lives on SessionTemplate.
    """
    name: str
    session_templates: list[str] = field(default_factory=list)  # referenced session_template names
    memory_dir: str | None = None  # bootstrap memory .md files
    assets_dir: str | None = None  # bootstrap assets directory
    source_manifest: str = ""      # Which manifest.json defined this template


@dataclass
class CommandRoute:
    """QQ Bot command routing rule from a manifest."""
    prefixes: list[str] = field(default_factory=list)
    target: str = ""  # URL to forward matching messages to


@dataclass
class ManifestConfig:
    """Aggregated config from all loaded manifests."""
    session_templates: list[SessionTemplate] = field(default_factory=list)
    character_templates: list[CharacterTemplate] = field(default_factory=list)
    mcp_servers: list[McpServer] = field(default_factory=list)
    command_routes: list[CommandRoute] = field(default_factory=list)

    def get_session_template(self, name: str) -> SessionTemplate | None:
        for t in self.session_templates:
            if t.name == name:
                return t
        return None

    def get_character_template(self, name: str) -> CharacterTemplate | None:
        for t in self.character_templates:
            if t.name == name:
                return t
        return None


# ------------------------------------------------------------------ #
#  Loader
# ------------------------------------------------------------------ #

# Repo-root anchor: packages/core/manifest_loader.py → repo root.
# Relative plugin_manifests are rebased here (not CWD), so starting Pan from
# any working directory still finds them (跨设备移植：消除 CWD 依赖).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def resolve_manifest_files(plugin_paths: list[str]) -> list[Path]:
    """Resolve *plugin_paths* to the concrete manifest.json files they point at.

    Mirrors the path-resolution logic that ``load_manifests`` uses, but only
    returns the (deduplicated, existence-filtered) list of files so callers can
    ``stat`` them for change detection without re-parsing. Entries that do not
    exist are skipped — matching ``load_manifests``' skip-on-missing behaviour.

    This is the single source of truth for "which files does a given
    ``plugin_paths`` resolve to", so hot-reload can stat exactly the files that
    were loaded (no drift between load and change-detection).
    """
    files: list[Path] = []
    seen: set[Path] = set()
    for raw_path in plugin_paths:
        p = Path(raw_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.exists():
            continue
        manifest_path = p if p.suffix == ".json" else p / "manifest.json"
        if not manifest_path.exists():
            continue
        resolved = manifest_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        files.append(resolved)
    return files


def load_manifests(plugin_paths: list[str]) -> ManifestConfig:
    """Load and merge manifest.json files from multiple plugin directories.

    Each *plugin_paths* entry can be:
    - A directory containing `manifest.json` (e.g. ``D:/project/RuleWhisper/pan_plugin``)
    - A path to a `.json` file directly

    Relative paths are resolved against the repo root, not the process CWD.
    Later-loaded entries override earlier ones by name (dedup).
    """
    config = ManifestConfig()

    for manifest_path in resolve_manifest_files(plugin_paths):
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
    # Session templates — dedup by name. ``profiles`` is the legacy key kept
    # for backward compatibility (pre-refactor manifests used it).
    for raw in data.get("session_templates", data.get("profiles", [])):
        template = _parse_session_template(raw, plugin_dir)
        _dedup_append(config.session_templates, template, key=lambda t: t.name)

    # Character templates — dedup by name
    for raw in data.get("character_templates", []):
        template = _parse_character_template(raw, plugin_dir)
        _dedup_append(config.character_templates, template, key=lambda t: t.name)

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


def _parse_session_template(raw: dict, plugin_dir: str) -> SessionTemplate:
    """Parse a session_template entry from manifest.json."""
    # Normalize system_prompt: if it's a list, join with newlines
    sp = raw.get("system_prompt", "")
    if isinstance(sp, list):
        sp = "\n".join(sp)

    # Capability flags: nested ``pan_access`` (canonical), or legacy top-level
    # keys from pre-refactor manifests. Explicit nested values win.
    pa = dict(raw.get("pan_access", {}) or {})
    for key in _PAN_ACCESS_KEYS:
        if key in pa:
            pa[key] = bool(pa[key])
        else:
            pa[key] = bool(raw.get(key, False))

    return SessionTemplate(
        name=raw.get("name", ""),
        # ``raw.get("adapter") or ""`` distinguishes "not specified" (→ "")
        # from an explicit adapter name. Empty string lets the caller fall back
        # to the default adapter; a non-empty value locks the adapter.
        adapter=raw.get("adapter") or "",
        model=raw.get("model"),
        mcp_mode=raw.get("mcp_mode", "optional"),
        permission_mode=raw.get("permission_mode"),
        system_prompt=sp,
        mcp_servers=list(raw.get("mcp_servers", [])),
        source_manifest=plugin_dir,
        pan_access=pa,
    )


def _parse_character_template(raw: dict, plugin_dir: str) -> CharacterTemplate:
    """Parse a character_template entry from manifest.json."""
    session_templates = raw.get("session_templates", [])
    if isinstance(session_templates, str):
        session_templates = [session_templates]

    return CharacterTemplate(
        name=raw.get("name", ""),
        session_templates=list(session_templates),
        memory_dir=_resolve_plugin_dir(raw.get("memory_dir"), plugin_dir),
        assets_dir=_resolve_plugin_dir(raw.get("assets_dir"), plugin_dir),
        source_manifest=plugin_dir,
    )


def _resolve_plugin_dir(value, plugin_dir: str) -> str | None:
    """Resolve a manifest-declared directory relative to *plugin_dir*.

    Validates the result stays inside the plugin dir — an absolute or
    ``..``-escaping path would let a malicious manifest index arbitrary
    directories (#31). Returns None for empty/escaping values.
    """
    if not value:
        return None
    resolved = str(Path(plugin_dir, value).resolve())
    try:
        Path(resolved).relative_to(Path(plugin_dir).resolve())
    except ValueError:
        log.error(
            "Manifest dir %r escapes plugin dir %r; ignoring it",
            value,
            plugin_dir,
        )
        return None
    return resolved


def _resolve_plugin_var(value: str, plugin_dir: str) -> str:
    """Replace ${PLUGIN_DIR} and normalize the path. Only resolves if the var is present."""
    if "${PLUGIN_DIR}" in value:
        return str(Path(value.replace("${PLUGIN_DIR}", plugin_dir)).resolve())
    return value


def _parse_mcp_server(raw: dict, plugin_dir: str) -> McpServer:
    """Parse an MCP server entry, resolving ${PLUGIN_DIR}."""
    srv = McpServer(name=raw.get("name", ""))
    if "command" in raw:
        srv.command = _resolve_plugin_var(raw["command"], plugin_dir) if isinstance(raw["command"], str) else raw["command"]
    if "args" in raw:
        srv.args = [
            _resolve_plugin_var(arg, plugin_dir) if isinstance(arg, str) else str(arg)
            for arg in raw["args"]
        ]
    if "env" in raw:
        srv.env = {
            k: _resolve_plugin_var(v, plugin_dir) if isinstance(v, str) else str(v)
            for k, v in raw["env"].items()
        }
    if "cwd" in raw:
        srv.cwd = _resolve_plugin_var(raw["cwd"], plugin_dir) if isinstance(raw["cwd"], str) else raw["cwd"]
    return srv


def _dedup_append(lst: list, item, key) -> None:
    """Replace existing item with same key, otherwise append.

    Overriding an earlier entry is intentional (later manifests win) but must
    not be silent — a plugin shadowing a built-in template/system_prompt is
    usually a misconfiguration (#40).
    """
    k = key(item)
    for i, existing in enumerate(lst):
        if key(existing) == k:
            if existing != item:
                log.warning(
                    "Manifest entry %r overridden by later manifest (dedup)",
                    k,
                )
            lst[i] = item
            return
    lst.append(item)
