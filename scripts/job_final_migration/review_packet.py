"""P9C private human review packet, built only from a P9B isolated snapshot.

Never calls migrate, Mapper, a runtime store or a legacy reader. Raw identities
and paths belong in an ignored private output directory, not in Git evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import html
import io
import json
from pathlib import Path

from packages.jobs import final_contracts as fc
from scripts.p9_practical_rehearsal import digest_file
from .apply import REVIEW_DRAFT_SCHEMA, REVIEW_PACKET_SCHEMA, _inputs, _source, outside_root, plan_digest
from .inventory import build_inventory, is_within_root
from .mapping import require

PACKET_SCHEMA = REVIEW_PACKET_SCHEMA
DECISIONS = [
    {"id": "D1", "question": "Which reviewed simple IDs are in scope, and who signs for the data owner?",
     "default": "none approved; candidate selection is not authorization",
     "impact": "An accountable owner must approve exact ID/hash/selection and fixed Action/provider/policies; later edits require a new digest.",
     "evidenceRequired": "named signer role, current grants, Action pins, per-ID policy evidence; trusted verification contract still to be designed"},
    {"id": "D2", "question": "Which complex IDs may remain historical permanently, and which require a new canonical definition?",
     "default": "preserve all bytes; no conversion or deletion",
     "impact": "Permanent history can permit legacy host retirement only after an independent nonexecuting reader preserves original IDs/logs/receipts. Conversion is separate implementation and acceptance.",
     "evidenceRequired": "per-ID choice, retention/pins and reader acceptance; no blanket acceptance of active/uncertain records"},
    {"id": "D3", "question": "Who owns unresolved resource/effect/notice and consumed-schedule reconciliation?",
     "default": "unknown remains uncertain; no retry, resend or receipt fabrication",
     "impact": "An assigned business owner must reconcile original keys and counters before recreation; historical archiving alone cannot settle effects or authorize R6.",
     "evidenceRequired": "per-ID original effect/dispatch/notice receipts, resource ownership and schedule/counter decision; operational actions require a separate authorized task"},
]


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _csv(rows):
    if not rows:
        return b""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        cells = {k: json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                 if isinstance(v, (dict, list)) else "" if v is None else str(v) for k, v in row.items()}
        # Treat untrusted source strings as spreadsheet text, never formulas.
        writer.writerow({k: "'" + v if v.startswith(("=", "+", "-", "@", "\t", "\r")) else v
                         for k, v in cells.items()})
    return stream.getvalue().encode("utf-8")


def _cell(value):
    return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def _proposal(item, source):
    """Mirror only the mapper's deterministic ID formula, not its definitions."""
    typ = item["type"]
    if typ == "MIG-TEMPLATE":
        kinds = ["template"]
    else:
        kinds = ["job"]
        if typ == "MIG-TRIGGER":
            kinds.append("trigger")
        elif typ in {"MIG-MSG", "MIG-SCHED"} or source.get("config", {}).get("schedule"):
            kinds.extend(["creator", "trigger"])
    suffix = item["migrationId"].removeprefix("mig1_")
    return {"proposedTargetIds": [f"{kind}_{suffix}" for kind in kinds],
            "allocationStatus": "proposal-only; not allocated", "fieldMap": item["fieldMap"],
            "canonicalState": "draft" if typ == "MIG-TEMPLATE" else "disabled",
            "actionBindingRequired": ["process.exec"] if typ == "MIG-BG" else ["review mapper-specific pinned Action/provider refs"],
            "semanticChanges": item["expectedSemanticDiff"]}


