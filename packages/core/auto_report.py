"""Automatic answer reports: explicit origins and immutable admission decisions.

This policy never governs manual sends, output publication or terminal logging.
Plugin message capability is independent of tools, panels and subscription transport.
"""
from __future__ import annotations

import copy
import re

DEFAULTS = {"system": False, "ma": True, "user": False, "plugins": {}}
manifest_provider = None


def normalize(value, *, strict=False):
    if not isinstance(value, dict):
        if strict:
            raise ValueError("autoReport must be an object")
        return copy.deepcopy(DEFAULTS)
    if strict and set(value) - set(DEFAULTS):
        raise ValueError("Unknown autoReport field")
    result = copy.deepcopy(DEFAULTS)
    for key in ("system", "ma", "user"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
        elif strict and key in value:
            raise ValueError(f"autoReport.{key} must be boolean")
    plugins = value.get("plugins", {})
    if not isinstance(plugins, dict):
        if strict:
            raise ValueError("autoReport.plugins must be an object")
        plugins = {}
    for pid, enabled in plugins.items():
        if isinstance(pid, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", pid) and type(enabled) is bool:
            result["plugins"][pid] = enabled
        elif strict:
            raise ValueError("Invalid autoReport plugin override")
    return result


def plugin_sources():
    from packages.plugins.catalog import Catalog
    if manifest_provider is None:
        from .config import load_config, DEFAULT_PLUGIN_MANIFESTS
        from .manifest_loader import load_manifests
        manifest = load_manifests(load_config().get("plugin_manifests", DEFAULT_PLUGIN_MANIFESTS))
    else:
        manifest = manifest_provider()
    return [{"id": p.id, "label": p.display_name,
             "defaultEnabled": p.message_source.get("auto_report_default", False),
             "legacySources": list(p.message_source.get("legacy_sources", ())) }
            for p in Catalog(manifest).plugins.values()
            if p.message_source is not None]


def origin(item, plugins):
    source = item.get("source") or (item.get("type") if item.get("type") != "task" else None)
    # Core report/system identity cannot be shadowed by plugin legacy aliases.
    if (source == "report" or item.get("type") in {"report", "zombie"}
            or (source is None and item.get("type") is None
                and "result" in item and "text" not in item)):
        return {"kind": "report"}
    if item.get("noticeKind") == "background_job_terminal":
        return {"kind": "system"}
    pid = item.get("sourcePluginId")
    if pid:
        return {"kind": "plugin", "pluginId": pid}
    for plugin in plugins:
        if source in plugin["legacySources"]:
            return {"kind": "plugin", "pluginId": plugin["id"]}
    if source in {"system", "automation"}:
        return {"kind": "system"}
    if source in {"agent", "meta-agent"}:
        return {"kind": "ma"}
    if source in {None, "user", "native-tui"}:
        return {"kind": "user"}
    return {"kind": "unknown"}


def admission(items, settings, plugins):
    """Any allowed non-report input enables a mixed batch. Reports never echo.

    Persist each decision with the reserved item. Recovery reuses that decision;
    later setting/manifest/queue/history changes cannot reclassify this run.
    """
    policy = normalize(settings)
    defaults = {p["id"]: p["defaultEnabled"] for p in plugins}
    decisions = []
    for item in items:
        decision = item.get("autoReportAdmission")
        if not isinstance(decision, dict) or decision.get("version") != 1:
            provenance = origin(item, plugins)
            kind = provenance["kind"]
            enabled = (policy["plugins"].get(provenance["pluginId"], defaults.get(provenance["pluginId"], False))
                       if kind == "plugin" else policy.get(kind, False))
            decision = {"version": 1, "origin": provenance, "enabled": enabled is True}
            item["autoReportAdmission"] = decision
        decisions.append(copy.deepcopy(decision))
    return {"version": 1, "inputs": decisions,
            "enabled": any(d.get("enabled") is True and d.get("origin", {}).get("kind") != "report"
                           for d in decisions)}
