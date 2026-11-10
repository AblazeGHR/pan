"""Retired Action Runtime chain boundary; canonical Run chains own execution."""
import pytest
from packages.actions.store import Store
from packages.jobs import final_contracts as fc
from tests.support.canonical_host import build, USER

def test_legacy_action_chain_writer_is_retired(tmp_path):
    with pytest.raises(PermissionError, match='retired'):
        Store(tmp_path / 'actions').put('actions', {'id': 'legacy'})

def test_canonical_job_projection_has_no_legacy_chain_rows(tmp_path):
    h = build(tmp_path)
    try:
        assert h.service.list('job', actor=USER)['items'] == []
        with h.service.transaction(readonly=True) as db:
            assert db.execute("SELECT COUNT(*) FROM final_operations").fetchone()[0] == 0
    finally:
        import asyncio
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_unknown_chain_authority_fails_closed(tmp_path):
    h = build(tmp_path)
    try:
        with pytest.raises(fc.ContractError, match='Authorization'):
            h.service.list('job', actor={'type': 'agent', 'id': 'unknown'})
    finally:
        import asyncio
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_legacy_chain_does_not_replay_or_create_execution(tmp_path):
    with pytest.raises(PermissionError, match='retired'):
        Store(tmp_path / 'actions').put('groups', {'id': 'legacy-group'})
