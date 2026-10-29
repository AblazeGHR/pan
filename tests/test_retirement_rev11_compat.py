"""Revision11: old form fields compose the existing canonical Actions."""
import asyncio
import copy
import sys

import pytest

from packages.jobs.final_contracts import ContractError
from packages.jobs.final_service import normalize_legacy_form_fields
from tests.test_job_final_http_service import USER, enabled, launch
from tests.test_session_product_closure import config
from tests.test_job_automatic_recovery import job_definition, create, drive_until, policy, state
from tests.test_session_product_closure import h  # noqa: F401
from tests.test_job_automatic_recovery import env  # noqa: F401


def test_assign_legacy_aliases_map_to_product_arguments_and_preserve_text_source():
    fields = {"target": {"sessionId": "ses_owned"}, "message": "原文 bytes \u0001",
              "source": "agent", "sourceSessionId": "ses_source", "taskId": "task-1"}
    assert normalize_legacy_form_fields(fields) == {
        "sessionId": "ses_owned", "text": "原文 bytes \u0001", "source": "agent",
        "sourceSessionId": "ses_source", "taskId": "task-1"}
    explicit = normalize_legacy_form_fields(fields, canonical={
        "sessionId": "ses_owned", "text": "原文 bytes \u0001", "source": "agent",
        "sourceSessionId": "ses_source", "taskId": "task-1"})
    assert explicit["taskId"] == "task-1"


@pytest.mark.parametrize("fields", [
    {"sessionId": "a", "targetSessionId": "b", "text": "x"},
    {"sessionId": "a", "text": "x", "unknown": 1},
    {"sessionId": "a", "text": "x", "source": "spoof", "taskId": 4},
])
def test_assign_conflict_unknown_and_bad_source_fail_before_composition(fields):
    with pytest.raises(ContractError):
        normalize_legacy_form_fields(fields)


def test_shell_command_form_maps_to_explicit_argv_and_rejects_conflict():
    mapped = normalize_legacy_form_fields({"command": "echo legacy-shell", "cwd": "D:/tmp", "hideWindow": False})
    assert mapped["argv"][-1] == "echo legacy-shell"
    assert mapped["cwd"] == "D:/tmp" and mapped["hideWindow"] is False
    with pytest.raises(ContractError):
        normalize_legacy_form_fields({"command": "echo x", "argv": ["echo", "x"], "cwd": "D:/tmp"})
    with pytest.raises(ContractError):
        normalize_legacy_form_fields({"argv": ["echo", "x"], "cwd": "D:/tmp", "extra": 1})


def test_assign_mapper_reaches_real_owned_queue_without_spawn(h):
    mapped = normalize_legacy_form_fields({"targetSessionId": "ses_a", "text": "legacy assign",
                                           "source": "agent", "sourceSessionId": "ses_source", "taskId": "legacy-task"})
    job = enabled(h, config(h, "assign", **mapped))
    admitted = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    run = h.service.get_run(admitted["runId"], actor=USER)
    assert run["state"] == "completed"
    row = h.owned["ses_a"].queue_pending[0]
    assert row["text"] == "legacy assign" and row["source"] == "agent"
    assert row["sourceSessionId"] == "ses_source" and row["taskId"] == "legacy-task"
    assert len(h.service.final.get(job["id"])["attempts"]) == 1


def test_shell_mapper_reaches_real_owned_process_and_cold_receipt(env):
    mapped = normalize_legacy_form_fields({"argv": [sys.executable, "-c", "raise SystemExit(0)"],
                                           "cwd": str(env.tmp), "hideWindow": True})
    value = job_definition(env, recovery=policy(automatic=False, retryIf=[]))
    value["steps"][0]["arguments"] = {key: {"from": "literal", "value": val}
                                        for key, val in mapped.items()}
    jid, rid = create(env, value)
    asyncio.run(drive_until(env, jid, rid, "completed"))
    first = state(env, jid)
    assert first["runs"][rid]["state"] == "completed"
    assert len(first["attempts"]) == len(first["effects"]) == 1
    reopened = env.service.final.get(jid)
    assert reopened == first


def test_mapper_preserves_explicit_canonical_values_and_rejects_override():
    canonical = {"sessionId": "ses_a", "text": "canonical", "source": "agent"}
    assert normalize_legacy_form_fields({"sessionId": "ses_a", "text": "canonical", "source": "agent"}, canonical=canonical) == canonical
    with pytest.raises(ContractError):
        normalize_legacy_form_fields({"sessionId": "ses_a", "text": "legacy"}, canonical=canonical)
