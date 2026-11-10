"""Authorization checks use the canonical FinalService boundary.

The former Action Runtime writer/executor is retired; these tests retain the
permission and fail-closed assertions on the successor service.
"""
import asyncio
import pytest
from packages.actions.store import Store
from packages.jobs.final_service import bind
from packages.jobs import final_contracts as fc
from tests.support.canonical_host import build, USER

def test_retired_runtime_writer_cannot_mutate(tmp_path):
    with pytest.raises(PermissionError, match="retired"):
        Store(tmp_path / "actions").put("actions", {"id": "legacy"})

def test_canonical_service_is_readable_and_authentication_is_fail_closed(tmp_path):
    h = build(tmp_path)
    try:
        assert any(item["id"] == "owned_message" for item in h.service.actions(actor=USER)["items"])
        denied = {kind: {name: False for name in names} for kind, names in h.automation.auth.PERMISSION_SCHEMA.items()}
        denied['action']['manageSystemActions'] = False
        h.automation.auth.change_permissions(denied, 0)
        with pytest.raises(fc.ContractError, match="Authorization revoked"):
            h.service.actions(actor={"type": "agent", "id": "retired-agent"})
    finally:
        asyncio.run(h.service.close())
        asyncio.run(h.automation.close())

def test_legacy_trigger_dispatch_surface_is_retired():
    from packages.automation.engine import Engine
    from packages.automation.store import Ledger
    for name in ('execute', 'run_attempt'):
        assert not hasattr(Engine, name), name
    for name in ('claim', 'finish'):
        assert not hasattr(Ledger, name), name

def test_retired_runtime_read_path_does_not_create_execution(tmp_path):
    store = Store(tmp_path / "actions")
    with pytest.raises(PermissionError):
        store.put("groups", {"id": "legacy_group"})
    assert not (tmp_path / "actions" / "registry.json").exists()

def test_canonical_service_binding_uses_owned_profile(tmp_path):
    h = build(tmp_path)
    try:
        assert h.service.profile_id.startswith("profile_")
        assert h.service.root == tmp_path / "canonical"
    finally:
        asyncio.run(h.service.close())
        asyncio.run(h.automation.close())

def test_sensitive_legacy_command_parameters_are_never_written(tmp_path):
    secret = "SENSITIVE_PARAMETER_SENTINEL"
    with pytest.raises(PermissionError):
        Store(tmp_path / "actions").put("actions", {"id": "legacy", "secret": secret})
    assert secret not in str(list(tmp_path.rglob("*")))
