"""P5b fixed system Action entrypoint contracts; no real Pan service."""
from __future__ import annotations

import asyncio
import pytest

from packages.core import lifecycle_action


def test_protected_action_retry_reuses_effect_identity_and_never_relaunches(tmp_path, monkeypatch):
    from packages.core import platform

    monkeypatch.setattr(platform, "supports", lambda capability: True)
    launches = []

    async def submit():
        return await lifecycle_action.submit_protected(
            root=tmp_path, operation="restart", port=43210, old_pid=41,
            old_pid_created_at=12.5, request_id="request-stable",
            launch=lambda *args: launches.append(args),
        )

    first = asyncio.run(submit())
    second = asyncio.run(submit())
    assert first["effectId"] == second["effectId"]
    assert first["receipt"]["receipt"]["code"] == "execution_pending"
    assert second["receipt"] == first["receipt"]
    assert len(launches) == 1


def test_fixed_definition_rejects_mutation_and_local_start_is_action_gated(tmp_path, monkeypatch):
    definition = lifecycle_action.fixed_definition()
    assert definition["id"] == "system_main_lifecycle"
    assert definition["system"] is True and definition["effect"] == "high_risk"
    monkeypatch.setattr(lifecycle_action, "fixed_definition", lambda: (_ for _ in ()).throw(ValueError("forged")))
    with pytest.raises(ValueError, match="forged"):
        lifecycle_action.execute_local("start", root=tmp_path)


def test_receipt_wait_timeout_is_explicit_and_does_not_retry(tmp_path):
    with pytest.raises(TimeoutError, match="canonical receipt=effect_missing"):
        lifecycle_action.wait_for_receipt(
            root=tmp_path, effect_id="effect_missing", operation="exit", timeout=.01, poll=.001,
        )


@pytest.mark.parametrize("operation", ["exit", "restart"])
def test_cli_uses_canonical_receipt_and_requires_identity_proof(tmp_path, monkeypatch, operation):
    from packages.core import cli

    record = {"pid": 41, "createdAt": 12.5}
    monkeypatch.setattr(cli, "verified", lambda root: (43210, record, {}))
    seen = []
    monkeypatch.setattr(cli, "request", lambda port, path, payload, instance: (
        seen.append((port, path, payload, instance)) or {"accepted": True, "effectId": "effect_1"}
    ))
    if operation == "exit":
        receipt = {"outcome": "succeeded", "receipt": {"phase": "offline", "offlineProof": {
            "ok": True, "root": str(tmp_path), "port": 43210, "oldPid": 41, "oldPidCreatedAt": 12.5}}}
    else:
        receipt = {"outcome": "succeeded", "receipt": {"phase": "ready", "readyProof": {
            "ok": True, "root": str(tmp_path), "port": 43210, "http": True,
            "newPid": 42, "newPidCreatedAt": 13.5, "listenerOwner": 42,
            "identity": {"ok": True, "root": str(tmp_path)}}}}
    monkeypatch.setattr(lifecycle_action, "run_wait_for_receipt", lambda **kwargs: receipt)
    result = cli.lifecycle(tmp_path, operation, 1)
    assert result["ok"] is True
    assert seen[0][1] == lifecycle_action.ACTION_ROUTE
    assert seen[0][2]["operation"] == operation
