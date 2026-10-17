"""Plugin declarations and configuration compilation, independent of running providers."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from .validation import validate_document

MODES = ("standalone", "pan_native", "gateway")
RESERVED = {"pan", "pan-tools"}
_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_EXPORT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Plugin:
    id: str
    display_name: str
    source: dict
    aliases: tuple[str, ...] = ()
    supported_modes: tuple[str, ...] = MODES
    default_mode: str = "standalone"
    tool_exports: dict[str, str] = field(default_factory=dict)
    version: str = "1.0.0"
    legacy: bool = False
    panels: tuple[dict, ...] = ()
    data_version: int = 1
    agent_capabilities: tuple[str, ...] | None = None

    def public(self) -> dict:
        return {"id": self.id, "name": self.id, "displayName": self.display_name,
                "aliases": list(self.aliases), "supportedModes": list(self.supported_modes),
                "defaultMode": self.default_mode, "sourceKind": self.source["kind"],
                "version": self.version, "legacy": self.legacy}


class Catalog:
    def __init__(self, manifest):
        self.servers = {s.name: {k: v for k, v in asdict(s).items()
                                if v is not None and v != [] and v != {}}
                        for s in manifest.mcp_servers}
        self.plugins: dict[str, Plugin] = {}
        self.aliases: dict[str, str] = {}
        declared_refs = set()
        for raw in getattr(manifest, "plugins", []):
            validate_document("plugin", raw)
            if raw.get("protocol") != "pan-plugin/v1":
                raise ValueError("Plugin requires protocol pan-plugin/v1")
            pid = raw.get("id", "")
            if not isinstance(pid, str) or not _ID.fullmatch(pid) or pid in RESERVED:
                raise ValueError(f"Invalid or reserved plugin ID: {pid!r}")
            source = copy.deepcopy(raw.get("source", {}))
            if source.get("kind") not in {"mcp", "pan_api"}:
                raise ValueError(f"Plugin {pid}: source must be mcp or pan_api")
            if source["kind"] == "mcp":
                ref = source.get("server_ref")
                if ref not in self.servers or ref in RESERVED:
                    raise ValueError(f"Plugin {pid}: invalid MCP server_ref {ref!r}")
                if "packages.plugins.mcp" in self.servers[ref].get("args", []):
                    raise ValueError("A plugin cannot aggregate the Pan plugin projection")
                declared_refs.add(ref)
            else:
                file = source.get("capabilities_file")
                if not isinstance(file, str) or not Path(file).is_file():
                    raise ValueError(f"Plugin {pid}: capabilities_file is unavailable")
                document = json.loads(Path(file).read_text(encoding="utf-8"))
                validate_document("capability", document)
                provider = document.get("provider", {})
                if provider.get("transport") == "stdio_jsonrpc":
                    command_ref = provider.get("command_ref")
                    if command_ref not in self.servers or command_ref in RESERVED:
                        raise ValueError(f"Plugin {pid}: unknown API command_ref")
                    declared_refs.add(command_ref)
            exposure = raw.get("exposure", {})
            modes = tuple(exposure.get("supported_modes", MODES))
            default = exposure.get("default_mode", "standalone")
            if not modes or len(set(modes)) != len(modes) or any(m not in MODES for m in modes):
                raise ValueError(f"Plugin {pid}: invalid supported_modes")
            if default not in modes:
                raise ValueError(f"Plugin {pid}: default_mode must be supported")
            exports = raw.get("tool_exports", {})
            if not isinstance(exports, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) or not _EXPORT.fullmatch(v)
                for k, v in exports.items()
            ) or len(set(exports.values())) != len(exports):
                raise ValueError(f"Plugin {pid}: invalid or duplicate tool_exports")
            aliases = raw.get("aliases", [])
            if not isinstance(aliases, list) or any(not isinstance(a, str) or not a for a in aliases):
                raise ValueError(f"Plugin {pid}: aliases must be non-empty strings")
            panels = raw.get("panels", [])
            agent_capabilities = raw.get("agent_capabilities")
            if agent_capabilities is not None:
                if source['kind'] != 'pan_api' or any(cap not in {c['id'] for c in document['capabilities']} for cap in agent_capabilities):
                    raise ValueError("agent_capabilities requires declared API capabilities")
            if len({p["id"] for p in panels}) != len(panels):
                raise ValueError(f"Plugin {pid}: duplicate panel ID")
            for panel in panels:
                expected_effects = {'list': 'read', 'quota': 'read', 'switch': 'write_external', 'default_account': 'write_external'}
                if any(panel['actions'].get(name, {}).get('effect') != effect for name, effect in expected_effects.items()):
                    raise ValueError('account-switcher/v1 requires list/quota reads and switch/default_account external writes')
                if any(not action["capability"].startswith(pid + ".") for action in panel["actions"].values()):
                    raise ValueError("Panel actions must belong to their plugin")
                if source['kind'] == 'pan_api' and any(action['capability'] not in {c['id'] for c in document['capabilities']} for action in panel['actions'].values()):
                    raise ValueError('Panel action capability is not declared')
            self._add(Plugin(pid, raw.get("display_name") or pid, source, tuple(aliases),
                             modes, default, dict(exports), raw.get("version", "1.0.0"),
                             panels=tuple(copy.deepcopy(panels)), data_version=raw.get("data", {}).get("version", 1),
                             agent_capabilities=tuple(agent_capabilities) if agent_capabilities is not None else None))
        for name in self.servers:
            if name in declared_refs or name in self.plugins or name in self.aliases:
                continue
            if name == "pan-tools":
                raise ValueError("pan-tools is reserved for generated plugin access")
            if name in {"pan-qq", "qq"} and "qq" not in self.plugins:
                self._add(Plugin("qq", "QQ", {"kind": "mcp", "server_ref": name},
                                 aliases=("pan-qq",), legacy=True, tool_exports={
                                     "qq_" + export: export for export in ("send_message", "send_file",
                                     "read_conversation", "list_contacts", "read_inbox", "bind", "unbind")}))
                continue
            modes = ("standalone",) if name == "pan" else MODES
            self._add(Plugin(name, name, {"kind": "mcp", "server_ref": name},
                             supported_modes=modes, legacy=True))
        # Aliases may refer to legacy descriptors, but cannot shadow another plugin.
        for alias, pid in self.aliases.items():
            if alias in self.plugins and alias != pid:
                raise ValueError(f"Plugin alias collision: {alias}")

    def _add(self, plugin: Plugin) -> None:
        if plugin.id in self.plugins or plugin.id in self.aliases:
            raise ValueError(f"Duplicate plugin ID: {plugin.id}")
        self.plugins[plugin.id] = plugin
        for alias in plugin.aliases:
            if alias in RESERVED or alias in self.aliases or alias == plugin.id:
                raise ValueError(f"Plugin alias collision: {alias}")
            self.aliases[alias] = plugin.id

    def get(self, name: str) -> Plugin:
        if not isinstance(name, str):
            raise ValueError("Plugin ID must be a string")
        pid = self.aliases.get(name, name)
        if pid not in self.plugins:
            raise ValueError(f"Unknown plugin: {name!r}")
        return self.plugins[pid]

    def bindings(self, raw: object) -> list[dict]:
        if not isinstance(raw, list):
            raise ValueError("pluginBindings must be an array")
        result, seen = [], set()
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"plugin_id", "enabled", "mode"}:
                raise ValueError("Invalid plugin binding fields")
            plugin = self.get(item.get("plugin_id"))
            enabled = item.get("enabled", True)
            mode = item.get("mode", plugin.default_mode)
            if not isinstance(enabled, bool) or mode not in plugin.supported_modes:
                raise ValueError(f"Invalid enabled/mode for plugin {plugin.id}")
            binding = {"plugin_id": plugin.id, "enabled": enabled, "mode": mode}
            if plugin.id in seen:
                previous = next(b for b in result if b["plugin_id"] == plugin.id)
                if previous != binding:
                    raise ValueError(f"Conflicting bindings for plugin {plugin.id}")
                continue
            seen.add(plugin.id)
            result.append(binding)
        return result

    def legacy_bindings(self, names: list[str]) -> list[dict]:
        if not isinstance(names, list):
            raise ValueError("MCP server names must be a list")
        return self.bindings([{"plugin_id": name, "enabled": True, "mode": "standalone"}
                              for name in names])

    def compile(self, bindings: list[dict]) -> list[dict]:
        """Physical descriptors; no gateway capability names in connection config."""
        from packages.core.config import resolve_pan_python_argv
        root = str(Path(__file__).resolve().parents[2])
        python = resolve_pan_python_argv()

        def projection(name, view, *extra):
            return {"name": name, "command": python[0],
                    "args": [*python[1:], "-m", "packages.plugins.mcp", "--view", view, *extra],
                    "cwd": root}

        result, native, gateway, core = [], [], False, False
        for binding in self.bindings(bindings):
            if not binding["enabled"]:
                continue
            plugin, mode = self.get(binding["plugin_id"]), binding["mode"]
            if plugin.id == "pan":
                core = True
            elif mode == "gateway":
                gateway = True
            elif mode == "pan_native":
                native.append(plugin.id)
            elif plugin.source["kind"] == "mcp":
                cfg = copy.deepcopy(self.servers[plugin.source["server_ref"]])
                cfg["name"] = plugin.id
                result.append(cfg)
            else:
                result.append(projection(plugin.id, "standalone", "--plugin", plugin.id))
        if native:
            pan = projection("pan", "native", *(["--core"] if core else []))
            # Native selections change actual tools; gateway selections do not.
            pan["env"] = {"PAN_NATIVE_PLUGINS": json.dumps(sorted(native))}
            result.append(pan)
        elif core:
            result.append(copy.deepcopy(self.servers["pan"]))
        if gateway:
            result.append(projection("pan-tools", "gateway"))
        return result


def session_bindings(session, catalog: Catalog) -> list[dict]:
    ac = session.adapter_config
    if "plugin_bindings" in ac:
        return catalog.bindings(ac["plugin_bindings"])
    return catalog.legacy_bindings([s["name"] for s in ac.get("mcp_servers", [])])
