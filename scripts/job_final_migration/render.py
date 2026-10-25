"""Deterministic JSON and Markdown renderers for the read-only inventory."""

from __future__ import annotations

import json
from typing import Any


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return text or "-"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    root = report["root"]
    lines = [
        "# Job final migration inventory",
        "",
        ("Offline apply committed; canonical objects remain disabled. Source root "
         "unchanged; runtime fencing and activation are not verified."
         if report["migrationExecuted"] else
         "Read-only inventory: no migration was executed, no target store was "
         "written, and no service or process was started."),
        "",
        f"- Root: `{root['path']}`",
        f"- Real profile: {_yes_no(root['realProfile'])}"
        f" (markers: {', '.join(root['realProfileMarkers']) or 'none'})",
        f"- `--allow-real-profile`: {_yes_no(root['allowRealProfile'])}",
        f"- Mapping version: {report['mappingVersion']}",
        f"- Schema whitelist: {report['schemaWhitelist']}",
        f"- Inventory digest: `{report.get('inventoryDigest', '-')}`",
        f"- Plan digest: `{report.get('planDigest', '-')}`",
        f"- Target writes: {report['targetWrites']}",
        "",
        "## Summary",
        "",
        "| Total | Simple | Complex | Unknown | Scan errors |",
        "|---:|---:|---:|---:|---:|",
        f"| {summary['total']} | {summary['simple']} | {summary['complex']} | "
        f"{summary['unknown']} | {summary['scanErrors']} |",
        "",
        "| Source type | Total | Simple | Complex | Unknown |",
        "|---|---:|---:|---:|---:|",
    ]
    for source_type, counts in sorted(summary["bySourceType"].items()):
        lines.append(
            f"| {_cell(source_type)} | {counts['total']} | {counts['simple']} | "
            f"{counts['complex']} | {counts['unknown']} |"
        )
    lines.extend(["", "## Instances", ""])
    for item in report["instances"]:
        lines.extend([
            f"### {_cell(item['migrationId'])} — {_cell(item['id'])}",
            "",
            f"- Type: {_cell(item['type'])} (`{_cell(item['sourceKind'])}`)",
            f"- Classification: {_cell(item['classification'])}"
            f" ({_cell(item['classificationDetail'])})",
            f"- Owner: {_cell(item['owner'].get('kind'))}"
            f" / {_cell(item['owner'].get('id'))}",
            f"- Authorization evidence: {_cell(item['authEvidence'])}",
            f"- Source file: `{item['sourceFile']}`",
            f"- SHA-256: `{item['sha256'] or '-'}`",
            f"- Schema fingerprint: `{item['schemaFingerprint'] or '-'}`",
            f"- Suggested action: {_cell(item['suggestedAction'])}",
        ])
        if item["rejectionReasons"]:
            lines.append("- Rejection reasons:")
            lines.extend(f"  - {_cell(reason)}"
                         for reason in item["rejectionReasons"])
        else:
            lines.append("- Rejection reasons: none")
        if item["target"]:
            target = json.dumps(item["target"], ensure_ascii=False, sort_keys=True)
            lines.append(f"- Target: `{_cell(target)}`")
        lines.append(f"- Cursor: `{json.dumps(item['cursor'], ensure_ascii=False, sort_keys=True)}`")
        if item["activeEffectHandles"]:
            lines.append("- Active effect handles: "
                         + ", ".join(_cell(value) for value in item["activeEffectHandles"]))
        if item["noticeReceipts"]:
            lines.append("- Notice receipts: "
                         + ", ".join(_cell(value) for value in item["noticeReceipts"]))
        if item["fieldMap"]:
            lines.append("- Field map:")
            lines.extend(f"  - `{_cell(source)}` → `{_cell(target)}`"
                         for source, target in item["fieldMap"].items())
        if item["expectedSemanticDiff"]:
            lines.append("- Expected semantic diff:")
            lines.extend(f"  - {_cell(value)}" for value in item["expectedSemanticDiff"])
        lines.append("")

    lines.extend([
        "## Plan (offline apply committed)" if report["migrationExecuted"] else "## Plan (not executed)",
        "",
        f"- Executable: {_yes_no(report['plan']['executable'])}",
        f"- Target ID allocation: {report['plan']['targetIdAllocation']}",
        f"- Planned simple items: {len(report['plan']['items'])}",
        f"- Preserved items: {len(report['plan']['preserved'])}",
        "",
    ])
    if report["plan"]["items"]:
        lines.extend([
            "### Planned simple items",
            "",
            "| Migration ID | Original ID | Type | Source file | SHA-256 |",
            "|---|---|---|---|---|",
        ])
        for item in report["plan"]["items"]:
            digest = (item["originalDigest"] or "").split(":", 1)[-1]
            lines.append(
                f"| {_cell(item['migrationId'])} | {_cell(item['originalId'])} | "
                f"{_cell(item['type'])} | `{item['sourceFile']}` | `{digest}` |"
            )
        lines.append("")
    if report["plan"]["preserved"]:
        lines.extend([
            "### Preserved items",
            "",
            "| Migration ID | Original ID | Type | Reason codes |",
            "|---|---|---|---|",
        ])
        for item in report["plan"]["preserved"]:
            lines.append(
                f"| {_cell(item['migrationId'])} | {_cell(item['originalId'])} | "
                f"{_cell(item['type'])} | {_cell(', '.join(item['reasonCodes']))} |"
            )
        lines.append("")

    lines.extend(["## Scan errors", ""])
    if report["scanErrors"]:
        lines.extend([
            "| Code | Path | Detail |",
            "|---|---|---|",
        ])
        for error in report["scanErrors"]:
            lines.append(
                f"| {_cell(error['code'])} | `{_cell(error['path'])}` | "
                f"{_cell(error['detail'])} |"
            )
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)
