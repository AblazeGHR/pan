"""Replay the script-only runtime binding preservation guard on owned fixtures."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "tests")]

from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.render import render_markdown
from tests.test_job_final_migration import _background_job, _write_json
from tests.test_job_final_migration_apply import context_for
from tests.test_job_migration_runtime_readiness import _runtime_delivery_source
from tests.support.job_migration_runtime_rehearsal import hashes


def run(output):
    output = Path(output).absolute()
    assert not output.exists(), "Use a new owned output directory"
    output.mkdir(parents=True)
    source = output / "synthetic-source"
    raw_path, _ = _runtime_delivery_source(source, "send_session")
    _write_json(source / "background_jobs/jobs/job_process.json", _background_job(source))
    original = raw_path.read_bytes()
    before = hashes(source)
    inventory = build_inventory(source)
    _write_json(output / "inventory.json", inventory)
    (output / "inventory.md").write_text(render_markdown(inventory), encoding="utf-8")
    context = {**context_for(source), "partialPolicy": {"version": 1, "mode": "preserve-incompatible"}}
    target = output / "offline-bundle.json"
    digest = migration.plan_digest(inventory, context)
    dry = migration.migrate(source, target, context, preserve_incompatible=True)
    assert not target.exists()
    applied = migration.migrate(source, target, context, apply=True, preserve_incompatible=True, expected_digest=digest)
    bundle = migration.read_bundle(target)
    preserved, = bundle["preserved"]
    assert len(bundle["entries"]) == 1 and bundle["entries"][0]["type"] == "MIG-BG"
    assert preserved["targetIds"] == [] and preserved["executable"] is False
    assert "runtime_binding_unavailable" in preserved["reasonCodes"]
    relative = raw_path.relative_to(source).as_posix()
    assert base64.b64decode(bundle["sourceBackups"][relative]["bytes"]) == original
    replay = migration.migrate(source, target, context, apply=True, preserve_incompatible=True, expected_digest=digest)
    assert replay["idempotentReplay"] and replay["targetWrites"] == 0
    rollback = migration.rollback(source, target, expected_digest=bundle["bundleDigest"])
    assert rollback["runtimeFenceVerified"] is False
    assert hashes(source) == before
    _write_json(output / "source-before.json", before)
    _write_json(output / "source-after.json", hashes(source))
    _write_json(output / "dry-run.json", dry)
    _write_json(output / "apply.json", applied)
    _write_json(output / "rollback.json", rollback)
    summary = {
        "source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "syntheticOnly": True, "sourceUnchanged": True,
        "canonicalEntries": len(bundle["entries"]), "preservedEntries": len(bundle["preserved"]),
        "runtimeDeliveryTargetIds": preserved["targetIds"],
        "runtimeBindingGap": preserved["runtimeBindingGap"],
        "originalFileSha256": hashlib.sha256(original).hexdigest(),
        "bundleDigestBeforeRollback": bundle["bundleDigest"],
        "idempotentReplayWrites": replay["targetWrites"],
        "rollbackRuntimeFenceVerified": rollback["runtimeFenceVerified"],
        "productRuntimeExercised": False, "eventBacklogRetired": False,
        "requiresNewUserDecision": False, "wholeProfileMigrationComplete": False,
        "temporaryFiles": [p.name for p in output.rglob("*.tmp")], "portsOpened": [],
    }
    assert summary["temporaryFiles"] == []
    _write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), indent=2))
