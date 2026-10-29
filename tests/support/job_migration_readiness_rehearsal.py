"""Replayable synthetic OFFLINE drill. Never constructs/starts a runtime service.

Run through run_tests_isolated.py; --output must be a fresh owned directory.
Only test factories and a memory-only collector of current Action publications
are reused. All source records and authority attestations are synthetic.
"""
from __future__ import annotations

import argparse
import base64
import copy
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "tests")]

from packages.jobs import final_contracts as fc
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.snapshot import stable_copy
from scripts.job_final_migration.review_packet import build_packet
from test_job_final_contracts import object_document
from test_job_final_migration import (
    ACTION_ID, _action_job, _background_job, _make_root, _message_job,
    _scheduled_job, _write_json,
)
from test_job_final_migration_apply import context_for


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def current_actions():
    # Exercise the actual current publication method, without __init__, a DB,
    # OwnerFence, service.start, provider call, runtime import or activation.
    from packages.jobs.final_service import FinalService

    class Collector:
        executor = SimpleNamespace(channel_provider=object())

        def __init__(self):
            self.actions = {}

        def publish_action(self, key, definition):
            definition = fc.validate_definition("action", definition)
            self.actions[key] = object_document("action", definition, id=key, revision=1)

        def transaction(self, **kwargs):
            return nullcontext(None)

        def _get(self, db, kind, key):
            assert kind == "action"
            return self.actions[key]

    collector = Collector()
    FinalService._install_product_actions(collector)
    keys = {"process.exec": "product_process_logs_v1", "session.send": "product_session_message_v2",
            "session.send-many": "product_session_broadcast_v2", "session.assign": "product_assign_v2",
            "qq.send": "product_channel", ACTION_ID: "product_session_message_v2"}
    result = {}
    for key, product in keys.items():
        obj = collector.actions[product]
        fields = fc.schema_artifacts()["action_definition.schema.json"]["properties"]
        definition = {k: v for k, v in obj.items() if k in fields}
        result[key] = {"object": obj,
                       "ref": {"id": product, "revision": obj["revision"], "digest": fc.digest(definition)},
                       "completions": {"ordinary": {"contract": obj["defaultCompletion"]}}}
    return result


def fixture(output):
    root = _make_root(output)
    # Scheduler current one-shot, broadcast, and terminal history/cursor inputs.
    scheduled = _scheduled_job()
    _write_json(root / "background_jobs/jobs/job_current_schedule.json", scheduled)
    message = _message_job()
    message.update(jobId="job_broadcast", kind="session-broadcast",
                   targetSessionIds=["sess_a", "sess_b", "sess_a"],
                   targetStruct={"sessionIds": ["sess_a", "sess_b"]})
    message.pop("targetSessionId")
    _write_json(root / "background_jobs/jobs/job_broadcast.json", message)
    # Each incompatible identity remains in its original bytes/path.
    for label, patch in {
        "active": {"status": "running", "processTreeEnded": False},
        "uncertain": {"status": "uncertain", "processTreeEnded": False},
        "authority": {"creatorSessionId": None, "sourceStruct": {}, "sourceSessionId": None},
        "unknown_field": {"futureFlag": {"preserve": True}},
        "pending_notice": {"notificationState": "pending"},
    }.items():
        value = _background_job(root)
        value.update(jobId="job_" + label, **patch)
        _write_json(root / f"background_jobs/jobs/job_{label}.json", value)
    for feature in ("monitoring", "stable_reset", "target_clear", "fallback"):
        value = _action_job()
        value.update(jobId="job_" + feature, taskId="task_" + feature)
        if feature in {"monitoring", "stable_reset"}:
            value["config"]["recovery"].update(monitoring=True, enabled=feature == "stable_reset")
        else:
            value["config"]["arguments"][feature] = {"from": "literal", "value": True}
            # No legacy whitelist field silently becomes a new product option.
            value["config"][feature] = True
        _write_json(root / f"background_jobs/jobs/job_{feature}.json", value)
    for name, raw in {"corrupt": b'{broken-json', "duplicate": b'{"kind":"a","kind":"b"}',
                      "future": b'{"kind":"future","jobId":"job_future"}'}.items():
        (root / f"background_jobs/jobs/job_{name}.json").write_bytes(raw)
    (root / "background_jobs/runs.jsonl").write_text(
        json.dumps({"taskId": "historical_only", "runId": "run_original", "status": "completed"}) + "\n",
        encoding="utf-8")
    # Outside inventory's fixed allowlist; source guard must still conserve it.
    (root / "scheduler/cursors-unported.json").write_text('{"cursor":"opaque"}', encoding="utf-8")
    inventory = build_inventory(root)
    context = context_for(root)
    context["actions"] = current_actions()
    context.update(partialPolicy={"version": 1, "mode": "preserve-incompatible"},
                   selectedMigrationIds=sorted(i["migrationId"] for i in inventory["instances"]
                                               if i["classificationDetail"] == "simple"))
    return root, context


