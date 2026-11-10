"""Retired Action Job template boundary; canonical definitions are authoritative."""
import pytest
from packages.actions.store import Store
from packages.jobs import final_contracts as fc
from tests.support.canonical_host import build, USER

def config(**fields):
    return {'name': 'canonical job', 'steps': [], **fields}

async def setup(root, pause=None):
    raise PermissionError('Legacy Action Job host retired; use canonical FinalService')

def test_legacy_template_writer_is_retired(tmp_path):
    with pytest.raises(PermissionError, match='retired'):
        Store(tmp_path / 'actions').put('actions', {'id': 'legacy'})

def test_canonical_profile_has_no_legacy_templates_or_execution_rows(tmp_path):
    h = build(tmp_path)
    try:
        assert h.service.list('job', actor=USER)['items'] == []
        with h.service.transaction(readonly=True) as db:
            assert db.execute("SELECT COUNT(*) FROM final_operations").fetchone()[0] == 0
    finally:
        import asyncio
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_template_unknown_authority_is_denied_without_write(tmp_path):
    h = build(tmp_path)
    try:
        with pytest.raises(fc.ContractError, match='Authorization'):
            h.service.list('job', actor={'type': 'agent', 'id': 'unknown'})
    finally:
        import asyncio
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

def test_template_legacy_cancel_and_recovery_are_not_replayed(tmp_path):
    with pytest.raises(PermissionError, match='retired'):
        Store(tmp_path / 'actions').put('groups', {'id': 'legacy-group'})
