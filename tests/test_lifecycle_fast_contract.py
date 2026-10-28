"""Fast unit contracts; capability injection is not POSIX lifecycle support.

Windows exercises the real declared capability. On POSIX these tests inject
only that capability decision to cover shared logic; a separate test exercises
the real host's explicit 501 boundary. No platform identity is changed.
"""
import asyncio
import json

import pytest

from packages.core import platform
from packages.web import server
from tests import test_lifecycle_canonical_preparation as preparation
from tests import test_lifecycle_strict_successors as strict
from tests.test_lifecycle_canonical_preparation import prepared


@pytest.fixture
def lifecycle_unit_capability(monkeypatch):
    actual = platform.supports
    if not actual("service.lifecycle"):
        monkeypatch.setattr(platform, "supports", lambda name: True if name == "service.lifecycle" else actual(name))


@pytest.mark.parametrize("operation", ["exit", "restart"])
def test_fast_ask_requires_choice(prepared, lifecycle_unit_capability, operation):
    preparation.test_ask_without_choice_rejects_before_admission_or_worker_gate(prepared, operation)


@pytest.mark.parametrize("operation,strategy,choice,mark", [
    ("exit", "ask", True, True), ("exit", "ask", False, False),
    ("exit", "offline", False, True), ("restart", "preserve-running", True, False),
])
def test_fast_frozen_snapshot_gate_and_shutdown(prepared, lifecycle_unit_capability, operation, strategy, choice, mark):
    preparation.test_freeze_before_gate_and_worker_shutdown_before_supervisor(prepared, operation, strategy, choice, mark)


@pytest.mark.parametrize("operation", ["exit", "restart"])
def test_fast_nonblocking_singleflight(prepared, lifecycle_unit_capability, operation):
    strict.test_scheduled_response_does_not_wait_for_worker_shutdown(prepared, operation)


@pytest.mark.parametrize("again", ["exit", "restart"])
def test_fast_original_identity_after_memory_loss(prepared, lifecycle_unit_capability, again):
    preparation.test_lost_memory_still_fences_duplicate_or_cross_operation(prepared, again)


def test_fast_prelaunch_partial_failure_stays_unknown(prepared, lifecycle_unit_capability):
    preparation.test_partial_worker_failure_is_unknown_and_never_launches_or_reopens(prepared)


def test_fast_cancel_during_ambiguous_launch_stays_unknown(prepared, lifecycle_unit_capability):
    strict.test_cancel_during_ambiguous_launch_preserves_original_unknown(prepared)


@pytest.mark.skipif(platform.supports("service.lifecycle"), reason="real unsupported lifecycle boundary applies only on hosts without capability")
@pytest.mark.parametrize("operation", ["exit", "restart"])
def test_fast_real_host_reports_lifecycle_unsupported_without_effect(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(server, "_PROJECT_DIR", tmp_path)
    result = asyncio.run(server.api_system_main_lifecycle({"operation": operation,
        "options": {"markRunningSessionsOffline": True}}))
    assert result.status_code == 501
    assert json.loads(result.body)["error"]["code"] == "unsupported_platform"
    assert not (tmp_path / "data").exists()