def cli(output, label, root, *args, expected=0):
    command = [sys.executable, "-X", "utf8", "-m", "scripts.job_final_migration", "--root", str(root),
               "--out", "-", *map(str, args)]
    process = subprocess.run(command, cwd=REPO, capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    _write_json(output / f"{label}.command.json", {"argv": command, "exit": process.returncode})
    (output / f"{label}.stderr.txt").write_text(process.stderr, encoding="utf-8")
    (output / f"{label}.stdout.json").write_text(process.stdout, encoding="utf-8")
    assert process.returncode == expected, (label, process.returncode, process.stderr)
    return json.loads(process.stdout) if expected == 0 else process.stderr


def crash_recovery(output, root, context_path, digest, allocations):
    results = []
    for boundary in ("before", "after"):
        target = output / ("crash-" + boundary) / "bundle.json"
        args = ["--root", str(root), "--out", "-", "--target", str(target), "--context", str(context_path),
                "--preserve-incompatible", "--apply", "--mark-read-only", "--expected-digest", digest]
        code = """
import os, sys
from pathlib import Path
from scripts.job_final_migration.cli import main
target, boundary = Path(sys.argv[1]), sys.argv[2]
replace = os.replace
def crash(src, dst):
    if Path(dst) == target:
        if boundary == 'after':
            replace(src, dst)
        os._exit(77)
    replace(src, dst)
os.replace = crash
main(sys.argv[3:])
raise AssertionError('Crash boundary was not reached')
"""
        command = [sys.executable, "-X", "utf8", "-c", code, str(target), boundary, *args]
        child = subprocess.run(command, cwd=REPO, capture_output=True, timeout=60)
        _write_json(output / ("crash-" + boundary + ".command.json"),
                    {"argv": command, "exit": child.returncode})
        assert child.returncode == 77, child.stderr
        assert target.exists() == (boundary == "after")
        if target.exists():
            assert migration.read_bundle(target)["allocations"] == allocations
        # The tool refuses the stale lock. The OS child exit receipt proves only
        # this owned injector is dead; no global lock/PID discovery or cleanup.
        locked = cli(output, "crash-" + boundary + "-locked", root, *args[4:], expected=1)
        assert "File exists" in locked
        lock = target.with_name("bundle.json.lock")
        assert lock.exists()
        lock.unlink()
        orphan_hashes = {}
        for orphan in target.parent.glob(".bundle.json.*.tmp"):
            orphan_hashes[orphan.name] = hashlib.sha256(orphan.read_bytes()).hexdigest()
            orphan.unlink()
        cli(output, "crash-" + boundary + "-recovered", root, *args[4:])
        assert migration.read_bundle(target)["allocations"] == allocations
        results.append({"boundary": boundary, "exit": child.returncode,
                        "visibleCommittedBundle": boundary == "after", "sameIdsAfterRecovery": True,
                        "ownedOrphanTemporaryFilesRemoved": orphan_hashes})
    _write_json(output / "crash-recovery.json", results)
    return results


def run(output):
    output = Path(output).absolute()
    assert not output.exists(), "Use a fresh owned --output; never overwrite a drill"
    output.mkdir(parents=True)
    root, context = fixture(output)
    before = hashes(root)
    _write_json(output / "source-before.json", before)
    context_path = output / "context.json"
    _write_json(context_path, context)
    inventory = cli(output, "01-inventory", root)
    target = output / "publication-not-yet-created/bundle.json"
    base = ["--target", target, "--context", context_path]
    # Strict mode refuses corrupt/unknown; explicit reviewed partial is separate.
    assert "unknown/corrupt" in cli(output, "02-strict-rejection", root, *base, expected=1)
    partial = [*base, "--preserve-incompatible"]
    plan = cli(output, "03-dry-run", root, *partial)
    assert not target.parent.exists() and not plan["plan"]["executable"]
    digest = plan["planDigest"]
    committed = cli(output, "04-apply", root, *partial, "--apply", "--mark-read-only",
                    "--expected-digest", digest)
    bundle = migration.read_bundle(target)
    allocations = copy.deepcopy(bundle["allocations"])
    raw, mtime = target.read_bytes(), target.stat().st_mtime_ns
    replay = cli(output, "05-replay", root, *partial, "--apply", "--expected-digest", digest)
    assert replay["idempotentReplay"] and replay["targetWrites"] == 0
    assert target.read_bytes() == raw and target.stat().st_mtime_ns == mtime
    for record in bundle["canonical"]:
        fc.validate_object(record["kind"], record["object"])
        assert record["object"]["state"] in {"disabled", "draft"}
    for name, backup in bundle["sourceBackups"].items():
        assert base64.b64decode(backup["bytes"]) == (root / name).read_bytes()
    assert not bundle["runtimeActivated"] and not bundle["runtimeFenceVerified"]
    assert all(p["targetIds"] == [] for p in bundle["preserved"])
    changed = copy.deepcopy(context)
    changed["profileId"] += "_edited"
    _write_json(context_path, changed)
    assert "plan_stale" in cli(output, "06-stale-context", root, *partial, "--apply",
                                "--expected-digest", digest, expected=1)
    _write_json(context_path, context)
    path = root / "background_jobs/runs.jsonl"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n")
        assert "plan_stale" in cli(output, "07-stale-source", root, *partial, "--apply",
                                    "--expected-digest", digest, expected=1)
    finally:
        path.write_bytes(original)
    assert target.read_bytes() == raw
    cli(output, "08-rollback", root, "--target", target, "--rollback",
        "--expected-digest", bundle["bundleDigest"])
    rolled = migration.read_bundle(target)
    assert rolled["canonical"] == [] and rolled["allocations"] == allocations
    assert rolled["entries"] and rolled["sourceBackups"] == bundle["sourceBackups"]
    cli(output, "09-reapply", root, *partial, "--apply", "--mark-read-only", "--expected-digest", digest)
    assert migration.read_bundle(target)["allocations"] == allocations
    crashes = crash_recovery(output, root, context_path, digest, allocations)
    # Reuse existing bounded stable snapshot + review packet; no mapper/apply
    # authority is derived from this packet and no real root is accessed.
    snapshot = output / "snapshot"
    copied = snapshot / "source-copy"
    manifest = []
    for name, sha in before.items():
        result = stable_copy(root / name, copied / name)
        assert result["state"] == "stable"
        manifest.append({"path": name, "kind": "file", "state": "stable", "sha256": sha})
    _write_json(snapshot / "snapshot-manifest.json", manifest)
    _write_json(snapshot / "wp08-inventory.json", build_inventory(copied, allow_real_profile=True))
    _write_json(snapshot / "preservation-bundle-manifest.json", {"canonicalBundleProduced": False})
    manifest_sha = hashlib.sha256((snapshot / "snapshot-manifest.json").read_bytes()).hexdigest()
    packet = build_packet(snapshot, output / "review-packet", expected_manifest_sha256=manifest_sha)
    assert not packet["canonicalTargetCreated"]
    after = hashes(root)
    assert after == before
    temporary = [p.relative_to(output).as_posix() for p in output.rglob("*")
                 if p.name.endswith((".tmp", ".lock", ".capture"))]
    assert temporary == []
    _write_json(output / "dispositions.json", inventory["instances"])
    _write_json(output / "source-after.json", after)
    summary = {"baseline": "d350efaf87e6ae60a5a60f037a4d736b52a2243b",
               "source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
               "inventory": inventory["summary"], "migratedOffline": len(bundle["entries"]),
               "preserved": len(bundle["preserved"]), "canonicalObjects": len(bundle["canonical"]),
               "allocations": allocations, "sourceUnchanged": before == after,
               "unscannedSourceFiles": sorted(set(before) - set(bundle["sourceBackups"])),
               "committedTargetSha256": hashlib.sha256(raw).hexdigest(),
               "remainingTemporaryArtifacts": temporary,
               "crashRecovery": crashes,
               "runtimeActivated": False, "runtimeFenceVerified": False,
               "actionPins": {k: v["ref"] for k, v in context["actions"].items()},
               "offlinePublicationExecutableFlag": committed["plan"]["executable"]}
    _write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), indent=2))
