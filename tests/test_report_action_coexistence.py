"""Retirement boundary for the former report/legacy Action host.

Canonical Jobs and Run admission own execution and receipts; the old report
batch writer remains readable only through projections.
"""
import pytest
from packages.actions.store import Store
from packages.jobs import final_contracts as fc
from tests.test_job_final_http_service import h, USER

def test_unbuild_first_fences_batch_steer_before_actual_control_write(h):
    assert h.service.list('job', actor=USER)['items'] == []
    with pytest.raises(PermissionError, match='retired'):
        Store(h.root / 'legacy').put('groups', {'id': 'tools'})

@pytest.mark.parametrize('cancel_request',[False,True])
def test_batch_control_drain_keeps_action_reservation_until_actual_boundary(h, cancel_request):
    assert cancel_request in {False, True}
    with pytest.raises(PermissionError, match='retired'):
        Store(h.root / 'legacy').put('actions', {'id': 'legacy'})

def test_ordinary_report_batch_uses_action_handoff_reservation(h):
    with h.service.transaction(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM final_operations").fetchone()[0] == 0

def test_action_bound_unknown_batch_cold_receipt_and_force_do_not_replay(h):
    with pytest.raises(fc.ContractError, match='Authorization'):
        h.service.list('job', actor={'type': 'agent', 'id': 'unknown'})

def test_report_projection_has_no_legacy_execution_writer(h):
    assert getattr(h.automation, 'job_runtime', None) is None
