"""P9B: explicit partial imports; unselected records never execute."""
import asyncio
import base64
import copy
import hashlib
import os
import sys
from pathlib import Path
import sqlite3

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.snapshot import collect, stable_copy, sqlite_snapshot
from tests.test_job_final_migration import _make_root, _background_job, _write_json, _snapshot
from tests.test_job_final_migration_apply import context_for
from tests.test_job_runtime_owner import migration_fixture

POLICY = {"version": 1, "mode": "preserve-incompatible"}


def partial(root, target, context, **kwargs):
    return migration.migrate(root, target, context, preserve_incompatible=True, apply=True,
        expected_digest=migration.plan_digest(build_inventory(root), context), **kwargs)


@pytest.mark.parametrize("raw", [b'{broken-json', b'{"kind":"future","status":"unknown"}'])
def test_partial_preserves_complex_unknown_raw_bytes_and_replays_once(tmp_path, raw):
    root = _make_root(tmp_path)
    complex_record = _background_job(root)
    complex_record.update(jobId="job_live", status="running", processTreeEnded=False)
    _write_json(root / "background_jobs/jobs/job_live.json", complex_record)
    unknown = root / "background_jobs/jobs/job_future.json"
    unknown.write_bytes(raw)
    before = _snapshot(root)
    context = {**context_for(root), "partialPolicy": POLICY}
    target = tmp_path / "partial.json"
    report = partial(root, target, context, mark_read_only=True)
    bundle = migration.read_bundle(target)
    assert len(bundle["entries"]) == 6 and len(bundle["preserved"]) == 2
    assert report["partialApply"]["allLegacyCleared"] is False
    assert all(p["targetIds"] == [] and p["executable"] is False for p in bundle["preserved"])
    assert base64.b64decode(bundle["sourceBackups"]["background_jobs/jobs/job_future.json"]["bytes"]) == raw
    assert all(r["object"]["state"] in {"disabled", "draft"} for r in bundle["canonical"])
    assert partial(root, target, context)["targetWrites"] == 0
    allocations = bundle["allocations"]
    migration.rollback(root, target, expected_digest=bundle["bundleDigest"])
    partial(root, target, context, mark_read_only=True)
    assert migration.read_bundle(target)["allocations"] == allocations
    assert _snapshot(root) == before


def test_partial_policy_and_selected_ids_are_bound_to_reviewed_digest(tmp_path):
    root = _make_root(tmp_path)
    context = context_for(root)
    target = tmp_path / "partial.json"
    with pytest.raises(migration.ApplyError, match="partialPolicy"):
        partial(root, target, context)
    context["partialPolicy"] = POLICY
    context["selectedMigrationIds"] = [next(iter(context["records"]))]
    digest = migration.plan_digest(build_inventory(root), context)
    changed = copy.deepcopy(context)
    changed["selectedMigrationIds"] = list(context["records"])
    with pytest.raises(migration.ApplyError, match="plan_stale"):
        migration.migrate(root, target, changed, apply=True, expected_digest=digest, preserve_incompatible=True)
    partial(root, target, context)
    bundle = migration.read_bundle(target)
    assert len(bundle["entries"]) == 1 and len(bundle["preserved"]) == 5
    context["selectedMigrationIds"] = ["unknown-id"]
    with pytest.raises(migration.ApplyError, match="selectedMigrationIds"):
        partial(root, tmp_path / "bad.json", context)


@pytest.mark.parametrize("fault", ["authorization", "reference"])
def test_partial_does_not_skip_invalid_selected_authority_or_reference(tmp_path, fault):
    root = _make_root(tmp_path)
    (root / "background_jobs/jobs/job_future.json").write_text('{"kind":"future"}')
    context = {**context_for(root), "partialPolicy": POLICY}
    if fault == "authorization":
        next(iter(context["records"].values()))["authorizationEvidence"] = ""
    else:
        next(iter(context["actions"].values()))["ref"]["digest"] = "0" * 64
    with pytest.raises(migration.ApplyError):
        partial(root, tmp_path / "bad.json", context)
    assert not (tmp_path / "bad.json").exists()


