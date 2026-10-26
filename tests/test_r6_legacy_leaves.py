"""R6-A successor negative controls: deleted leaves, preserved evidence/reads."""
import asyncio
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from packages.core import background_jobs
from packages.jobs.retired import RetiredEntryPoint, reject_execution
from scripts.audit_r6_leaves import REMOVED, REPLACED, production_references

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path,name", [(path, name) for path, names in REMOVED.items() for name in names])
def test_removed_leaf_is_absent_and_explicitly_retired_without_owner_or_io(tmp_path, monkeypatch, path, name):
    if path == "packages/scheduler/engine.py":
        with pytest.raises(ModuleNotFoundError) as exc:
            importlib.import_module(path.removesuffix(".py").replace("/", "."))
        assert exc.value.name == "packages.scheduler.engine"
        assert not list(tmp_path.rglob("*"))
        return
    module = importlib.import_module(path.removesuffix(".py").replace("/", "."))
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(tmp_path / "must-not-exist"))
    assert name not in vars(module)
    with pytest.raises(RetiredEntryPoint, match=name + " retired"):
        getattr(module, name)
    assert not (tmp_path / "must-not-exist").exists()
    assert not list(tmp_path.rglob("*"))  # Includes owner DB/lock/lease files.


def test_no_import_alias_attribute_or_literal_getattr_can_reach_a_removed_leaf():
    assert production_references(ROOT) == []


