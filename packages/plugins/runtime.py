"""Authoritative per-Session capability directory and invocation service."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from pathlib import Path

import jsonschema
from mcp import types

from .catalog import Catalog, digest, session_bindings
from .providers import ApiProvider, McpProvider
from .validation import validate_document
from .lifecycle import coordinator, io

log = logging.getLogger(__name__)


def error(code, message):
    return {"content": [{"type": "text", "text": json.dumps(
        {"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False)}],
        "isError": True}


def read_api_document(plugin):
    document = json.loads(Path(plugin.source["capabilities_file"]).read_text(encoding="utf-8"))
    validate_document("capability", document)
    if document.get("protocol") != "pan-capability/v1" or document.get("plugin_id") != plugin.id:
        raise ValueError("API document protocol/plugin_id mismatch")
    spec = document.get("provider", {})
    if spec.get("transport") not in {"stdio_jsonrpc", "http"}:
        raise ValueError("Unsupported API provider transport")
    caps = document.get("capabilities")
    if not isinstance(caps, list) or not caps:
        raise ValueError("API document requires capabilities")
    seen = set()
    for cap in caps:
        name = cap.get("id", "")
        export = cap.get("export_key", "")
        if not isinstance(name, str) or not name.startswith(plugin.id + ".") or name in seen:
            raise ValueError("Invalid or duplicate API capability ID")
        if not isinstance(export, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", export):
            raise ValueError("Invalid API export_key")
        seen.add(name)
        if not cap.get("description") or not cap.get("contract_version"):
            raise ValueError("API capability requires description and contract_version")
        jsonschema.validators.validator_for(cap["input_schema"]).check_schema(cap["input_schema"])
        if cap.get("output_schema"):
            jsonschema.validators.validator_for(cap["output_schema"]).check_schema(cap["output_schema"])
        execution = cap.get("execution", {})
        if spec["transport"] == "stdio_jsonrpc" and not execution.get("operation"):
            raise ValueError("API capability requires execution.operation")
        if spec["transport"] == "http":
            from urllib.parse import urlsplit
            url = urlsplit(spec.get("endpoint", ""))
            path = execution.get("path", "")
            if url.scheme not in {"http", "https"} or not url.hostname or url.query or url.fragment:
                raise ValueError("API provider requires a configured HTTP endpoint")
            if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
                    or "://" in path or execution.get("method") not in {"GET", "POST", "PUT", "PATCH", "DELETE"}):
                raise ValueError("API capability requires fixed execution.path/method")
    return document


class Runtime:
    def __init__(self, get_manifest, get_session, on_native_change=None, data_root=None, audience="agent"):
        self.get_manifest = get_manifest
        self.get_session = get_session
        self.on_native_change = on_native_change
        self.providers = {}
        self.snapshots = {}
        self.generations = {}
        self.locks = {}
        self.invocations = set()
        self.requests = set()
        self.closing = False
        self.watcher = None
        self.data_root = data_root
        self.audience = audience

    def context(self, sid):
        session = self.get_session(sid)
        if session is None:
            raise ValueError("Session not found")
        catalog = Catalog(self.get_manifest())
        from .lifecycle import apply_pins, pinned
        apply_pins(catalog, sid)
        bindings = session_bindings(session, catalog)
        unpinned = [b for b in bindings if b['enabled'] and b['plugin_id'].startswith('action-group-')
                    and b['plugin_id'] not in pinned(sid)[0]]
        if unpinned:
            from packages.core.worker import find_alive_worker_by_session
            if find_alive_worker_by_session(sid):
                # Adding a Group to an existing gateway does not change its
                # descriptor. Still require a real Worker start to admit it.
                for binding in unpinned:
                    binding['enabled'] = False
        return session, catalog, bindings

    async def snapshot(self, sid, pid):
        async with coordinator().claim(shared={pid} if pid.startswith('action-group-') else ()):
            async with self.locks.setdefault((sid, pid), asyncio.Lock()):
                return await self._snapshot(sid, pid)

    async def _snapshot(self, sid, pid):
        session, catalog, bindings = await io(self.context, sid)
        plugin = catalog.get(pid)
        binding = next((b for b in bindings if b["plugin_id"] == plugin.id and b["enabled"]), None)
        if not binding:
            raise ValueError("Plugin is disabled for this Session")
        key = (sid, plugin.id)
        document = await io(read_api_document, plugin) if plugin.source["kind"] == "pan_api" else None
        config = document if document else catalog.servers[plugin.source["server_ref"]]
        revision = digest([plugin.source, config, plugin.agent_capabilities, plugin.data_version])
        owner = self.providers.get(key)
        if owner is None or owner[0] != revision or owner[1].closed:
            if owner:
                await owner[1].close()
            from .data import plugin_data_dir
            data_dir = plugin_data_dir(self.data_root, plugin.id, plugin.data_version) if self.data_root else None
            provider = ApiProvider(document, catalog, session, data_dir) if document else McpProvider(config, session, data_dir)
            self.providers[key] = (revision, provider)
        provider = self.providers[key][1]
        tools = await provider.request("list")
        descriptors, names, native_names = [], set(), set()
        for tool in tools:
            types.Tool.model_validate(tool)
            original = tool["name"]
            if document:
                cap = next(c for c in document["capabilities"] if c["id"] == original)
                canonical, export, version = cap["id"], cap["export_key"], cap["contract_version"]
            else:
                export = plugin.tool_exports.get(original, original.replace("-", "_"))
                canonical, version = f"{plugin.id}.{export}", plugin.version
            native = "pan_" + plugin.id.replace("-", "_") + "_" + export
            if (canonical in names or native in native_names or not re.fullmatch(r"[A-Za-z0-9_]+", native)
                    or len(native) > 128):
                raise ValueError("Conflicting or invalid projected tool name")
            names.add(canonical)
            native_names.add(native)
            jsonschema.validators.validator_for(tool["inputSchema"]).check_schema(tool["inputSchema"])
            descriptors.append({"name": canonical, "plugin": plugin.id, "source_name": original,
                                "native_name": native, "tool": copy.deepcopy(tool),
                                "contract_version": version, "contract_digest": digest([tool, version])})
        if self.audience != "panel" and plugin.agent_capabilities is not None:
            descriptors = [d for d in descriptors if d['name'] in plugin.agent_capabilities]
        previous = self.snapshots.get(key)
        if previous != descriptors:
            self.snapshots[key] = descriptors
            self.generations[key] = self.generations.get(key, 0) + 1
            if previous is not None and binding["mode"] != "gateway" and self.on_native_change:
                await self.on_native_change(sid)
        return descriptors

    async def directory(self, sid, view, plugin_id=None):
        _, catalog, bindings = await io(self.context, sid)
        mode = {"native": "pan_native", "gateway": "gateway", "standalone": "standalone"}.get(view)
        if mode is None:
            raise ValueError("Unknown projection view")
        result = []
        for binding in bindings:
            if (binding["enabled"] and binding["mode"] == mode and binding["plugin_id"] != "pan"
                    and (plugin_id is None or binding["plugin_id"] == plugin_id)):
                descriptors = await self.snapshot(sid, binding["plugin_id"])
                allowed = catalog.get(binding["plugin_id"]).agent_capabilities
                result.extend(d for d in descriptors if self.audience == "panel" or allowed is None or d['name'] in allowed)
        native = [d["native_name"] for d in result]
        if len(set(native)) != len(native):
            raise ValueError("Projected tool name collision between plugins")
        return result

    async def invoke(self, sid, view, name, arguments, expected_digest=None, plugin_id=None):
        descriptors = await self.directory(sid, view, plugin_id)
        key = "native_name" if view == "native" else ("source_name" if view == "standalone" else "name")
        descriptor = next((d for d in descriptors if d[key] == name), None)
        if not descriptor:
            return error("tool_unavailable", "Tool is not enabled in this Session projection")
        if descriptor['plugin'].startswith('action-group-'):
            async with coordinator().claim(shared={descriptor['plugin']}):
                # The initial directory may have preceded an unbuild/rebuild.
                current = await self.directory(sid, view, plugin_id)
                descriptor = next((d for d in current if d[key] == name), None)
                if descriptor is None:
                    return error('tool_unavailable', 'Action Group changed before admission')
                return await self._invoke_resolved(sid, view, descriptor, arguments, expected_digest)
        return await self._invoke_resolved(sid, view, descriptor, arguments, expected_digest)

    async def _invoke_resolved(self, sid, view, descriptor, arguments, expected_digest):
        if expected_digest and expected_digest != descriptor["contract_digest"]:
            return error("contract_changed", "Discover the current schema before calling again")
        try:
            jsonschema.validate(arguments, descriptor["tool"]["inputSchema"])
        except jsonschema.ValidationError as exc:
            return error("invalid_arguments", exc.message)
        # Resolve current bindings again after discovery awaited upstream work.
        _, _, bindings = await io(self.context, sid)
        mode = {"native": "pan_native", "gateway": "gateway", "standalone": "standalone"}[view]
        if not any(b["enabled"] and b["plugin_id"] == descriptor["plugin"] and b["mode"] == mode for b in bindings):
            return error("tool_unavailable", "Plugin configuration changed")
        provider = self.providers[(sid, descriptor["plugin"])][1]
        result = await provider.request("call", {"name": descriptor["source_name"], "arguments": arguments})
        types.CallToolResult.model_validate(result)
        if not result.get("isError") and descriptor["tool"].get("outputSchema"):
            jsonschema.validate(result.get("structuredContent"), descriptor["tool"]["outputSchema"])
        return result

    async def handle(self, request):
        if self.closing:
            return error("runtime_stopping", "Plugin runtime is stopping")
        task = asyncio.current_task()
        self.requests.add(task)
        try:
            return await self._dispatch(request)
        finally:
            self.requests.discard(task)

    async def _dispatch(self, request):
        if self.closing:
            return error("runtime_stopping", "Plugin runtime is stopping")
        sid, op = request["session_id"], request["operation"]
        view, plugin = request.get("view", "gateway"), request.get("plugin")
        if op == "core_enabled":
            _, _, bindings = self.context(sid)
            return {"enabled": any(b["enabled"] and b["plugin_id"] == "pan" for b in bindings)}
        if op == "list":
            return {"tools": await self.directory(sid, view, plugin)}
        if op == "discover":
            descriptors = await self.directory(sid, "gateway")
            query = request.get("query", "").casefold()
            name = request.get("name")
            filtered = [d for d in descriptors if (not plugin or d["plugin"] == plugin)
                        and (not name or d["name"] == name)
                        and (not query or query in (d["name"] + " " + d["tool"].get("description", "")).casefold())]
            selection = [(d["name"], d["contract_digest"]) for d in descriptors]
            generation = digest([sid, selection])
            cursor = request.get("cursor")
            offset = 0
            if cursor:
                version, offset_text = cursor.rsplit(":", 1)
                expected = digest([generation, query, plugin, name, bool(request.get("include_schema"))])
                if version != expected:
                    raise ValueError("Directory cursor expired; discover again")
                offset = int(offset_text)
                if offset < 0:
                    raise ValueError("Invalid directory cursor")
            limit = max(1, min(100, int(request.get("limit", 20))))
            items = []
            for d in filtered[offset:offset + limit]:
                item = {"name": d["name"], "plugin": d["plugin"],
                        "description": d["tool"].get("description", ""),
                        "contract_version": d["contract_version"], "contract_digest": d["contract_digest"]}
                if name or request.get("include_schema"):
                    item.update({k: v for k, v in d["tool"].items() if k != "name"})
                items.append(item)
            cursor_key = digest([generation, query, plugin, name, bool(request.get("include_schema"))])
            return {"tools": items, "generation": generation,
                    "next_cursor": f"{cursor_key}:{offset + limit}" if offset + limit < len(filtered) else None}
        if op == "call":
            task = asyncio.create_task(self.invoke(sid, view, request["name"], request.get("arguments", {}),
                                                   request.get("expected_contract_digest"), plugin))
            self.invocations.add(task)
            task.add_done_callback(self._retire)
            try:
                return await asyncio.wait_for(asyncio.shield(task), 35)
            except TimeoutError:
                return error("execution_unconfirmed", "Execution timed out; do not automatically replay")
            except Exception as exc:
                return error("execution_unconfirmed", f"Execution did not return a confirmed result: {exc}. Do not automatically replay")
        raise ValueError("Unknown plugin runtime operation")

    def _retire(self, task):
        self.invocations.discard(task)
        if not task.cancelled():
            task.exception()

    def start(self):
        self.watcher = asyncio.create_task(self._watch(), name="pan-plugin-directory-watch")

    async def _watch(self):
        while not self.closing:
            await asyncio.sleep(2)
            for sid, pid in list(self.providers):
                if self.closing:
                    break
                try:
                    bindings = self.context(sid)[2] if self.get_session(sid) is not None else []
                    if any(b["enabled"] and b["plugin_id"] == pid for b in bindings):
                        await asyncio.wait_for(self.snapshot(sid, pid), 10)
                    elif not self.invocations:
                        _, provider = self.providers.pop((sid, pid))
                        await provider.close()
                        self.snapshots.pop((sid, pid), None)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.debug("Plugin directory refresh unavailable: %s/%s", sid, pid)

    async def close(self):
        self.closing = True
        if self.watcher:
            self.watcher.cancel()
            await asyncio.gather(self.watcher, return_exceptions=True)
        if self.requests:
            await asyncio.gather(*self.requests, return_exceptions=True)
        if self.invocations:
            await asyncio.gather(*self.invocations, return_exceptions=True)
        await asyncio.gather(*(p.close() for _, p in self.providers.values()), return_exceptions=True)
        self.providers.clear()
