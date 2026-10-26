"""P9 recovery controls: synthetic profiles only, never practical data."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService
from packages.jobs.runtime_owner import OwnerFence
from scripts.job_final_migration import apply as migration
from tests.test_job_runtime_owner import migration_fixture


def graph(service):
    with service.transaction(readonly=True) as db:
        definitions = db.execute("SELECT kind,id,document FROM final_documents WHERE kind != 'action' ORDER BY kind,id").fetchall()
        authorities = db.execute("SELECT * FROM final_authorities ORDER BY kind,id").fetchall()
        links = db.execute("SELECT * FROM creator_links").fetchall()
    return definitions, authorities, links, service.creators.list()


def assert_closed(service):
    assert graph(service) == ([], [], [], [])
    assert service.task is None
    with pytest.raises(fc.ContractError, match="fence"):
        with OwnerFence(service.root.parent).writer():
            pytest.fail("failed import reopened legacy writes")


def test_p9_import_rechecks_current_agent_grants_and_restart_stays_blocked(tmp_path):
    service, spec, bundle = migration_fixture(tmp_path)
    # This is an explicitly synthetic principal; default current Agent grants
    # deny create even though the offline bundle has an attestation.
    for record in bundle["canonical"]:
        record["object"]["createdBy"] = {"type": "agent", "id": "p9_denied_fixture"}
    bundle = migration._seal(bundle)
    Path(spec["path"]).write_text(fc.canonical_json(bundle), encoding="utf-8")
    spec["expected_hash"] = hashlib.sha256(Path(spec["path"]).read_bytes()).hexdigest()
    with pytest.raises(fc.ContractError, match="denied"):
        asyncio.run(service.start(activation=spec))
    assert service.owner.status()["state"] == "failed"
    assert_closed(service)
    restored = FinalService(service.root, service.auth)
    with pytest.raises(fc.ContractError, match="original reviewed bundle"):
        asyncio.run(restored.start())
    assert_closed(restored)


def test_p9_import_write_failure_rolls_back_entire_graph_then_replays_once(tmp_path, monkeypatch):
    service, spec, bundle = migration_fixture(tmp_path)
    raw = Path(spec["path"]).read_bytes()
    save = service._save
    def fail_after_write(db, kind, object_id, value):
        save(db, kind, object_id, value)
        if kind == "trigger":
            raise OSError("P9 injected failure after graph rows written")
    with monkeypatch.context() as patch:
        patch.setattr(service, "_save", fail_after_write)
        with pytest.raises(OSError, match="P9 injected"):
            asyncio.run(service.start(activation=spec))
    assert_closed(service)
    async def resume():
        restored = FinalService(service.root, service.auth)
        await restored.start(activation=spec)
        before = graph(restored)
        assert len(before[0]) == 3  # Job, Trigger, activation receipt
        assert len(before[3]) == 1
        await restored.close()
        replay = FinalService(service.root, service.auth)
        await replay.start(activation=spec)
        after = graph(replay)
        # Owner epoch is intentionally refreshed; stable definitions/receipts
        # and object identity are not recreated by replay.
        assert before[:3] == after[:3]
        assert [r["id"] for r in before[3]] == [r["id"] for r in after[3]]
        assert all(r["state"] == "disabled" for r in after[3])
        await replay.close()
    asyncio.run(resume())
    assert Path(spec["path"]).read_bytes() == raw


def test_p9_process_exit_mid_import_rolls_back_and_same_bundle_recovers(tmp_path):
    service, spec, bundle = migration_fixture(tmp_path)
    spec_path = tmp_path / "reviewed-activation.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    raw = Path(spec["path"]).read_bytes()
    code = """
import asyncio,json,os,sys
from pathlib import Path
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
root=Path(sys.argv[1])
service=FinalService(root,CanonicalUserAuth(root.parent/'auth'))
save=service._save
def crash(db,kind,object_id,value):
    save(db,kind,object_id,value)
    if kind=='trigger': os._exit(77)
service._save=crash
asyncio.run(service.start(activation=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))))
"""
    child = subprocess.run([sys.executable, "-c", code, str(service.root), str(spec_path)], capture_output=True, text=True)
    assert child.returncode == 77, child.stderr
    assert_closed(service)
    assert service.owner.status()["state"] == "activating"
    async def resume():
        restored = FinalService(service.root, service.auth)
        await restored.start(activation=spec)
        assert restored.owner.token[0] == 2
        rows = graph(restored)
        assert len(rows[0]) == 3 and len(rows[3]) == 1
        actual_ids = {r[1] for r in rows[0] if r[0] != "runtime_activation"} | {r["id"] for r in rows[3]}
        assert actual_ids == set(bundle["entries"][0]["targetIds"])
        await restored.close()
    asyncio.run(resume())
    assert Path(spec["path"]).read_bytes() == raw
