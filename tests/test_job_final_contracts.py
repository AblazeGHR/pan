"""FC01-FC04 contract tests for the Job final model (WP01).

The IDs map to ``docs/design/job-unification/JOB_FINAL_MODEL_20261025.md``
§12.1. Every test is a pure contract check: no store, no scheduler, no service.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import jsonschema
import pytest

from packages.jobs import final_contracts as fc

DESIGN_DOC = Path(__file__).resolve().parents[1] / "docs" / "design" / "job-unification" / "JOB_FINAL_MODEL_20261025.md"

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


# ── Fixtures ───────────────────────────────────────────────────────────────


def process_contract(contract_id: str = "process-exit") -> dict:
    return {
        "id": contract_id,
        "schema": {"type": "object"},
        "terminalProofSchema": {"type": "object"},
    }


def action_definition(**overrides) -> dict:
    value = {
        "name": "Run a process",
        "inputSchema": {
            "type": "object",
            "properties": {"argv": {"type": "array"}},
            "additionalProperties": False,
            "required": ["argv"],
        },
        "outputSchema": {"type": "object"},
        "providerRef": {"id": "core.process", "releaseDigest": DIGEST_C, "generation": 1},
        "execution": {"operation": "process.run", "config": {}},
        "effectClass": "write_local",
        "requiredCapabilities": ["process"],
        "completionContracts": [process_contract()],
        "defaultCompletion": "process-exit",
        "cancel": {"supported": True},
        "reconcile": {"supported": False},
        "retry": {"idempotent": False, "sameKeySupported": True, "requiresProof": True},
        "system": True,
    }
    value.update(overrides)
    return value


def step(step_id: str = "prepare", action_id: str = "act_process", **overrides) -> dict:
    value = {
        "stepId": step_id,
        "actionRef": {"id": action_id, "revision": 2, "digest": DIGEST_B},
        "arguments": {"argv": {"from": "input", "path": "argv"}},
        "completion": {"contract": "process-exit"},
    }
    value.update(overrides)
    return value


def job_definition(**overrides) -> dict:
    value = {
        "name": "Daily digest",
        "inputSchema": {
            "type": "object",
            "properties": {"argv": {"type": "array"}},
            "additionalProperties": False,
            "required": ["argv"],
        },
        "arguments": {"argv": ["echo", "hi"]},
        "steps": [step()],
        "retention": {"policyRef": "policy_default"},
    }
    value.update(overrides)
    return value


def trigger_definition(**overrides) -> dict:
    value = {
        "name": "On scheduler tick",
        "eventTypes": ["scheduler.tick"],
        "creatorSelector": {"mode": "ids", "ids": ["creator_1"]},
        "targetJobId": "job_1",
    }
    value.update(overrides)
    return value


def creator_definition(**overrides) -> dict:
    value = {
        "name": "Scheduler instance",
        "providerRef": {
            "type": "time",
            "id": "scheduler",
            "releaseDigest": DIGEST_C,
            "generation": 1,
        },
        "configSchemaRef": {"id": "scheduler_config", "revision": 1, "digest": DIGEST_D},
        "config": {"rules": []},
        "eventTypes": ["scheduler.tick"],
        "requiredCapabilities": [],
    }
    value.update(overrides)
    return value


def template_definition(**overrides) -> dict:
    value = {
        "name": "Daily template",
        "jobDefinition": job_definition(),
        "composition": {
            "creators": [{"mode": "create", "key": "creator", "definition": creator_definition()}],
            "triggers": [
                {
                    "key": "trigger",
                    "definition": trigger_definition(),
                    "targetJobKey": "job",
                    "creatorKeys": ["creator"],
                }
            ],
        },
    }
    value.update(overrides)
    return value


def event_envelope(**overrides) -> dict:
    value = {
        "schemaVersion": 2,
        "eventId": "evt_1",
        "eventType": "scheduler.tick",
        "eventSchemaRevision": 1,
        "creatorId": "creator_1",
        "creatorRevision": 1,
        "occurrenceKey": "2026-10-25T09:00:00+08:00",
        "occurredAt": "2026-10-25T09:00:00+08:00",
        "payload": {"driveId": "drive_1"},
    }
    value.update(overrides)
    return value


DEFINITION_FIXTURES = {
    "action": action_definition,
    "trigger": trigger_definition,
    "event_creator": creator_definition,
    "job": job_definition,
    "template": template_definition,
}


def object_document(kind: str, definition: dict, **overrides) -> dict:
    value = {
        "schemaVersion": 2,
        "id": f"{kind}_1",
        "profileId": "profile_1",
        "createdBy": {"type": "user", "id": "user_1"},
        "owner": {"type": "session", "id": "session_1"},
        "createdAt": "2026-10-25T00:00:00Z",
        "updatedAt": "2026-10-25T00:00:00Z",
        "state": "published" if kind in {"action", "template"} else "enabled",
        "stateRevision": 1,
        fc.VERSION_FIELD[kind]: 1,
    }
    if kind == "action":
        value["enabled"] = True
    if kind == "event_creator":
        value["ownerEpoch"] = 1
        value["providerGeneration"] = 1
    value.update(overrides)
    value.update(copy.deepcopy(definition))
    return value


def action_record(
    *,
    state: str = "published",
    revision: int = 2,
    digest: str = DIGEST_B,
    enabled: bool = True,
    contracts: tuple[str, ...] = ("process-exit",),
) -> dict:
    return {
        "id": "act_process",
        "revision": revision,
        "digest": digest,
        "state": state,
        "enabled": enabled,
        "definition": {
            "completionContracts": [
                {
                    "id": contract,
                    "schema": {"type": "object", "properties": {"argv": {"type": "array"}}},
                    "terminalProofSchema": {"type": "object"},
                }
                for contract in contracts
            ]
        },
    }


class Resolver:
    """Minimal in-memory resolver; unseen objects look not-found."""

    def __init__(self, records: dict[tuple[str, str], dict]) -> None:
        self.records = records

    def __call__(self, kind: str, object_id: str):
        return self.records.get((kind, object_id))


def expect_error(code: str, call, *, path: str | None = None, reason: str | None = None) -> fc.ContractError:
    with pytest.raises(fc.ContractError) as info:
        call()
    error = info.value
    assert error.code == code, error.as_dict()
    if path is not None:
        assert error.path == path, error.as_dict()
    if reason is not None:
        assert error.details.get("reason") == reason, error.as_dict()
    return error


# ── FC01: six objects, legal/illegal schema, read-only fields ──────────────


@pytest.mark.parametrize("kind", sorted(DEFINITION_FIXTURES))
def test_fc01_each_definition_object_accepts_a_minimal_valid_definition(kind):
    value = fc.validate_definition(kind, DEFINITION_FIXTURES[kind]())
    assert value["description"] == ""
    if kind == "job":
        assert value["overlap"] == {"mode": "forbid", "maxActive": 1, "maxPending": 0}
        assert value["recovery"] == {
            "automatic": False,
            "retryIf": [],
            "maxAttempts": 3,
            "backoffSeconds": ["5", "15", "60"],
            "stableResetSeconds": "300",
        }
        assert value["notification"] == {"on": [], "recipients": []}
    if kind == "trigger":
        assert value["eventFilter"] == {}
        assert value["condition"] is None
        assert value["arguments"] == {}


def test_fc01_event_publish_envelope_is_valid_and_has_no_editable_definition():
    envelope = fc.validate_event_publish(event_envelope())
    assert envelope["eventType"] == "scheduler.tick"
    assert fc.event_fact_digest(envelope) == fc.digest(
        {field: envelope[field] for field in fc.EVENT_FACT_FIELDS}
    )
    expect_error(
        "unsupported_operation",
        lambda: fc.validate_definition("event", envelope),
        reason="unsupported_operation",
    )


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("action", "effectClass"),
        ("trigger", "targetJobId"),
        ("event_creator", "configSchemaRef"),
        ("job", "steps"),
        ("template", "composition"),
    ],
)
def test_fc01_missing_required_field_is_rejected(kind, field):
    definition = DEFINITION_FIXTURES[kind]()
    definition.pop(field)
    expect_error("invalid_schema", lambda: fc.validate_definition(kind, definition), reason="missing_field")


@pytest.mark.parametrize("kind", sorted(DEFINITION_FIXTURES))
def test_fc01_unknown_field_is_rejected(kind):
    definition = DEFINITION_FIXTURES[kind]()
    definition["mysteryField"] = True
    error = expect_error(
        "invalid_schema", lambda: fc.validate_definition(kind, definition), reason="unknown_field"
    )
    assert error.details["field"] == "mysteryField"


def _set_path(value: dict, path: str, replacement) -> None:
    parts = path.split(".")
    target = value
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    last = parts[-1]
    if isinstance(target, list):
        target[int(last)] = replacement
    else:
        target[last] = replacement


@pytest.mark.parametrize(
    ("kind", "path", "value"),
    [
        pytest.param("action", "effectClass", "write", id="action-effect-class"),
        pytest.param(
            "job", "overlap", {"mode": "serial", "maxActive": 1, "maxPending": 0}, id="job-overlap-mode"
        ),
        pytest.param("event_creator", "providerRef.type", "cron", id="creator-provider-type"),
        pytest.param(
            "job", "steps.0.arguments.argv.from", "env", id="job-binding-source"
        ),
        pytest.param("job", "steps.0.completion.contract", 7, id="job-contract-type"),
    ],
)
def test_fc01_invalid_enum_or_type_is_rejected(kind, path, value):
    definition = DEFINITION_FIXTURES[kind]()
    _set_path(definition, path, value)
    error = expect_error("invalid_schema", lambda: fc.validate_definition(kind, definition))
    assert error.details.get("reason") in {"invalid_enum", "invalid_type"}


@pytest.mark.parametrize(
    ("kind", "path", "value"),
    [
        pytest.param("action", "completionContracts", [], id="empty-contracts"),
        pytest.param(
            "trigger", "creatorSelector", {"mode": "type", "ids": ["c"]}, id="selector-mode"
        ),
        pytest.param(
            "job", "steps.0.arguments.argv", {"from": "literal", "value": 1, "path": "extra"}, id="literal-extra-key"
        ),
    ],
)
def test_fc01_invalid_variant_is_rejected(kind, path, value):
    definition = DEFINITION_FIXTURES[kind]()
    _set_path(definition, path, value)
    error = expect_error("invalid_schema", lambda: fc.validate_definition(kind, definition))
    assert error.details.get("reason") in {"no_variant_matched", "invalid_value"}


def test_fc01_condition_errors_use_the_condition_error_code():
    definition = trigger_definition(condition={"field": "a", "op": "contains", "value": 1})
    expect_error("invalid_condition", lambda: fc.validate_definition("trigger", definition), reason="invalid_enum")
    deep = {"field": "a", "op": "eq", "value": 1}
    for _ in range(fc.MAX_CONDITION_DEPTH + 1):
        deep = {"not": deep}
    expect_error(
        "invalid_condition",
        lambda: fc.validate_definition("trigger", trigger_definition(condition=deep)),
        reason="condition_depth_exceeded",
    )


@pytest.mark.parametrize("kind", sorted(DEFINITION_FIXTURES))
@pytest.mark.parametrize("field", ["id", "profileId", "createdAt", "updatedAt", "stateRevision"])
def test_fc01_server_fields_are_read_only(kind, field):
    definition = DEFINITION_FIXTURES[kind]()
    definition[field] = "server"
    error = expect_error(
        "invalid_schema", lambda: fc.validate_definition(kind, definition), reason="read_only_field"
    )
    assert error.details["field"] == field


@pytest.mark.parametrize("kind", sorted(DEFINITION_FIXTURES))
def test_fc01_write_body_accepts_only_definition_revision_and_request_key(kind):
    definition = DEFINITION_FIXTURES[kind]()
    body = {"definition": definition, "expectedRevision": 0, "requestKey": "create-1"}
    result = fc.validate_write(kind, body, create=True)
    assert result["expectedRevision"] == 0
    assert result["requestKey"] == "create-1"
    assert len(result["requestDigest"]) == 64
    expect_error(
        "invalid_schema",
        lambda: fc.validate_write(kind, {**body, "operationId": "x"}, create=True),
        reason="unknown_field",
    )


def test_fc01_object_envelopes_validate_for_all_six_objects():
    assert fc.validate_object("action", object_document("action", action_definition()))["enabled"] is True
    assert fc.validate_object("job", object_document("job", job_definition()))["configRevision"] == 1
    assert fc.validate_object("trigger", object_document("trigger", trigger_definition()))["revision"] == 1
    assert (
        fc.validate_object(
            "event_creator", object_document("event_creator", creator_definition())
        )["ownerEpoch"]
        == 1
    )
    assert fc.validate_object("template", object_document("template", template_definition()))["state"] == "published"

    record = {
        **event_envelope(),
        "emittedAt": "2026-10-25T01:00:01Z",
        "receivedAt": "2026-10-25T01:00:02Z",
        "profileId": "profile_1",
    }
    record["factDigest"] = fc.event_fact_digest(record)
    assert fc.validate_object("event", record)["factDigest"] == record["factDigest"]

    expect_error(
        "invalid_schema",
        lambda: fc.validate_object("action", object_document("action", action_definition(), state="enabled")),
        reason="invalid_enum",
    )


def test_fc01_runtime_records_use_the_declared_state_vocabulary():
    run = {
        "runId": "run_1",
        "profileId": "profile_1",
        "jobId": "job_1",
        "configRevision": 3,
        "planDigest": DIGEST_A,
        "admissionId": "adm_1",
        "cause": {"type": "manual"},
        "state": "queued",
        "stateRevision": 0,
        "transitionSeq": 0,
        "createdAt": "2026-10-25T00:00:00Z",
    }
    step_record = {
        "runId": "run_1",
        "stepId": "prepare",
        "state": "pending",
        "stateRevision": 0,
        "attemptCount": 0,
    }
    attempt = {
        "attemptId": "attempt_1",
        "runId": "run_1",
        "stepId": "prepare",
        "state": "preparing",
        "ownerEpoch": 1,
        "generation": 1,
    }
    effect = {
        "effectKey": {"runId": "run_1", "stepId": "prepare", "targetId": "local", "operationOrdinal": 0},
        "attemptId": "attempt_1",
        "outcome": "unknown",
        "receipt": {},
        "createdAt": "2026-10-25T00:00:00Z",
    }
    operation = {
        "operationId": "op_1",
        "requestKey": "k1",
        "principal": {"type": "user", "id": "user_1"},
        "profileId": "profile_1",
        "operation": "create",
        "state": "accepted",
        "requestDigest": DIGEST_A,
        "expectedRevision": 0,
        "objectRefs": [],
        "createdAt": "2026-10-25T00:00:00Z",
    }
    admission = {
        "admissionId": "adm_1",
        "profileId": "profile_1",
        "jobId": "job_1",
        "configRevision": 1,
        "cause": {"type": "event", "eventId": "evt_1", "triggerId": "trigger_1"},
        "authority": {"type": "agent", "id": "agent_1"},
        "state": "skipped",
        "reason": "overlap",
        "createdAt": "2026-10-25T00:00:00Z",
    }
    for kind, record in (
        ("run", run),
        ("step", step_record),
        ("attempt", attempt),
        ("effect", effect),
        ("operation", operation),
        ("admission", admission),
    ):
        assert fc.validate_record(kind, record)

    assert set(fc.schema_artifacts()["run.schema.json"]["properties"]["state"]["enum"]) == set(fc.RUN_STATES)
    assert set(fc.schema_artifacts()["step.schema.json"]["properties"]["state"]["enum"]) == set(fc.STEP_STATES)
    assert set(fc.schema_artifacts()["attempt.schema.json"]["properties"]["state"]["enum"]) == set(
        fc.ATTEMPT_STATES
    )
    assert set(fc.schema_artifacts()["operation.schema.json"]["properties"]["state"]["enum"]) == set(
        fc.OPERATION_STATES
    )
    assert set(fc.schema_artifacts()["effect.schema.json"]["properties"]["outcome"]["enum"]) == set(
        fc.EFFECT_OUTCOMES
    )
    assert set(fc.schema_artifacts()["admission.schema.json"]["properties"]["state"]["enum"]) == set(
        fc.ADMISSION_STATES
    )

    expect_error(
        "invalid_schema",
        lambda: fc.validate_record("run", {**run, "state": "finished"}),
        reason="invalid_enum",
    )
    expect_error(
        "unsupported_operation",
        lambda: fc.validate_record("nope", run),
        reason="unsupported_operation",
    )

def test_fc01_capacity_limits_are_explicit():
    too_many_steps = job_definition(steps=[step(f"s{index}") for index in range(fc.MAX_JOB_STEPS + 1)])
    expect_error("invalid_schema", lambda: fc.validate_definition("job", too_many_steps), reason="limit_exceeded")

    too_many_filters = trigger_definition(eventFilter={f"field{index}": 1 for index in range(17)})
    expect_error(
        "invalid_schema", lambda: fc.validate_definition("trigger", too_many_filters), reason="limit_exceeded"
    )

    oversized = action_definition(description="x" * (fc.MAX_DEFINITION_BYTES + 1))
    expect_error("capacity_exceeded", lambda: fc.validate_definition("action", oversized), reason="capacity_exceeded")

    creators = [
        {"mode": "create", "key": f"c{index}", "definition": creator_definition()}
        for index in range(fc.MAX_COMPOSITION_OBJECTS - 10)
    ]
    triggers = [
        {
            "key": f"t{index}",
            "definition": trigger_definition(),
            "targetJobKey": "job",
            "creatorKeys": ["c0"],
        }
        for index in range(20)
    ]
    oversized_composition = template_definition(
        composition={"creators": creators, "triggers": triggers}
    )
    expect_error(
        "capacity_exceeded",
        lambda: fc.validate_definition("template", oversized_composition),
        reason="capacity_exceeded",
    )


def test_fc01_committed_schema_artifacts_match_the_generator():
    assert fc.check_schema_artifacts() == []
    artifacts = fc.schema_artifacts()
    names = set(artifacts)
    assert {"common.schema.json", "contract_manifest.json", "job.schema.json", "event_record.schema.json"} <= names
    for name, document in artifacts.items():
        if name.endswith(".schema.json"):
            assert document["$schema"] == fc.SCHEMA_DRAFT
            jsonschema.Draft202012Validator.check_schema(document)
    manifest = artifacts["contract_manifest.json"]
    assert manifest["contractVersion"] == 2
    assert set(manifest["objects"]) == set(fc.OBJECT_KINDS)
    assert manifest["errorCodes"]["revision_conflict"]["http"] == 409
    schema_files = {name for name in names if name.endswith(".schema.json")}
    assert schema_files
    on_disk = {path.name for path in fc.SCHEMA_DIRECTORY.iterdir() if path.suffix == ".json"}
    assert on_disk == names


def test_fc01_design_document_job_example_parses_and_validates():
    text = DESIGN_DOC.read_text(encoding="utf-8")
    block = re.search(r'```json\n(\{\n  "schemaVersion": 2,\n  "id": "job_example".*?)```', text, re.S)
    assert block, "design §2.4 JSON example is missing"
    example = fc.loads(block.group(1))
    assert example["schemaVersion"] == 2

    # The example is definition content: envelope fields are server-assigned,
    # Action Refs carry a digest in the contract, and retention is required.
    definition = {
        key: value
        for key, value in example.items()
        if key not in {"schemaVersion", "id", "configRevision", "owner"}
    }
    definition["name"] = "job_example"
    definition["retention"] = {"policyRef": "policy_default"}
    for item in definition["steps"]:
        item["actionRef"]["digest"] = DIGEST_D
    validated = fc.validate_definition("job", definition)
    assert validated["recovery"]["backoffSeconds"] == ["5", "15", "60"]
    assert validated["overlap"] == example["overlap"]


def test_fc01_json_input_rejects_duplicate_keys_and_non_finite_numbers():
    expect_error("invalid_schema", lambda: fc.loads('{"a": 1, "a": 2}'), reason="duplicate_key")
    expect_error("invalid_schema", lambda: fc.loads('{"a": NaN}'))
    expect_error("invalid_schema", lambda: fc.loads("{"))


# ── FC02: versions, CAS, idempotency ──────────────────────────────────────


@pytest.mark.parametrize("value", [1, 3, "2", True, None])
def test_fc02_schema_version_must_be_exactly_two(value):
    expect_error(
        "unsupported_schema_version",
        lambda: fc.validate_object("job", {"schemaVersion": value}),
        reason="unsupported_schema_version",
    )


def test_fc02_expected_revision_rules_for_create_and_update():
    body = {"definition": job_definition(), "expectedRevision": 0, "requestKey": "k1"}
    assert fc.validate_write("job", body, create=True)["expectedRevision"] == 0
    expect_error("invalid_schema", lambda: fc.validate_write("job", body), reason="invalid_integer")
    expect_error(
        "invalid_schema",
        lambda: fc.validate_write("job", {**body, "expectedRevision": 2}, create=True),
        reason="invalid_integer",
    )
    assert fc.validate_write("job", {**body, "expectedRevision": 2})["expectedRevision"] == 2


def test_fc02_revision_conflict_reports_current_revision():
    error = expect_error("revision_conflict", lambda: fc.check_revision(3, 2), reason="revision_conflict")
    assert error.details["currentRevision"] == 3
    assert error.status == 409
    assert fc.check_revision(3, 3) == 3


def test_fc02_content_and_publish_increment_revision_state_ops_do_not():
    publish = fc.check_definition_operation("action", "draft", "publish")
    assert publish == {"state": "published", "versionDelta": 1, "stateRevisionDelta": 0}
    pause = fc.check_definition_operation("job", "enabled", "pause")
    assert pause == {"state": "paused", "versionDelta": 0, "stateRevisionDelta": 1}
    disable = fc.check_definition_operation("action", "published", "disable")
    assert disable == {"state": "published", "versionDelta": 0, "stateRevisionDelta": 1}
    assert fc.next_revision(4) == 5
    expect_error("revision_conflict", lambda: fc.next_revision(4, expected=3))


def test_fc02_idempotent_replay_for_same_key_and_content():
    scope = fc.idempotency_scope({"type": "user", "id": "user_1"}, "profile_1", "create")
    digest = fc.request_digest("create", {"definition": "x"})
    record = {"scope": scope, "requestDigest": digest, "operationId": "op_1", "state": "pending"}
    assert fc.idempotency_outcome(record, scope=scope, request_digest=digest) == "replay"
    assert fc.idempotency_outcome(None, scope=scope, request_digest=digest) == "new"


def test_fc02_idempotency_conflict_for_same_key_different_content():
    scope = fc.idempotency_scope({"type": "agent", "id": "agent_1"}, "profile_1", "update")
    record = {"scope": scope, "requestDigest": "0" * 64, "operationId": "op_1", "state": "pending"}
    error = expect_error(
        "idempotency_conflict",
        lambda: fc.idempotency_outcome(record, scope=scope, request_digest="1" * 64),
        reason="idempotency_conflict",
    )
    assert error.details["operationId"] == "op_1"


def test_fc02_idempotency_scope_is_per_principal_and_pending_records_are_not_purged():
    user_scope = fc.idempotency_scope({"type": "user", "id": "user_1"}, "profile_1", "create")
    agent_scope = fc.idempotency_scope({"type": "agent", "id": "user_1"}, "profile_1", "create")
    other_profile = fc.idempotency_scope({"type": "user", "id": "user_1"}, "profile_2", "create")
    assert len({user_scope, agent_scope, other_profile}) == 3
    assert fc.idempotency_outcome({"scope": agent_scope}, scope=user_scope, request_digest="x") == "new"
    for state in ("accepted", "pending", "unknown"):
        assert fc.idempotency_purgeable({"state": state}) is False
    for state in ("completed", "rejected"):
        assert fc.idempotency_purgeable({"state": state}) is True
    expect_error(
        "unsupported_operation",
        lambda: fc.idempotency_scope({"type": "user", "id": "u"}, "p", "nope"),
        reason="unsupported_operation",
    )


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("", id="empty"),
        pytest.param("x" * 201, id="too-long"),
        pytest.param("bad\nkey", id="control-character"),
        pytest.param(" padded ", id="surrounding-space"),
    ],
)
def test_fc02_request_key_bounds(key):
    body = {"definition": job_definition(), "expectedRevision": 0, "requestKey": key}
    expect_error("invalid_schema", lambda: fc.validate_write("job", body, create=True), reason="invalid_request_key")


def test_fc02_event_receipts_replay_or_conflict_on_the_fact_digest():
    envelope = event_envelope()
    stored = {"eventId": envelope["eventId"], "factDigest": fc.event_fact_digest(envelope)}
    assert fc.event_receipt_outcome(None, envelope) == "new"
    assert fc.event_receipt_outcome(stored, envelope) == "replay"

    different = event_envelope(payload={"driveId": "drive_2"})
    error = expect_error(
        "idempotency_conflict",
        lambda: fc.event_receipt_outcome(stored, different),
        reason="event_fact_conflict",
    )
    assert error.details["eventId"] == envelope["eventId"]
    assert error.details["storedDigest"] != error.details["incomingDigest"]


# ── FC03: bindings, complete Template, unpublished Actions ────────────────


def _binding_job() -> dict:
    return job_definition(
        steps=[
            step(
                "prepare",
                arguments={
                    "literal": {"from": "literal", "value": {"from": "step", "path": "keep"}},
                    "argv": {"from": "input", "path": "argv"},
                    "tick": {"from": "event", "path": "payload.driveId"},
                    "who": {"from": "system", "path": "system.sessionId"},
                },
                completion={"contract": "process-exit", "parameters": {"argv": ["echo"]}},
            ),
            step(
                "publish",
                arguments={"text": {"from": "step", "stepId": "prepare", "path": "stdout"}},
            ),
        ]
    )


def test_fc03_binding_tagged_union_accepts_the_five_declared_forms():
    value = fc.validate_definition("job", _binding_job())
    literal = value["steps"][0]["arguments"]["literal"]
    assert literal == {"from": "literal", "value": {"from": "step", "path": "keep"}}
    assert value["steps"][1]["arguments"]["text"] == {
        "from": "step",
        "stepId": "prepare",
        "path": "stdout",
    }


@pytest.mark.parametrize(
    "binding",
    [
        pytest.param({"from": "step", "stepId": "later", "path": "x"}, id="forward-step"),
        pytest.param({"from": "literal", "value": 1, "path": "extra"}, id="literal-extra-key"),
        pytest.param({"from": "input"}, id="missing-path"),
    ],
)
def test_fc03_binding_rejects_forward_and_malformed_references(binding):
    job = job_definition(
        steps=[
            step("prepare"),
            step("later", arguments={"value": binding}),
        ]
    )
    expect_error("invalid_schema", lambda: fc.validate_definition("job", job))


def test_fc03_binding_rejects_self_reference():
    job = job_definition(steps=[step("prepare", arguments={"argv": {"from": "step", "stepId": "prepare", "path": "x"}})])
    expect_error(
        "invalid_schema",
        lambda: fc.validate_definition("job", job),
        reason="unknown_or_forward_step_ref",
    )


def test_fc03_binding_rejects_undeclared_input_paths_and_unknown_system_fields():
    undeclared = job_definition(
        steps=[step("prepare", arguments={"argv": {"from": "input", "path": "undeclared"}})]
    )
    expect_error(
        "invalid_schema",
        lambda: fc.validate_definition("job", undeclared),
        path="definition.steps.0.arguments.argv.path",
        reason="undeclared_input_path",
    )
    unknown_system = job_definition(
        steps=[step("prepare", arguments={"argv": {"from": "system", "path": "system.nope"}})]
    )
    expect_error(
        "invalid_schema",
        lambda: fc.validate_definition("job", unknown_system),
        reason="unknown_system_field",
    )


@pytest.mark.parametrize(
    "binding",
    [
        pytest.param({"from": "step", "stepId": "s", "path": "x"}, id="step-source"),
        pytest.param({"from": "input", "path": "x"}, id="input-source"),
    ],
)
def test_fc03_trigger_bindings_reject_step_and_input_sources(binding):
    trigger = trigger_definition(arguments={"value": binding})
    expect_error(
        "invalid_schema",
        lambda: fc.validate_definition("trigger", trigger),
        reason="binding_source_unavailable",
    )


def test_fc03_action_reference_requires_published_exact_revision_and_digest():
    job = job_definition()
    resolver = Resolver({("action", "act_process"): action_record()})
    assert len(fc.validate_references("job", job, resolver)) == 1

    unpublished = Resolver({("action", "act_process"): action_record(state="draft")})
    expect_error(
        "reference_conflict",
        lambda: fc.validate_references("job", job, unpublished),
        reason="unpublished",
    )
    retired = Resolver({("action", "act_process"): action_record(state="retired")})
    expect_error("reference_conflict", lambda: fc.validate_references("job", job, retired), reason="unpublished")

    wrong_revision = Resolver({("action", "act_process"): action_record(revision=3)})
    expect_error(
        "reference_conflict",
        lambda: fc.validate_references("job", job, wrong_revision),
        reason="revision_mismatch",
    )
    wrong_digest = Resolver({("action", "act_process"): action_record(digest=DIGEST_C)})
    expect_error(
        "reference_conflict",
        lambda: fc.validate_references("job", job, wrong_digest),
        reason="digest_mismatch",
    )
    missing = Resolver({})
    expect_error("reference_conflict", lambda: fc.validate_references("job", job, missing), reason="not_found")


def test_fc03_disabled_action_blocks_admission_but_not_definition_editing():
    job = job_definition()
    disabled = Resolver({("action", "act_process"): action_record(enabled=False)})
    assert fc.validate_references("job", job, disabled)
    expect_error(
        "reference_conflict",
        lambda: fc.validate_references("job", job, disabled, require_enabled=True),
        reason="disabled",
    )


def test_fc03_step_completion_contract_must_be_declared_and_parameters_validated():
    job = job_definition()
    resolver = Resolver({("action", "act_process"): action_record(contracts=("provider-receipt",))})
    expect_error(
        "reference_conflict",
        lambda: fc.validate_references("job", job, resolver),
        path="definition.steps.0.completion.contract",
        reason="completion_contract_unsupported",
    )
    invalid_parameters = job_definition(
        steps=[step(completion={"contract": "process-exit", "parameters": {"argv": "not-an-array"}})]
    )
    expect_error(
        "invalid_schema",
        lambda: fc.validate_references("job", invalid_parameters, Resolver({("action", "act_process"): action_record()})),
        path="definition.steps.0.completion.parameters.argv",
    )


def test_fc03_template_composition_requires_unique_and_resolvable_local_keys():
    duplicated = template_definition()
    duplicated["composition"]["triggers"].append(copy.deepcopy(duplicated["composition"]["triggers"][0]))
    expect_error(
        "invalid_schema",
        lambda: fc.validate_definition("template", duplicated),
        reason="duplicate_local_key",
    )

    dangling = template_definition()
    dangling["composition"]["triggers"][0]["creatorKeys"] = ["absent"]
    expect_error(
        "reference_conflict",
        lambda: fc.validate_definition("template", dangling),
        reason="dangling_local_key",
    )

    wrong_target = template_definition()
    wrong_target["composition"]["triggers"][0]["targetJobKey"] = "other"
    expect_error(
        "reference_conflict",
        lambda: fc.validate_definition("template", wrong_target),
        reason="dangling_local_key",
    )

    ambiguous_creator = template_definition()
    ambiguous_creator["composition"]["creators"][0] = {
        "mode": "create",
        "key": "creator",
        "definition": creator_definition(),
        "ref": {"id": "creator_1", "revision": 1, "digest": DIGEST_A},
    }
    expect_error("invalid_schema", lambda: fc.validate_definition("template", ambiguous_creator))


def test_fc03_template_cannot_embed_trigger_links_or_unfilled_parameter_holes():
    with_links = template_definition()
    with_links["jobDefinition"]["triggerLinks"] = [{"triggerId": "trigger_1"}]
    expect_error(
        "reference_conflict",
        lambda: fc.validate_definition("template", with_links),
        reason="template_links_from_composition",
    )

    hole = template_definition(
        jobDefinition=job_definition(
            inputSchema={
                "type": "object",
                "properties": {"argv": {"type": "array"}},
                "additionalProperties": False,
                "required": ["argv"],
            },
            arguments={},
        )
    )
    expect_error("invalid_schema", lambda: fc.validate_definition("template", hole))


def test_fc03_trigger_target_and_job_links_must_stay_consistent():
    trigger = trigger_definition(targetJobId="job_1")
    draft_job = Resolver({("job", "job_1"): {"id": "job_1", "revision": 1, "state": "draft"}})
    expect_error(
        "reference_conflict",
        lambda: fc.validate_references("trigger", trigger, draft_job),
        reason="target_unavailable",
    )
    enabled_job = Resolver({("job", "job_1"): {"id": "job_1", "revision": 1, "state": "enabled"}})
    assert fc.validate_references("trigger", trigger, enabled_job)

    linked = job_definition(triggerLinks=[{"triggerId": "trigger_1"}])
    mismatched = Resolver(
        {
            ("action", "act_process"): action_record(),
            ("trigger", "trigger_1"): {
                "id": "trigger_1",
                "revision": 1,
                "state": "enabled",
                "definition": {"targetJobId": "job_other"},
            },
        }
    )
    expect_error(
        "reference_conflict",
        lambda: fc.validate_references("job", linked, mismatched, object_id="job_1"),
        reason="target_mismatch",
    )


# ── FC04: unsupported operations and no legacy fallback ───────────────────


@pytest.mark.parametrize("kind", sorted(fc.OBJECT_OPERATIONS))
def test_fc04_supported_object_operations_are_accepted(kind):
    for operation in fc.OBJECT_OPERATIONS[kind]:
        assert fc.check_operation(kind, operation) == "definition"


@pytest.mark.parametrize(
    ("kind", "operation"),
    [
        ("action", "pause"),
        ("action", "resume"),
        ("action", "retry"),
        ("action", "cancel"),
        ("trigger", "retry"),
        ("trigger", "cancel"),
        ("template", "pause"),
        ("template", "resume"),
        ("template", "enable"),
        ("job", "publish"),
        ("event_creator", "publish"),
        ("event", "enable"),
        ("event", "pause"),
        ("event", "retire"),
        ("run", "publish"),
    ],
)
def test_fc04_unsupported_operations_return_explicit_errors(kind, operation):
    expect_error(
        "unsupported_operation",
        lambda: fc.check_operation(kind, operation),
        reason="unsupported_operation",
    )


def test_fc04_definition_transition_violations_are_explicit():
    expect_error(
        "unsupported_operation",
        lambda: fc.check_definition_operation("job", "draft", "resume"),
        reason="invalid_state_transition",
    )
    expect_error(
        "unsupported_operation",
        lambda: fc.check_definition_operation("job", "retired", "enable"),
        reason="invalid_state_transition",
    )
    expect_error(
        "unsupported_operation",
        lambda: fc.check_definition_operation("template", "published", "pause"),
        reason="unsupported_operation",
    )
    assert fc.check_definition_operation("trigger", "disabled", "enable")["state"] == "enabled"
    assert fc.check_definition_operation("trigger", "paused", "resume")["state"] == "enabled"
    assert fc.check_definition_operation("job", "retired", "transfer-owner")["state"] == "retired"


def test_fc04_run_layer_transitions_require_the_declared_operation():
    assert fc.check_transition("run", "queued", "running") == "advance"
    expect_error(
        "unsupported_operation",
        lambda: fc.check_transition("run", "queued", "completed"),
        reason="invalid_state_transition",
    )
    expect_error(
        "unsupported_operation",
        lambda: fc.check_transition("run", "completed", "retry_wait"),
        reason="invalid_state_transition",
    )
    expect_error(
        "unsupported_operation",
        lambda: fc.check_transition("run", "uncertain", "completed"),
        reason="operation_required",
    )
    assert fc.check_transition("run", "uncertain", "completed", operation="reconcile") == "reconcile"
    assert fc.check_transition("run", "failed", "retry_wait", operation="retry") == "retry"
    assert fc.check_transition("step", "failed", "running", operation="retry") == "retry"
    expect_error(
        "unsupported_operation",
        lambda: fc.check_transition("admission", "admitted", "rejected"),
        reason="invalid_state_transition",
    )
    expect_error(
        "invalid_schema",
        lambda: fc.check_transition("run", "nowhere", "running"),
        reason="unknown_state",
    )


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("job", "kind"),
        ("job", "schedule"),
        ("job", "legacyCompat"),
        ("job", "enabled"),
        ("trigger", "actionId"),
        ("trigger", "steps"),
        ("trigger", "retry"),
        ("trigger", "maxConcurrency"),
        ("action", "input_schema"),
        ("template", "enabled"),
        ("event_creator", "cursor"),
        ("event_creator", "producedCount"),
    ],
)
def test_fc04_legacy_fields_are_rejected_without_fallback(kind, field):
    definition = DEFINITION_FIXTURES[kind]()
    definition[field] = "legacy"
    error = expect_error(
        "invalid_schema", lambda: fc.validate_definition(kind, definition), reason="legacy_field"
    )
    assert error.details["field"] == field


def test_fc04_legacy_event_wire_names_are_rejected():
    for field, value in (("type", "scheduler.tick"), ("schemaRevision", 1), ("id", "evt_1")):
        envelope = event_envelope()
        envelope[field] = value
        error = expect_error(
            "invalid_schema", lambda: fc.validate_event_publish(envelope), reason="legacy_field"
        )
        assert error.details["field"] == field
    # Trusted identity is bound by the publish service, never self-reported.
    expect_error("invalid_schema", lambda: fc.validate_event_publish({**event_envelope(), "system": {}}))


def test_fc04_error_codes_are_a_closed_set_with_http_mapping():
    assert len(fc.ERROR_CODES) == len(set(fc.ERROR_CODES)) == 18
    assert set(fc.ERROR_HTTP_STATUS) == set(fc.ERROR_CODES)
    assert set(fc.ERROR_HTTP_STATUS.values()) <= {400, 401, 403, 404, 409, 422, 429, 503}
    assert fc.ERROR_HTTP_STATUS["reconciliation_required"] == 409
    assert fc.ERROR_HTTP_STATUS["capability_unsupported"] == 422
    assert fc.ERROR_HTTP_STATUS["capacity_exceeded"] == 429
    with pytest.raises(ValueError):
        fc.ContractError("made_up_code", "x")
    error = fc.ContractError("not_found", "hidden", path="id", details={"reason": "not_found"})
    assert error.as_dict() == {
        "code": "not_found",
        "message": "hidden",
        "path": "id",
        "details": {"reason": "not_found"},
    }


def test_fc04_operation_result_requires_the_documented_envelope():
    ok = {
        "operationId": "op_1",
        "requestKey": "k1",
        "status": "accepted",
        "objectRefs": [{"id": "job_1", "revision": 1, "digest": DIGEST_A}],
        "links": {},
    }
    assert fc.validate_operation_result(ok) is ok
    expect_error(
        "invalid_schema",
        lambda: fc.validate_operation_result({"operationId": "op_1"}),
        reason="missing_field",
    )
    expect_error(
        "invalid_schema",
        lambda: fc.validate_operation_result({**ok, "status": "done"}),
        reason="invalid_enum",
    )
    expect_error(
        "unsupported_operation",
        lambda: fc.validate_operation_result({**ok, "operation": "fly"}, known_operations=set(fc.OPERATIONS)),
        reason="unsupported_operation",
    )