def _read_dependencies(item, source):
    paths = [{"sourcePath": item["sourceFile"],
              "reader": "packages/core/background_jobs.py:get/list_jobs (static dependency; _root creates directories)",
              "replacement": "independent raw JSON historical reader; never call legacy reader on source"}]
    if item["sourceFile"].startswith("scheduler/tasks/"):
        paths[0]["reader"] = "scheduler legacy task JSON; original schema reader must be retained/replaced"
    elif item["sourceFile"].startswith("automation/"):
        paths[0]["reader"] = "automation legacy registry JSON; original schema reader must be retained/replaced"
    if source.get("logPath"):
        paths.append({"sourcePath": source["logPath"], "reader": "raw log referenced by legacy record",
                      "replacement": "preserve link identity; root-external paths not opened or verified"})
    paths.append({"sourcePath": "background_jobs/runs.jsonl",
                  "reader": "packages/core/background_jobs.py:list_run_records -> final_runtime/coordinator.py:read_legacy_history",
                  "replacement": "original-key ledger index; record-specific linkage unverified"})
    if source.get("targetSessionId") or source.get("targetSessionIds"):
        paths.append({"sourcePath": "sessions/<referenced ID>.json/history",
                      "reader": "Session history/notice receipts by original identity",
                      "replacement": "retain referenced Session history and outbox; no resend"})
    return paths


def _disposition(item, source):
    reasons = set(item["reasonCodes"])
    unresolved = bool(reasons & {"active_resource", "uncertain_effect", "pending_notice", "unverified_authority"})
    if item["classificationDetail"] == "unknown" or unresolved:
        suggestion = "需业务决策"
        detail = "Retain original bytes in quarantine; assign original-key reconciliation. Never infer success, retry or resend."
    elif reasons & {"schedule_semantic_loss", "counter_coupling", "business_completion_coupling", "missing_reference"}:
        suggestion = "转换（待单独批准）"
        detail = "Retain history; a new canonical definition needs reviewed schedule/counter/reference semantics and separate implementation."
    else:
        suggestion = "保留（待人类确认）"
        detail = "Candidate for permanent nonexecuting historical retention; no canonical Run or executable definition is fabricated."
    if reasons & {"schedule_semantic_loss", "counter_coupling", "business_completion_coupling"}:
        detail += " Any future conversion requires explicit schedule, consumed-count and business-completion semantics; no counter reset."
    return {"historicalPreserve": "yes: byte archive only; safe human display and receipt settlement not established",
            "historicalExecutionAllowed": False, "suggestion": suggestion, "suggestionDetail": detail,
            "oldReadDependencies": _read_dependencies(item, source),
            "r6Blocker": "legacy identity retained; independent nonexecuting history/receipt reader and owner retirement not accepted"
                         + ("; unresolved resource/effect/notice/authority requires reconciliation" if unresolved else ""),
            "decisionIds": ["D2", "D3"] if unresolved or item["classificationDetail"] == "unknown" else ["D2"]}


