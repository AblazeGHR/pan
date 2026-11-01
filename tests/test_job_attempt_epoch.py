"""Real canonical admission/execution at owner epochs beyond bootstrap."""
import asyncio
import copy

import pytest

from packages.jobs import final_contracts as fc
from tests.test_job_final_http_service import h, enabled, launch, USER, run_control
from packages.jobs.runtime_owner import OwnerFence


def hold_epoch(h, epoch):
    for previous in range(epoch - 1):
        old = OwnerFence(h.root.parent)
        old.claim(previous)
        old.set_state("active")
        old.release()
    owner = OwnerFence(h.root.parent)
    owner.claim(epoch - 1)
    owner.set_state("active")
    h.service.owner = h.service.creators.owner_fence = h.service.events.owner_fence = owner
    return owner


@pytest.mark.parametrize("epoch", [2, 3])
def test_new_attempt_persists_held_epoch_before_effect_and_late_receipts_are_rejected(h, epoch, monkeypatch):
    owner = hold_epoch(h, epoch)
    item = enabled(h)
    rid = launch(h, item)["runId"]
    observed = []
    executed = []
    original_execute = h.service.executor.execute
    async def checked_execute(**kwargs):
        durable = h.service.final.get(item["id"])["attempts"][kwargs["attempt"]["attemptId"]]
        assert kwargs["attempt"] == durable
        executed.append(copy.deepcopy(kwargs["attempt"]))
        return await original_execute(**kwargs)
    monkeypatch.setattr(h.service.executor, "execute", checked_execute)
    def before_effect_receipt():
        state = h.service.final.get(item["id"])
        observed.append(copy.deepcopy(next(iter(state["attempts"].values()))))
    h.provider.after_send = before_effect_receipt
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(item["id"])
    attempt = next(iter(state["attempts"].values()))
    assert observed[0]["ownerEpoch"] == attempt["ownerEpoch"] == epoch
    assert len(executed) == 1 and executed[0] == observed[0]
    assert observed[0]["attemptId"] == attempt["attemptId"]
    assert state["runs"][rid]["state"] == "completed" and len(h.provider.calls) == 1
    for stale_epoch, stale_generation, message in ((epoch - 1, 1, "ownerEpoch"), (epoch, 2, "generation")):
        with pytest.raises(fc.ContractError, match=message):
            with h.service.transaction() as db:
                job = h.service._get(db, "job", item["id"])
                h.service.final.ledger.finish_attempt(job, h.root, attempt["attemptId"], "completed",
                    expected_epoch=stale_epoch, expected_generation=stale_generation)
    assert h.service.final.get(item["id"])["attempts"][attempt["attemptId"]] == attempt
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1


def test_epoch2_retry_creates_new_attempt_and_preserves_effect_key_and_prior_attempt(h):
    owner = hold_epoch(h, 2)
    item = enabled(h)
    h.provider.fail_once = "0"
    rid = launch(h, item)["runId"]
    asyncio.run(h.service.dispatch_once())
    first = copy.deepcopy(next(iter(h.service.final.get(item["id"])["attempts"].values())))
    assert first["ownerEpoch"] == 2 and first["generation"] == 1 and first["state"] == "failed"
    assert run_control(h, rid, "retry", "retry-epoch2", stepId="s0").status_code == 202
    before = h.service.final.get(item["id"])
    with pytest.raises(fc.ContractError, match="identity already exists"):
        with h.service.transaction() as db:
            job = h.service._get(db, "job", item["id"])
            h.service.final.ledger.begin_attempt(job, h.root, rid, "s0",
                attempt_id=first["attemptId"], owner_epoch=3, retry=True)
    assert h.service.final.get(item["id"]) == before
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(item["id"])
    assert len(state["attempts"]) == 2 and state["attempts"][first["attemptId"]] == first
    second = next(a for a in state["attempts"].values() if a["attemptId"] != first["attemptId"])
    assert second["ownerEpoch"] == 2 and second["generation"] == 2 and second["state"] == "succeeded"
    assert h.provider.calls[0][1] == h.provider.calls[1][1]
    assert state["runs"][rid]["state"] == "completed"
