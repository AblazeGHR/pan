"""Canonical FinalService runtime identity checks.

The former action_runtime fixture is retired; lifecycle behavior is covered by
the canonical HTTP/retention suites and this file keeps the read-only boundary.
"""
import asyncio
import pytest
from packages.actions.store import Store
from packages.jobs import final_contracts as fc
from tests.support.canonical_host import build, USER

def test_legacy_runtime_setup_is_rejected(tmp_path):
    with pytest.raises(PermissionError, match='retired'):
        Store(tmp_path / 'actions').put('actions', {'id': 'legacy'})

def test_canonical_runtime_has_distinct_profile_and_no_runs(tmp_path):
    h = build(tmp_path)
    try:
        assert h.service.profile_id.startswith('profile_')
        assert h.service.list('job', actor=USER)['items'] == []
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_unknown_agent_cannot_reconcile_or_read_jobs(tmp_path):
    h = build(tmp_path)
    try:
        with pytest.raises(fc.ContractError, match='Authorization'):
            h.service.list('job', actor={'type': 'agent', 'id': 'unknown'})
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_canonical_operations_are_queryable_without_legacy_plan(tmp_path):
    h = build(tmp_path)
    try:
        with h.service.transaction(readonly=True) as db:
            assert db.execute("SELECT COUNT(*) FROM final_operations").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM final_documents").fetchone()[0] > 0
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_legacy_executor_does_not_replay_unknown_receipt(tmp_path):
    with pytest.raises(PermissionError, match='retired'):
        Store(tmp_path / 'actions').put('groups', {'id': 'legacy-group'})
