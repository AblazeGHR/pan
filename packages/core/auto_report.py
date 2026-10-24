"""Automatic answer reports: explicit origins and immutable admission decisions.

This policy never governs manual sends, output publication or terminal logging.
Plugin message capability is independent of tools, panels and subscription transport.

A second, independent policy decides whether an automatically reported answer
may carry the first ``PROMPT_SUMMARY_CHARS`` Unicode characters of the input
that started the run.  It shares the same origins (system/ma/user/plugin), but
every switch defaults to false and a plugin's ``auto_report_default`` never
enables it.
"""
from __future__ import annotations

import copy
import re

FLAGS = ("system", "ma", "user")
PLUGIN_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}")
PROMPT_SUMMARY_CHARS = 100

SUMMARY_DEFAULTS = {"system": False, "ma": False, "user": False, "plugins": {}}
DEFAULTS = {"system": False, "ma": True, "user": False, "plugins": {},
            "summary": copy.deepcopy(SUMMARY_DEFAULTS)}
manifest_provider = None


def _normalize_flags(value, defaults, *, strict, label):
    if not isinstance(value, dict):
        if strict:
            raise ValueError(f"{label} must be an object")
        return copy.deepcopy(defaults)
    if strict and set(value) - set(defaults):
        raise ValueError(f"Unknown {label} field")
    result = copy.deepcopy(defaults)
    for key in FLAGS:
        if type(value.get(key)) is bool:
            result[key] = value[key]
        elif strict and key in value:
            raise ValueError(f"{label}.{key} must be boolean")
    plugins = value.get("plugins", {})
    if not isinstance(plugins, dict):
        if strict:
            raise ValueError(f"{label}.plugins must be an object")
        plugins = {}
    for pid, enabled in plugins.items():
        if isinstance(pid, str) and PLUGIN_ID.fullmatch(pid) and type(enabled) is bool:
            result["plugins"][pid] = enabled
        elif strict:
            raise ValueError(f"Invalid {label} plugin override")
    return result


def normalize_summary(value, *, strict=False):
    return _normalize_flags(value, SUMMARY_DEFAULTS, strict=strict,
                            label="autoReport.summary")


def normalize(value, *, strict=False):
    if not isinstance(value, dict):
        if strict:
            raise ValueError("autoReport must be an object")
        return copy.deepcopy(DEFAULTS)
    if strict and set(value) - set(DEFAULTS):
        raise ValueError("Unknown autoReport field")
    result = _normalize_flags(value, DEFAULTS, strict=strict, label="autoReport")
    # ``summary`` is an independent policy with its own all-false defaults.
    # An omitted member keeps that policy's defaults under strict PUT too.
    result["summary"] = (normalize_summary(value["summary"], strict=strict)
                         if "summary" in value
                         else copy.deepcopy(SUMMARY_DEFAULTS))
    return result


def summary_excerpt(text):
    """First ``PROMPT_SUMMARY_CHARS`` Unicode characters, verbatim.

    No model call, no whitespace rewriting and no added suffix: the excerpt is
    always an exact prefix of the same input body its messageId identifies.
    """
    if not isinstance(text, str) or not text:
        return None
    return text[:PROMPT_SUMMARY_CHARS]


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


def admission(items, settings, plugins, summary=None):
    """Any allowed non-report input enables a mixed batch. Reports never echo.

    Persist each decision with the reserved item. Recovery reuses that decision;
    later setting/manifest/queue/history changes cannot reclassify this run.

    The answer-report and prompt-summary switches are frozen together but stay
    independent: a run may report without a summary, and a summary switch never
    makes a run report.  A plugin summary override never inherits
    ``auto_report_default``; omitting it means false.
    """
    policy = normalize(settings)
    if summary is None and isinstance(settings, dict):
        summary = settings.get("summary")
    summary_policy = normalize_summary(summary)
    defaults = {p["id"]: p["defaultEnabled"] for p in plugins}
    decisions = []
    for item in items:
        decision = item.get("autoReportAdmission")
        if not isinstance(decision, dict) or decision.get("version") != 1:
            provenance = origin(item, plugins)
            kind = provenance["kind"]
            if kind == "plugin":
                pid = provenance["pluginId"]
                enabled = policy["plugins"].get(pid, defaults.get(pid, False))
                with_summary = summary_policy["plugins"].get(pid, False)
            else:
                enabled = policy.get(kind, False)
                with_summary = summary_policy.get(kind, False)
            decision = {"version": 1, "origin": provenance, "enabled": enabled is True,
                        "summary": with_summary is True}
            item["autoReportAdmission"] = decision
        decisions.append(copy.deepcopy(decision))
    return {"version": 1, "inputs": decisions,
            "enabled": any(d.get("enabled") is True and d.get("origin", {}).get("kind") != "report"
                           for d in decisions)}


async def context():
    """Load registry/config off-loop and retire IO before cancellation escapes."""
    from packages.plugins.lifecycle import io
    from .config import load_config
    def read():
        policy = normalize((load_config().get("ui") or {}).get("autoReport"))
        return policy, plugin_sources(), policy["summary"]
    return await io(read)
