"""Auditable user instruction receipt, explicitly not a cryptographic signature.

The trusted caller pins the receipt digest independently of the context/bundle.
An embedded approval, agent-authored grant or placeholder cannot authorize it.
Trust is the operator/conversation provenance and external digest handoff, not
the mere fact that a JSON file contains the word 'user'.
"""
from __future__ import annotations

import copy
import hashlib

from packages.jobs import final_contracts as fc


def require(ok, message):
    if not ok:
        raise ValueError(message)

SCHEMA = "pan.job-final-migration.user-authorization/v1"
USER = {"type": "user", "id": "local"}
DECISIONS = {"migrate": "all-simple", "authorizationSource": "user", "owner": "user",
             "preservedDisposition": "unrecognized", "automaticDelete": False,
             "replayUnknown": False, "settleUnknown": False, "activeIds": []}


def payload_digest(context):
    value = copy.deepcopy(context)
    value.pop("userAuthorizationDigest", None)
    return fc.digest(value)


def record_instruction(*, instruction, reference, recorded_at, packet_digest, context):
    """Record an already received user instruction. This does not grant trust.

    The caller must retain the conversation source and independently pin the
    returned digest when handing it to apply/runtime activation.
    """
    receipt = {"schema": SCHEMA, "authorizationSource": "user", "principal": USER, "owner": USER,
        "provenance": {"channel": "user", "source": "conversation", "reference": reference,
                       "instruction": instruction, "instructionSha256": hashlib.sha256(instruction.encode()).hexdigest(),
                       "recordedBy": "assistant-as-recorder; not the approver"},
        "recordedAt": recorded_at, "timeBasis": "receipt recording time; original message timestamp unavailable",
        "signature": None, "authentication": "trusted conversation/operator handoff; not a cryptographic signature",
        "decisions": DECISIONS, "packetDigest": packet_digest,
        "profileRoot": context["isolation"]["profileRoot"], "contextPayloadDigest": payload_digest(context),
        "selectedMigrationIds": sorted(context["selectedMigrationIds"]),
        "records": {mid: {"sourceFileSha256": p["originalDigest"], "recordSha256": p["recordDigest"]}
                    for mid, p in sorted(context["records"].items())}}
    receipt["receiptDigest"] = fc.digest(receipt)
    return receipt


def validate_receipt(receipt, expected_digest):
    require(isinstance(receipt, dict) and bool(expected_digest), "user_authorization: externally pinned receipt required")
    body = {k: v for k, v in receipt.items() if k != "receiptDigest"}
    require(receipt.get("schema") == SCHEMA and fc.digest(body) == receipt.get("receiptDigest") == expected_digest,
            "user_authorization: receipt digest mismatch")
    require(receipt.get("authorizationSource") == "user" and receipt.get("principal") == USER
            and receipt.get("owner") == USER and receipt.get("signature") is None,
            "user_authorization: user provenance required; signatures/placeholders are not accepted")
    provenance = receipt.get("provenance", {})
    text, ref = provenance.get("instruction", ""), provenance.get("reference", "")
    require(provenance.get("channel") == "user" and provenance.get("source") == "conversation"
            and isinstance(text, str) and len(text.strip()) >= 40
            and isinstance(ref, str) and ref.startswith("conversation/user/")
            and all(token not in ref.lower() for token in ("todo", "placeholder", "self-approved"))
            and provenance.get("instructionSha256") == hashlib.sha256(text.encode()).hexdigest(),
            "user_authorization: missing user instruction; placeholder/self-approval refused")
    fc.check_timestamp(receipt.get("recordedAt"), "recordedAt")
    require(receipt.get("decisions") == DECISIONS, "user_authorization: decision scope mismatch")
    require(isinstance(receipt.get("packetDigest"), str) and len(receipt["packetDigest"]) == 64,
            "user_authorization: packet scope missing")
    return receipt


def validate_context(context, receipt, expected_digest):
    validate_receipt(receipt, expected_digest)
    require(context.get("userAuthorizationDigest") == expected_digest
            and payload_digest(context) == receipt["contextPayloadDigest"], "user_authorization: context changed")
    require(sorted(context.get("selectedMigrationIds", [])) == receipt["selectedMigrationIds"]
            and set(context.get("records", {})) == set(receipt["records"]), "user_authorization: selection changed")
    isolation = context.get("isolation", {})
    require(isolation.get("mode") == "owned-offline-copy" and isolation.get("profileRoot") == receipt["profileRoot"],
            "user_authorization: isolated profile scope mismatch")
    for mid, proof in context["records"].items():
        require(proof.get("principal") == USER and proof.get("owner") == USER
                and {"sourceFileSha256": proof.get("originalDigest"), "recordSha256": proof.get("recordDigest")}
                == receipt["records"][mid], "user_authorization: per-ID owner/hash mismatch")
    return receipt


def validate_bundle(bundle, expected_digest):
    receipt = bundle.get("authorizationAudit")
    validate_context(bundle.get("authorizationContext", {}), receipt, expected_digest)
    require(fc.digest(bundle["authorizationContext"]) == bundle["contextDigest"], "user_authorization: bundle context mismatch")
    require(bundle["root"] == receipt["profileRoot"], "user_authorization: source profile mismatch")
    require({e["migrationId"] for e in bundle["entries"]} == set(receipt["records"]),
            "user_authorization: bundle selection mismatch")
    for entry in bundle["entries"]:
        require(entry["originalDigest"] == receipt["records"][entry["migrationId"]]["sourceFileSha256"],
                "user_authorization: bundle source hash changed")
    require(all(r["object"]["createdBy"] == USER and r["object"]["owner"] == USER
                and r["object"]["state"] == "disabled" for r in bundle["canonical"]),
            "user_authorization: canonical owner/state changed")
    return receipt