def test_partial_failed_replace_keeps_complete_graph_and_preserved_bytes(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    (root / "background_jobs/jobs/job_future.json").write_bytes(b'{broken')
    context = {**context_for(root), "partialPolicy": POLICY}
    target = tmp_path / "partial.json"
    partial(root, target, context)
    old = target.read_bytes()
    replace = migration.os.replace
    def fail(src, dst):
        if Path(dst) == target:
            raise OSError("partial replace failure")
        return replace(src, dst)
    monkeypatch.setattr(migration.os, "replace", fail)
    with pytest.raises(OSError, match="partial replace"):
        partial(root, target, context, mark_read_only=True)
    assert target.read_bytes() == old
    assert target.with_name(target.name + ".bak").read_bytes() == old
    assert not target.with_name(target.name + ".lock").exists()


def test_partial_runtime_import_never_loads_unknown_or_replays_its_effect(tmp_path):
    service, spec, old = migration_fixture(tmp_path)
    root = service.root.parent
    unknown = root / "background_jobs/jobs/job_future.json"
    unknown.write_text('{"kind":"future","status":"unknown","enabled":true}', encoding="utf-8")
    raw = unknown.read_bytes()
    context = context_for(root)
    context.update(profileId=service.profile_id, partialPolicy=POLICY)
    context["records"][old["entries"][0]["migrationId"]] = old["entries"][0]["attestation"]
    target = tmp_path / "partial-runtime.json"
    partial(root, target, context, mark_read_only=True)
    bundle = migration.read_bundle(target)
    spec.update(path=str(target), expected_hash=hashlib.sha256(target.read_bytes()).hexdigest(),
                expected_plan_digest=bundle["planDigest"], active_ids=[])
    async def check():
        await service.start(activation=spec)
        assert service.executor.calls == []
        with service.transaction(readonly=True) as db:
            assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 1
        await service.close()
        restored = FinalService(service.root, service.auth)
        await restored.start(activation=spec)
        with restored.transaction(readonly=True) as db:
            assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 1
        await restored.close()
    asyncio.run(check())
    assert unknown.read_bytes() == raw
    assert bundle["preserved"][0]["executable"] is False


def test_stable_copy_retries_changed_file_then_verifies_two_hashes(tmp_path):
    source, target = tmp_path / "source", tmp_path / "private/copy"
    source.write_bytes(b"old")
    def mutate(number):
        if number == 1:
            source.write_bytes(b"new contents")
    result = stable_copy(source, target, observe=mutate)
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["stable"] is False
    assert result["state"] == "stable" and target.read_bytes() == b"new contents"


def test_continuously_changed_file_remains_blocked_and_unpublished(tmp_path):
    source, target = tmp_path / "source", tmp_path / "private/copy"
    source.write_text("0")
    result = stable_copy(source, target, attempts=3, observe=lambda number: source.write_text(str(number)))
    assert result["state"].startswith("blocked") and len(result["attempts"]) == 3
    assert not target.exists()


def test_sqlite_wal_backup_closes_handles_and_preserves_source_bytes(tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "private/copy.sqlite3"
    db = sqlite3.connect(source)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE facts(id PRIMARY KEY,value)")
        db.execute("INSERT INTO facts VALUES(1,'original-key')")
        db.commit()
        before = _snapshot(tmp_path)
        result = sqlite_snapshot(source, target)
        assert result["state"] == "stable-sqlite-backup"
        assert result["attempts"][-1]["integrity"] == "ok"
        restored = sqlite3.connect(target)
        try:
            assert restored.execute("SELECT * FROM facts").fetchall() == [(1, "original-key")]
        finally:
            restored.close()
        after = _snapshot(tmp_path)
        assert all(after[k] == v for k, v in before.items())
        target.replace(target.with_name("closed-handle.sqlite3"))
    finally:
        db.close()


def test_allowlist_excludes_lock_cache_and_worktree_and_never_follows_link(tmp_path):
    source = tmp_path / "data"
    for rel in ("sessions/real.json", "sessions/node_modules/dependency.json", "workdirs/unrelated.json",
                "actions/job-runtime/service.lock"):
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    external = tmp_path / "outside"
    external.mkdir()
    (external / "secret.json").write_text("secret")
    try:
        os.symlink(external, source / "sessions/link", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"link creation unsupported in this Windows test environment: {exc}")
    private = tmp_path / "private"
    result = collect(source, private)
    assert result["capturedFiles"] == 1 and result["linksNotFollowed"] == 1
    assert result["captureBlockers"] == 0 and result["explicitExclusions"] == 3
    assert not (private / "source-copy/actions/job-runtime/service.lock").exists()
    assert not (private / "source-copy/sessions/link").exists()
    assert not (private / "source-copy/workdirs").exists()