def build_packet(evidence, output, *, expected_manifest_sha256):
    evidence = Path(evidence).resolve(strict=True)
    root = (evidence / "source-copy").resolve(strict=True)
    output = outside_root(root, output)
    require(not output.is_relative_to(evidence) and not evidence.is_relative_to(output), "output must be separate from P9B evidence")
    require(not output.exists(), "packet output already exists; use a new private directory")
    manifest_path = evidence / "snapshot-manifest.json"
    require(digest_file(manifest_path) == expected_manifest_sha256, "snapshot manifest hash mismatch")
    captured = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(not any(r["state"].startswith("blocked") for r in captured), "snapshot contains blocked captures")
    checked = {}
    for row in captured:
        if row["kind"] != "file" or not row["state"].startswith("stable"):
            continue
        path = root / row["path"]
        require(is_within_root(root, path) and not path.is_symlink()
                and not any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                            for p in (path, *path.parents)), "snapshot path escapes root or contains a link")
        require(digest_file(path) == row["sha256"], f"snapshot file hash mismatch: {row['path']}")
        checked[row["path"]] = row["sha256"]
    inventory = build_inventory(root, allow_real_profile=True)
    cached = json.loads((evidence / "wp08-inventory.json").read_text(encoding="utf-8"))
    require(fc.digest(inventory) == fc.digest(cached), "inventory drift since P9B")
    sources = _inputs(root)
    require(all(name in checked and hashlib.sha256(raw).hexdigest() == checked[name]
                for name, raw in sources.items()), "unmanifested or changed WP08 input")
    archive_hash = digest_file(evidence / "preservation-bundle-manifest.json")
    identity = {"sourceManifestSha256": expected_manifest_sha256, "inventoryDigest": fc.digest(inventory),
                "preservationEnvelopeSha256": archive_hash, "canonicalBundleProduced": False,
                "canonicalBundleDigest": None,
                "proposedOperationId": "mop1_" + fc.digest({"root": str(root), "mappingVersion": "1"})[:24]}
    packet_id = "review1_" + fc.digest(identity)[:24]
    rows, simple, preserved = [], [], []
    for item in sorted(inventory["instances"], key=lambda i: i["migrationId"]):
        try:
            parsed = _source(item, sources)
        except (ValueError, KeyError, TypeError):
            if item["classificationDetail"] != "unknown":
                raise
            parsed = None
        source = parsed if isinstance(parsed, dict) else {}
        # P9B inventory's per-file recordDigest may contain canonical JSON
        # text rather than a digest. Never label that text as a SHA-256.
        record_sha = ("sha256:" + hashlib.sha256(json.dumps(parsed, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")).hexdigest()
                      if parsed is not None else item["originalDigest"])
        refs = {k: source[k] for k in ("targetSessionId", "targetSessionIds", "creatorSessionId", "sourceSessionId",
                "logPath", "terminalEventId", "scheduledParentJobId", "dispatchKey") if k in source}
        common = {"id": item["id"], "kind": item["sourceKind"], "migrationType": item["type"],
                  "migrationId": item["migrationId"], "sourcePath": item["sourceFile"],
                  "recordSha256": record_sha, "sourceFileSha256": item["originalDigest"],
                  "recordSha256Basis": "canonical-json" if parsed is not None else "raw-source-bytes; record unparsed",
                  "packetId": packet_id, "bundleIdentity": identity, "classification": item["classificationDetail"],
                  "observedStatus": source.get("status", "unparsed/unknown"), "targetIds": [],
                  "references": {"inventoryEdges": item["referenceEdges"], "observedOriginalKeys": refs,
                                 "verification": "not runtime-authorized; external paths not followed"}}
        if item["classificationDetail"] == "simple":
            row = {**common, "proposedCanonicalMapping": _proposal(item, source),
                   "risksAndUnknowns": ["shape-only; no authorization", "old owner/resource fence unverified",
                       "Action/provider digest and current grants missing", "retention/pins/notice policy requires evidence",
                       "history is evidence only; no canonical Run", "cross-store consistency not established"],
                   "decisionIds": ["D1"]}
            simple.append(row)
        else:
            row = {**common, "reasonCodes": item["reasonCodes"], "whyComplexOrUnknown": item["rejectionReasons"],
                   **_disposition(item, source)}
            preserved.append(row)
        rows.append(row)
    selection = [r["migrationId"] for r in simple]
    review = {"schema": PACKET_SCHEMA, "packetId": packet_id, "status": "draft_unapproved",
              "reviewPayloadDigest": fc.digest({"records": rows, "decisions": DECISIONS}),
              "signer": None, "signature": None, "verification": "unsupported; packet is nonexecutable"}
    context = {"schemaVersion": 1, "profileId": None, "actions": {}, "reviewManifest": review,
               "partialPolicy": {"version": 1, "mode": "preserve-incompatible"},
               "selectedMigrationIds": selection, "records": {r["migrationId"]: {
                   "originalDigest": r["sourceFileSha256"], "recordDigest": r["recordSha256"],
                   "principal": None, "owner": None, "authorizationEvidence": None,
                   "oldOwnerStopped": None, "oldOwnerEpoch": None, "fenceEvidence": None, "importedAt": None,
                   "policies": {key: None for key in ("overlap", "recovery", "notification", "retention")}}
                   for r in simple}}
    draft = {"schema": REVIEW_DRAFT_SCHEMA, "status": "draft_unapproved", "reviewManifest": review,
             "planDigest": plan_digest(inventory, context), "planDigestScope": "exact nested context; includes draft review metadata; never an approval",
             "context": context, "approvedSelection": [], "active_ids": [], "executable": False}
    counts = {"total": len(rows), "simple": len(simple), "complex": sum(r["classification"] == "complex" for r in preserved),
              "unknown": sum(r["classification"] == "unknown" for r in preserved),
              "recommendations": dict(Counter(r["suggestion"] for r in preserved)),
              "reasons": dict(Counter(c for r in preserved for c in r["reasonCodes"])),
              "filesHashVerified": len(checked)}
    md = ["# P9C 人类审阅包 — draft_unapproved", "", f"packetId: `{packet_id}`", "",
          "此包没有签署或授予权限，没有 canonical bundle、导入、转换、删除或运行激活。所有 targetIds 为空；拟 ID 仅供审阅。",
          "recordSha256 对可解析 JSON 重新规范化并计算 SHA；无法解析时使用 raw bytes SHA 并标明 basis。sourceFileSha256 始终是原文件 bytes SHA。",
          "旧 inventory 的 recordDigest 在逐文件记录中可能是 JSON 文本，不能直接当作 hash。", "",
          f"集合：{len(simple)} simple，{counts['complex']} complex，{counts['unknown']} unknown。planDigest：`{draft['planDigest']}`。", "",
          "旧读路径来自静态源码检查；本工具只直接读隔离文件。旧 get/list 的 _root 可创建目录，不能在真实源上调用。",
          "以下建议未获批准。historical preserve 仅证明原 bytes 可归档，不代表可安全展示、未知效果已结算或 R6 可放行。", "",
          "## 最少决策（3 组，允许逐 ID 分组回答）", ""]
    for decision in DECISIONS:
        md.extend([f"### {decision['id']}: {decision['question']}", "", decision["impact"], "",
                   "默认：" + decision["default"], "", "需要证据：" + decision["evidenceRequired"], ""])
    md.extend(["## Simple 审阅索引", "", "| ID | kind | 来源 | record SHA-256 | 拟 canonical ID |",
               "|---|---|---|---|---|"])
    for r in simple:
        md.append("| " + " | ".join(_cell(v) for v in [r["id"], r["kind"], r["sourcePath"], r["recordSha256"],
                  ", ".join(r["proposedCanonicalMapping"]["proposedTargetIds"])]) + " |")
    md.extend(["", "## Complex/unknown 逐 ID 建议", "", "完整原因、旧读路径、原键与 R6 阻断见 preserved-review.csv 和 records.json。", "",
               "| ID | 分类 | 原因 | 建议 | 决策 |", "|---|---|---|---|---|"])
    for r in preserved:
        md.append("| " + " | ".join(_cell(v) for v in [r["id"], r["classification"], "; ".join(r["whyComplexOrUnknown"]),
                  r["suggestion"], ", ".join(r["decisionIds"])]) + " |")
    files = {"simple-review.csv": _csv(simple), "preserved-review.csv": _csv(preserved), "records.json": _json(rows),
             "reviewed-context.draft.json": _json(draft), "decisions.json": _json(DECISIONS),
             "REVIEW.md": ("\n".join(md) + "\n").encode("utf-8")}
    manifest = {"schema": PACKET_SCHEMA, "packetId": packet_id, "status": "draft_unapproved", "executable": False,
                "bundleIdentity": identity, "counts": counts, "draftPlanDigest": draft["planDigest"],
                "signature": None, "approvedSelection": [], "active_ids": [], "canonicalTargetCreated": False,
                "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}}
    manifest["packetDigest"] = fc.digest(manifest)
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        (output / name).write_bytes(raw)
    # Manifest is written last; an interrupted review packet is never executable.
    (output / "packet-manifest.json").write_bytes(_json(manifest))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True, help="existing P9B private evidence directory")
    parser.add_argument("--out", type=Path, required=True, help="new private review packet directory")
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    manifest = build_packet(args.evidence, args.out, expected_manifest_sha256=args.expected_manifest_sha256)
    print(json.dumps({"packetId": manifest["packetId"], "packetDigest": manifest["packetDigest"], "counts": manifest["counts"]}))
