"""P9D context preparation and manual deletion documents, never deletion."""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import definition
from packages.jobs.migration_authorization import USER, record_instruction
from .apply import _inputs, _source
from .inventory import build_inventory
from .mapping import require
from .review_packet import _csv, _json


def prepare(service, packet_directory, *, instruction, reference, recorded_at, original_root):
    packet_directory = Path(packet_directory)
    manifest = json.loads((packet_directory / "packet-manifest.json").read_text(encoding="utf-8"))
    sealed = {k: v for k, v in manifest.items() if k != "packetDigest"}
    require(fc.digest(sealed) == manifest["packetDigest"], "P9C packet digest mismatch")
    for name, sha in manifest["files"].items():
        require(hashlib.sha256((packet_directory / name).read_bytes()).hexdigest() == sha, "P9C packet artifact changed")
    rows = json.loads((packet_directory / "records.json").read_text(encoding="utf-8"))
    reviewed = {r["migrationId"]: r for r in rows if r["classification"] == "simple"}
    root = service.root.parent
    inventory = build_inventory(root, allow_real_profile=True)
    simple = [i for i in inventory["instances"] if i["classificationDetail"] == "simple"]
    require(set(reviewed) == {i["migrationId"] for i in simple} and all(i["type"] == "MIG-BG" for i in simple),
            "P9D requires the exact reviewed all-simple background process selection")
    sources = _inputs(root)
    action = next(a for a in service.actions(actor=USER)["items"] if a["id"] == "product_process")
    binding = {"object": action, "ref": {"id": action["id"], "revision": action["revision"],
                "digest": fc.digest(definition("action", action))}, "completions": {"ordinary": {"contract": "process-exit"}}}
    status = service.owner.status()
    require(status is not None and status["state"] == "closed" and status["epoch"] >= 1,
            "Owned isolated profile must be fenced before context preparation")
    pins_path = root / "background_jobs/pins.json"
    old_pins = fc.loads(pins_path.read_text()) if pins_path.exists() else {}
    require(isinstance(old_pins, dict), "Unknown source pins format")
    context = {"schemaVersion": 1, "profileId": service.profile_id, "actions": {"process.exec": binding},
        "records": {}, "selectedMigrationIds": sorted(reviewed), "partialPolicy": {"version": 1, "mode": "preserve-incompatible"},
        "isolation": {"mode": "owned-offline-copy", "profileRoot": str(root), "fenceEpoch": status["epoch"],
                      "scope": "this new isolated copy only; no real profile/process/port claim"},
        "preservedDisposition": "unrecognized", "historyLogs": {},
        "importPolicyBasis": "new disabled manual-only import policy: no recovery, no notices, retain/pin history; not a claim about legacy policy"}
    original_prefix = original_root.replace("\\", "/").rstrip("/") + "/"
    for item in simple:
        mid = item["migrationId"]
        source = _source(item, sources)
        require(item["originalDigest"] == reviewed[mid]["sourceFileSha256"]
                and "sha256:" + fc.digest(source) == reviewed[mid]["recordSha256"], "Reviewed per-ID source hash changed")
        policies = {"overlap": {"mode": "forbid", "maxActive": 1, "maxPending": 0},
            "recovery": {"automatic": False, "maxAttempts": 1, "backoffSeconds": ["5"], "stableResetSeconds": "300"},
            "notification": {"on": [], "recipients": []},
            "retention": {"policyRef": "p9d_preserve_original_history", "pinned": True}}
        context["records"][mid] = {"originalDigest": item["originalDigest"], "recordDigest": reviewed[mid]["recordSha256"],
            "principal": copy.deepcopy(USER), "owner": copy.deepcopy(USER), "oldOwnerStopped": True,
            "oldOwnerEpoch": status["epoch"], "fenceEvidence": {"profileRoot": str(root), "epoch": status["epoch"],
                "state": "closed", "basis": "new owned isolated copy, no legacy host started; actual local OwnerFence established"},
            "authorizationEvidence": reference, "importedAt": recorded_at, "policies": policies,
            "originalPin": old_pins.get(item["id"], False)}
        log = source.get("logPath", "").replace("\\", "/")
        require(log.casefold().startswith(original_prefix.casefold()), "Historical log outside declared original data root")
        relative = log[len(original_prefix):]
        require(".." not in PurePosixPath(relative).parts and relative.startswith("background_jobs/logs/"), "Unsafe log reference")
        path = root / relative
        require(path.resolve().is_relative_to(root) and path.is_file() and not path.is_symlink(), "Archived log missing")
        context["historyLogs"][mid] = {"relativePath": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    receipt = record_instruction(instruction=instruction, reference=reference, recorded_at=recorded_at,
                                 packet_digest=manifest["packetDigest"], context=context)
    context["userAuthorizationDigest"] = receipt["receiptDigest"]
    return inventory, context, receipt


def manual_deletion_packet(packet_directory, output, *, original_root):
    """Write inert CSV/Markdown/text commands. Never opens original_root."""
    rows = json.loads((Path(packet_directory) / "records.json").read_text(encoding="utf-8"))
    result, commands = [], []
    for row in rows:
        if row["classification"] == "simple":
            continue
        rel = PurePosixPath(row["sourcePath"])
        require(not rel.is_absolute() and ".." not in rel.parts, "Unsafe manual deletion path")
        full = str(PurePosixPath(original_root.replace("\\", "/")) / rel)
        # Only an individually stored Job gets a whole-file deletion command.
        single = rel.parts[:2] == ("background_jobs", "jobs") and rel.suffix == ".json"
        literal = "'" + full.replace("'", "''") + "'"
        command = f"Remove-Item -LiteralPath {literal} -Confirm" if single else "NO WHOLE-FILE DELETE: shared registry requires a separately reviewed row edit"
        deps = {"oldReadDependencies": row["oldReadDependencies"], "references": row["references"],
                "checks": ["independent original-ID/raw/log history remains readable and backed up", "pins/retention/legal hold allow removal",
                    "no live resource/partial/unknown effect is implicitly treated as settled", "original notice/dispatch/handoff keys and shared Session/Locker references are retained",
                    "review all shared references; never cascade-delete logs, sessions, ledgers or attachments", "verify exact literal path, no link/junction, and current SHA matches"]}
        result.append({"id": row["id"], "migrationId": row["migrationId"], "type": row["kind"],
            "recognition": "unrecognized", "reasonCodes": row["reasonCodes"], "reasons": row["whyComplexOrUnknown"],
            "originalLocation": full, "sourceFileSha256": row["sourceFileSha256"], "recordSha256": row["recordSha256"],
            "dependenciesBeforeDelete": deps, "manualCommand": command, "automaticDelete": False})
        commands.extend([f"# {row['id'] or row['migrationId']} SHA {row['sourceFileSha256']}",
            f"Get-Item -LiteralPath {literal} | Select-Object FullName,Attributes,LinkType,Target",
            f"Get-FileHash -LiteralPath {literal} -Algorithm SHA256", command, ""])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "manual-delete.csv").write_bytes(_csv(result))
    (output / "manual-delete.json").write_bytes(_json(result))
    (output / "manual-delete-commands.txt").write_text("\n".join(commands), encoding="utf-8")
    guide = """# unrecognized 手动删除清单（未执行删除）

用户已决定全部 complex/unknown 保留并标记 unrecognized，包含此前 21 项和 4 条消息计划；不再等待逐项业务选择。
本清单仅提供未来人工删除入口，不授予本任务删除、结算、kill、重投或服务操作权限。

1. 先打开 manual-delete.csv/JSON，按单个 ID 审阅类型、原因、SHA 和 dependenciesBeforeDelete。
2. 在另一个已授权的人工维护任务中，核查该 ID 的原资源归属、原键收据、pins/retention、共享 Session/Locker/handoff 引用。
   unknown/partial 没有因归档而变成功；不确认效果、不重投、不清零已消费计数。不要级联删除共享日志或 Session。
3. 确认独立历史读取与备份可用。只复制 commands.txt 中该 ID 的 Get-Item/Get-FileHash 命令，核对完整绝对路径、
   Attributes 不含 ReparsePoint、没有 symlink/junction、父目录仍在指定数据根；SHA 必须与清单完全相同。
4. 需要时先将该 ID 的 Remove-Item 命令追加 -WhatIf 预览。实际删除只运行该单条 -LiteralPath/-Confirm 命令，
   不使用 -Recurse、通配符、批量循环，不删除整个 registry、ledger、目录或 owner lock。hash/引用变化时停止并重新审阅。
5. 删除后记录操作者、时间、ID、删除前 SHA，验证备份与原键查询仍可读；本任务未验证人工删除后的真实环境行为。

commands.txt 是惰性文本，生成器没有打开真实源、执行任何命令或调用 Remove-Item。
"""
    (output / "MANUAL_DELETE.md").write_text(guide, encoding="utf-8")
    return result
