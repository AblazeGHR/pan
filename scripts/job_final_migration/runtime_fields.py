"""Migration review metadata for approved runtime fields, never an executor.

Input bindings already exist. They do not by themselves supply a manager's
reservation/acceptance fence or per-target receipt. Until that contract is
wired, retain the legacy source rather than publish frozen literal Jobs.
"""
from __future__ import annotations

RUNTIME_OPT_IN = {"version": 1, "mode": "before-reservation"}
RUNTIME_PRODUCTS = {"session.assign": "product_assign_runtime_v1",
                    "session.send": "product_session_message_runtime_v1",
                    "session.send-many": "product_session_broadcast_runtime_v1"}


def opted_in(context: dict) -> bool:
    value = context.get("runtimeDelivery")
    if value is None:
        return False
    if value != RUNTIME_OPT_IN:
        raise ValueError("unsupported runtimeDelivery opt-in")
    return True


def runtime_definition(key, arguments, binding):
    """Consume the exact product declaration, preserving the published pin."""
    import copy
    from packages.jobs import final_contracts as fc
    from .mapping import require
    action = binding["object"]
    target = "sessionIds" if key == "session.send-many" else "sessionId"
    fields = [target, "text"]
    declaration = {"owner": "delivery-manager", "fields": fields,
                   "phase": "before-queue-acceptance", "fence": "per-target-attempt-effect",
                   "unknownPolicy": "query-original"}
    require(action["id"] == RUNTIME_PRODUCTS[key] and action.get("system") is True
            and action.get("runtimeBeforeAcceptance") == declaration
            and action["providerRef"] == {"id": "pan.typed", "generation": 3,
                "releaseDigest": fc.digest("pan.typed.session-runtime.v1")},
            "runtime_binding_unavailable: explicit pinned product runtime release required")
    require("deliveryRuntime" in fc.schema_artifacts()["job_definition.schema.json"]["properties"],
            "runtime_binding_unavailable: product runtime schema required")
    mapped = {k: {"from": "literal", "value": copy.deepcopy(v)} for k, v in arguments.items()}
    for field in fields:
        mapped[field] = {"from": "input", "path": "delivery." + field}
    input_properties = {target: ({"type": ["array", "null"], "items": {"type": "string"}}
                                if target == "sessionIds" else {"type": ["string", "null"]}),
                        "text": {"type": "string", "minLength": 1}}
    paths = ["delivery." + field for field in fields]
    extra = {"inputSchema": {"type": "object", "properties": {"delivery": {"type": "object",
                    "properties": input_properties, "required": fields, "additionalProperties": False}},
                "required": ["delivery"], "additionalProperties": False},
             "arguments": {"delivery": {field: copy.deepcopy(arguments[field]) for field in fields}},
             "deliveryRuntime": {"owner": "delivery-manager", "targetMode": "runtime", "inputFields": paths,
                 "allowedStates": ["pending", "reserved"], "concurrency": "job-config-cas+event-receipt",
                 "phase": "before-reservation", "audit": "final_operations", "unknownPolicy": "query-original"}}
    return mapped, extra


def delivery_binding_gap(source_type: str, source_kind: str | None, source: dict) -> dict | None:
    if source_type == "MIG-MSG":
        many = source.get("kind") == "session-broadcast"
        field, argument = ("targetSessionIds", "sessionIds") if many else ("targetSessionId", "sessionId")
    elif source_type == "MIG-SCHED":
        action = source.get("action") or {"api": "assign"}
        if not isinstance(action, dict) or action.get("api") not in {"assign", "send_session"}:
            # QQ runtime semantics require the pinned Action release to declare
            # them. A familiar field name alone is not such a declaration.
            return None
        field = "target_session_id" if source_kind == "legacy-scheduled-task" else "targetSessionId"
        argument = "sessionId"
    else:
        return None
    return {
        "policy": "target-runtime-fields-approved-20261031",
        "owner": "Creator/Delivery Manager",
        "allowedStates": ["pending", "reserved"],
        "effectiveStage": "event-published-before-queue-or-external-acceptance",
        "freezePerTargetAt": ["accepted", "success", "unknown", "partial"],
        "fields": [
            {"sourceField": field, "argument": argument,
             "proposedBinding": {"from": "input", "path": "delivery." + argument}},
            {"sourceField": "text", "argument": "text", "requiresActionDeclaration": "runtimeBeforeAcceptance",
             "proposedBinding": {"from": "input", "path": "delivery.text"}},
        ],
        "requiredEvidence": ["Event/dispatch identity", "runtimeRevision", "reservation fence",
                             "per-target receipt", "text revision", "receipt effective stage"],
        "audit": "original operation/requestKey and CAS; record selected revision/target/text digest in receipt",
        "unknownHandling": "query original accepted/unknown/partial identity; never blind retarget/resend",
        "knownNoAttemptHandling": "distinct from unknown; retain durable backlog under the original manager",
        "acceptanceState": "unverified",
        "bindingGenerated": False,
        "gap": "input binding exists; manager revision/reservation/receipt and Action declaration are not wired by this mapper",
        "followUp": "product owner wires approved manager contract; migration then re-reviews original source hashes",
    }