@pytest.mark.parametrize("path,name", [(path, name) for path, names in REPLACED.items() for name in names])
def test_old_python_producer_refuses_before_store_lease_job_or_run(tmp_path, path, name):
    from packages.automation.authorization import _MARKER, _SCOPE_KEY
    from packages.jobs.final_api import CanonicalUserAuth
    app = FastAPI()
    auth = CanonicalUserAuth(tmp_path / "auth")
    auth.permissions_status()
    app.state.automation = SimpleNamespace(auth=auth)
    request = Request({"type": "http", "app": app, "method": "POST", "path": "/retired", "headers": [],
                       _SCOPE_KEY: _MARKER})
    module = importlib.import_module(path.removesuffix(".py").replace("/", "."))
    import inspect
    parameters = inspect.signature(getattr(module, name)).parameters
    values = {key: request if key == "request" else {} if key in {"data", "fields"} else "job_original"
              for key in parameters}
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(module, name)(**values))
    assert error.value.status_code == 410
    assert error.value.detail["code"] == "retired"
    assert {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_old_action_snapshot_rejects_without_mutating_arguments_or_receipts(tmp_path):
    values = {"jobId": "job_original", "attempts": [{"id": "old", "status": "unknown"}]}
    before = json.dumps(values, sort_keys=True)
    with pytest.raises(PermissionError, match="retired"):
        asyncio.run(reject_execution(values, values, values, values))
    assert json.dumps(values, sort_keys=True) == before
    assert not list(tmp_path.rglob("*"))


def test_read_facade_preserves_60_original_ids_bytes_hashes_logs_and_run_history(tmp_path):
    root = tmp_path / "background_jobs"
    jobs = root / "jobs"
    jobs.mkdir(parents=True)
    logs = root / "logs"
    logs.mkdir()
    originals = {}
    for i in range(60):
        job_id = f"job_unrecognized_{i:02}"
        raw = (json.dumps({"jobId": job_id, "kind": "unrecognized-kind", "status": "unknown",
                           "privateFutureField": {"originalId": job_id}, "targetSessionId": "session_original",
                           "logPath": str(logs / (job_id + ".log")), "terminalEventId": job_id + ":terminal",
                           "receipt": {"status": "unknown", "originalKey": job_id}}, indent=3) + "\n").encode()
        path = jobs / (job_id + ".json")
        path.write_bytes(raw)
        (logs / (job_id + ".log")).write_bytes(b"immutable raw log\r\n")
        originals[job_id] = (raw, hashlib.sha256(raw).hexdigest())
    history = b'{"task_id":"sch_original","run_id":"run_original","status":"unknown"}\n'
    (root / "runs.jsonl").write_bytes(history)
    # Malformed data must remain on disk, never repaired or silently rewritten.
    (jobs / "job_partial.json").write_bytes(b'{"jobId":"job_partial",')
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert {j["jobId"] for j in background_jobs.list_jobs(root)} == set(originals)
    for job_id, (raw, digest) in originals.items():
        job = background_jobs.get(job_id, root)
        assert job["jobId"] == job_id and job["receipt"]["originalKey"] == job_id
        assert job["terminalEventId"] == job_id + ":terminal"
        assert (jobs / (job_id + ".json")).read_bytes() == raw
        assert hashlib.sha256(raw).hexdigest() == digest
        assert Path(job["logPath"]).read_bytes() == b"immutable raw log\r\n"
    runs = background_jobs.list_run_records("sch_original", registry_root=root)
    assert runs[0]["run_id"] == "run_original"
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert (root / "runs.jsonl").read_bytes() == history
    assert not (tmp_path / "job-runtime-owner.sqlite3").exists()


def test_manual_delete_dependency_packet_survives_r6_without_executing(tmp_path):
    from scripts.job_final_migration.p9d_support import manual_deletion_packet
    from tests.test_job_final_migration_user_authorization import evidence_for
    evidence, root, packet, _ = evidence_for(tmp_path, n_simple=1, n_complex=59)
    before = {p.relative_to(evidence).as_posix(): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
    rows = manual_deletion_packet(packet, tmp_path / "manual", original_root=str(root))
    assert len(rows) == 60
    assert all(row["dependenciesBeforeDelete"] and not row["automaticDelete"] for row in rows)
    for row in rows:
        dependencies = row["dependenciesBeforeDelete"]
        assert {"oldReadDependencies", "references", "checks"} <= dependencies.keys()
        checks = " ".join(dependencies["checks"])
        for required in ("original-ID/raw/log", "pins/retention", "partial/unknown", "handoff", "Session/Locker", "current SHA"):
            assert required in checks
    assert all("-Confirm" in row["manualCommand"] and "-Recurse" not in row["manualCommand"] for row in rows)
    assert {p.relative_to(evidence).as_posix(): p.read_bytes() for p in evidence.rglob("*") if p.is_file()} == before


def test_isolated_job_host_startup_http_and_shutdown_keep_legacy_retired(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from fastapi.testclient import TestClient
    from packages.automation.service import Service
    from packages.jobs import final_api
    from packages.jobs.final_service import bind
    from packages.jobs.runtime_owner import OwnerFence
    from packages.web import server

    # Exercise the production owner wrapper and real canonical/Automation
    # startup. OS/network/plugin services are outside this isolated Job smoke.
    profile = tmp_path
    observed = {}

    @asynccontextmanager
    async def job_host(app, owner):
        worker = SimpleNamespace(workers={}, set_running_observer=lambda callback: None)
        automation = Service(profile / "automation", worker, None)
        automation.auth = final_api.CanonicalUserAuth(profile / "automation")
        app.state.automation = automation
        service = bind(app, automation, profile / "job-final", owner=owner)
        observed.update(service=service, automation=automation, owner=owner)
        await service.start()
        await automation.start()
        try:
            yield
        finally:
            await automation.close()
            await service.close()

    monkeypatch.setattr(server, "_owned_lifespan", job_host)
    app = FastAPI(lifespan=server.lifespan)
    final_api.install(app)
    with TestClient(app) as client:
        service = observed["service"]
        auth = observed["automation"].auth
        client.headers["Authorization"] = "Bearer " + auth.login(auth.key())
        assert client.get("/api/jobs").json()["items"] == []
        assert client.get("/api/triggers").json()["items"] == []
        assert observed["owner"].status()["state"] == "active"
        for name in ("start_recovery_loop", "run_due_message_jobs", "recover_notifications"):
            with pytest.raises(RetiredEntryPoint, match="retired"):
                getattr(background_jobs, name)
        with service.transaction(readonly=True) as db:
            assert not service._all(db, "job")
        with observed["owner"].transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM legacy_leases").fetchone()[0] == 0
        assert not background_jobs.is_recovery_running()
    assert OwnerFence(profile).status()["state"] == "closed"
    assert observed["automation"].ingester is observed["automation"].dispatcher is None
    assert observed["service"].task is None
