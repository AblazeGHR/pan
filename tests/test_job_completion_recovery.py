"""Canonical completion/recovery boundaries after legacy runtime retirement."""
import asyncio
import pytest
from packages.actions.store import Store
from packages.jobs import final_contracts as fc
from tests.support.canonical_host import build, USER

def test_legacy_completion_writer_is_retired(tmp_path):
    with pytest.raises(PermissionError, match='retired'):
        Store(tmp_path / 'actions').put('actions', {'id': 'legacy'})

def test_canonical_service_exposes_empty_run_projection_without_legacy_runtime(tmp_path):
    h = build(tmp_path)
    try:
        assert h.service.list('job', actor=USER)['items'] == []
        assert getattr(h.automation, 'job_runtime', None) is None
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_canonical_service_denies_unknown_recovery_authority(tmp_path):
    h = build(tmp_path)
    try:
        with pytest.raises(fc.ContractError, match='Authorization'):
            h.service.list('job', actor={'type': 'agent', 'id': 'unknown'})
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_legacy_recovery_store_never_creates_execution_rows(tmp_path):
    store = Store(tmp_path / 'actions')
    with pytest.raises(PermissionError):
        store.put('groups', {'id': 'legacy-group'})
    assert not list(tmp_path.rglob('jobExecutions'))

def test_completion_metadata_is_canonical_only(tmp_path):
    h = build(tmp_path)
    try:
        with h.service.transaction(readonly=True) as db:
            assert db.execute("SELECT COUNT(*) FROM final_operations").fetchone()[0] == 0
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())
