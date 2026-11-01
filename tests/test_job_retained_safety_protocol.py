"""Executor cleanup protocol negatives; these are not real-tree proof."""
from concurrent.futures import ThreadPoolExecutor
import copy

import pytest

from packages.jobs.independent_executor import ExecutionConflict, ExecutionStore
from tests.test_job_final_independent_execution import attempt, claim, process_plan


def running(tmp_path):
    store = ExecutionStore(tmp_path)
    doc = claim(store, process_plan(tmp_path))
    store.phase(doc["effectId"], attempt(), doc["claim"], "executing")
    doc = store.phase(doc["effectId"], attempt(), doc["claim"], "running")
    return store, doc


def test_cleanup_concurrency_retains_one_request_and_requires_explicit_unknown_recheck(tmp_path):
    store, doc = running(tmp_path)
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(lambda _: store.request_cleanup(doc["effectId"], attempt()), range(4)))
    assert all(row["cleanupRequest"] == {"revision": 1, "status": "requested"} for row in rows)
    store.finish_cleanup(doc["effectId"], attempt(), doc["claim"], 1, {"confirmed": False})
    reopened = ExecutionStore(tmp_path)
    reopened.recover(lambda _: True)
    assert reopened.get(doc["effectId"])["cleanupRequest"]["status"] == "unknown"
    assert reopened.request_cleanup(doc["effectId"], attempt())["cleanupRequest"] == {"revision": 2, "status": "requested"}
    before = reopened.get(doc["effectId"])
    with pytest.raises(ExecutionConflict):
        reopened.finish_cleanup(doc["effectId"], attempt(), doc["claim"], 1, {})
    with pytest.raises(ExecutionConflict):
        reopened.finish_cleanup(doc["effectId"], attempt(), "wrong-claim", 2, {})
    assert reopened.get(doc["effectId"]) == before


@pytest.mark.parametrize("missing", ["rootExited", "identityCheck", "ownedTree", "confirmed"])
def test_incomplete_cleanup_cannot_be_confirmed(tmp_path, missing):
    store, doc = running(tmp_path)
    store.request_cleanup(doc["effectId"], attempt())
    proof = {"confirmed": True, "rootExited": True, "identityCheck": {"ours": True, "status": "alive"},
             "ownedTree": {"confirmed": True, "owned": [7], "remaining": []}}
    proof.pop(missing)
    result = store.finish_cleanup(doc["effectId"], attempt(), doc["claim"], 1, proof)
    assert result["cleanupRequest"]["status"] == "unknown"
    assert result["cleanupRequest"]["proof"]["confirmed"] is False


def test_lost_supervisor_cleanup_is_unknown_and_cannot_authorize_root_only_recovery(tmp_path):
    store, doc = running(tmp_path)
    store.request_cleanup(doc["effectId"], attempt())
    reopened = ExecutionStore(tmp_path)
    reopened.recover(lambda _: False)
    before = copy.deepcopy(reopened.get(doc["effectId"]))
    assert before["result"]["outcome"] == "unknown"
    assert before["cleanupRequest"]["status"] == "unknown"
    with pytest.raises(ExecutionConflict, match="handle unavailable"):
        reopened.request_cleanup(doc["effectId"], attempt())
    assert reopened.get(doc["effectId"]) == before


def test_cleanup_unavailable_capability_fails_explicitly_without_mutation(tmp_path, monkeypatch):
    from packages.core import platform
    from packages.core.platform.process import UnsupportedPlatformError
    store, doc = running(tmp_path)
    monkeypatch.setattr(platform, "supports", lambda name: False)
    with pytest.raises(UnsupportedPlatformError, match="processOwner unsupported"):
        store.request_cleanup(doc["effectId"], attempt())
    assert store.get(doc["effectId"]) == doc


def test_cleanup_http_rejects_self_asserted_proof_and_exposes_unsupported(tmp_path, monkeypatch):
    from packages.core import platform
    from packages.jobs.independent_executor import ExecutionHost
    from tests.test_independent_job_service import _service, _stop
    store, doc = running(tmp_path)
    server, thread, client, container = _service(tmp_path)
    container._execution_host = ExecutionHost(store)
    try:
        with pytest.raises(RuntimeError, match="400"):
            client._request("POST", "/executions/" + doc["effectId"] + "/cleanup",
                            {"attempt": attempt(), "proof": {"confirmed": True}})
        monkeypatch.setattr(platform, "supports", lambda name: False)
        with pytest.raises(RuntimeError, match="501.*processOwner unsupported"):
            client.cleanup_execution(doc["effectId"], attempt())
        assert "cleanupRequest" not in store.get(doc["effectId"])
    finally:
        _stop(server, thread)
