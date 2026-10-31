"""Migration review metadata for approved runtime fields, never an executor.

Input bindings already exist. They do not by themselves supply a manager's
reservation/acceptance fence or per-target receipt. Until that contract is
wired, retain the legacy source rather than publish frozen literal Jobs.
"""
from __future__ import annotations


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
