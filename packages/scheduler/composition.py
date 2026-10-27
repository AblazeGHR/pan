"""Recoverable Creator/Job/Trigger composition and Template instantiation.

Transport and authorization remain WP06 concerns. The caller supplies trusted
authorization evidence and a port for Job/Trigger writes; this module owns stable
IDs, Creator references, phase checkpoints, and the unified activation gate.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Protocol

from packages.jobs import final_contracts as contracts

from .creators import CreatorStore
from .providers import legacy_source_plan

PHASES = (
    "reserved", "disabled_objects_created", "links_verified",
    "activation_committed", "active", "complete",
)


class CompositionPort(Protocol):
    def create_disabled(self, operation: Mapping[str, Any]) -> None: ...
    def verify_links(self, operation: Mapping[str, Any]) -> None: ...
    def commit_activation(self, operation: Mapping[str, Any], token: str) -> None: ...
    def activate(self, operation: Mapping[str, Any], token: str, enabled: bool) -> None: ...
    def complete(self, operation: Mapping[str, Any]) -> None: ...
    def cancel(self, operation: Mapping[str, Any]) -> None: ...


class NullCompositionPort:
    """Creator-only compositions; WP03/WP02 adapters implement the full graph."""

    def create_disabled(self, operation): pass
    def verify_links(self, operation): pass
    def commit_activation(self, operation, token): pass
    def activate(self, operation, token, enabled): pass
    def complete(self, operation): pass
    def cancel(self, operation): pass


class CompositionCoordinator:
    def __init__(self, creators: CreatorStore, port: CompositionPort | None = None):
        self.creators = creators
        self.port = port or NullCompositionPort()

    @staticmethod
    def _validate_graph(graph):
        if not isinstance(graph, dict) or set(graph) - {"job", "creators", "triggers"}:
            raise ValueError("Invalid desiredGraph")
        creators = graph.get("creators", [])
        triggers = graph.get("triggers", [])
        if not isinstance(creators, list) or not isinstance(triggers, list) or len(creators) + len(triggers) > 256:
            raise ValueError("Composition accepts at most 256 Creator/Trigger objects")
        keys = set()
        for spec in creators:
            if not isinstance(spec, dict) or spec.get("mode") not in {"create", "reference"}:
                raise ValueError("Invalid CreatorSpec")
            key = spec.get("key")
            if not isinstance(key, str) or not key or key in keys:
                raise ValueError("Creator keys must be unique")
            keys.add(key)
            if spec["mode"] == "create" and set(spec) != {"mode", "key", "definition"}:
                raise ValueError("Create CreatorSpec requires definition")
            if spec["mode"] == "reference" and set(spec) != {"mode", "key", "ref"}:
                raise ValueError("Reference CreatorSpec requires ref")
        for spec in triggers:
            if not isinstance(spec, dict) or not {"key", "definition", "targetJobKey", "creatorKeys"} <= set(spec):
                raise ValueError("Invalid TriggerSpec")
            if spec["targetJobKey"] != "job" or spec["key"] in keys:
                raise ValueError("Trigger key/targetJobKey is invalid")
            if not set(spec["creatorKeys"]) <= keys:
                raise ValueError("Trigger references an undeclared Creator")
            keys.add(spec["key"])

    def reserve(self, *, request_key: str, desired_graph, expected_refs=(),
                authorization_evidence=None, old_graph_digest=None, activate=False,
                operation="create", legacy_sources=()):
        contracts.check_request_key(request_key)
        self._validate_graph(desired_graph)
        request = {
            "desiredGraph": desired_graph, "expectedRefs": list(expected_refs),
            "activate": bool(activate), "oldGraphDigest": old_graph_digest,
        }
        if legacy_sources:
            request["legacySources"] = list(legacy_sources)
        request_digest = contracts.request_digest(operation, request)
        existing = self.creators.composition_for_key(request_key)
        if existing:
            if existing["requestDigest"] != request_digest:
                raise contracts.ContractError("idempotency_conflict", "Composition requestKey changed")
            return existing
        operation_id = "comp_" + contracts.digest({"requestKey": request_key, "desiredGraph": desired_graph})[:24]

        def allocated(kind, key):
            return kind + "_" + contracts.digest({"operationId": operation_id, "kind": kind, "key": key})[:24]

        creator_ids = {
            spec["key"]: spec["ref"]["id"] if spec["mode"] == "reference" else allocated("creator", spec["key"])
            for spec in desired_graph.get("creators", [])
        }
        object_ids = {
            "job": allocated("job", "job"), "creators": creator_ids,
            "triggers": {spec["key"]: allocated("trg", spec["key"]) for spec in desired_graph.get("triggers", [])},
        }
        resolved_graph = copy.deepcopy(desired_graph)
        for spec in resolved_graph.get("triggers", []):
            definition = spec["definition"]
            definition["targetJobId"] = object_ids["job"]
            if definition.get("creatorSelector", {}).get("mode") == "ids":
                definition["creatorSelector"]["ids"] = [creator_ids[key] for key in spec["creatorKeys"]]
        record = {
            "operationId": operation_id, "requestKey": request_key, "operation": operation,
            "requestDigest": request_digest,
            "expectedRefs": copy.deepcopy(list(expected_refs)), "objectIds": object_ids,
            "desiredGraph": resolved_graph,
            "authorizationEvidence": copy.deepcopy(authorization_evidence or {}),
            "oldGraphDigest": old_graph_digest or contracts.digest(None),
            "legacySources": copy.deepcopy(list(legacy_sources)),
            "activate": bool(activate), "phase": "reserved", "status": "pending", "error": None,
        }
        return self.creators.save_composition(record)

    def compose(self, **request):
        operation = self.reserve(**request)
        if operation["phase"] == "complete":
            return operation
        if operation["status"] == "cancelled":
            raise contracts.ContractError("unsupported_operation", "Cancelled composition cannot resume")
        operation["status"] = "pending"
        operation["error"] = None
        operation = self.creators.save_composition(operation)
        try:
            return self._resume(operation)
        except Exception as exc:
            latest = self.creators.composition_for_key(operation["requestKey"])
            if latest["phase"] == "activation_committed":
                for spec in latest["desiredGraph"].get("creators", []):
                    if spec["mode"] == "create":
                        self.creators.deactivate_composition(
                            latest["objectIds"]["creators"][spec["key"]], latest["operationId"],
                        )
            latest.update(status="failed", error=str(exc)[:1000])
            self.creators.save_composition(latest)
            raise

    def _checkpoint(self, operation, phase):
        if PHASES.index(phase) != PHASES.index(operation["phase"]) + 1:
            raise ValueError("Invalid composition phase transition")
        operation["phase"] = phase
        operation["status"] = "complete" if phase == "complete" else "pending"
        return self.creators.save_composition(operation)

    def _resume(self, operation):
        graph = operation["desiredGraph"]
        ids = operation["objectIds"]
        created = [spec for spec in graph.get("creators", []) if spec["mode"] == "create"]
        if operation["phase"] == "reserved":
            for spec in created:
                self.creators.create(
                    spec["definition"], request_key=operation["operationId"] + ":creator:" + spec["key"],
                    creator_id=ids["creators"][spec["key"]], state="disabled",
                    created_by=operation["authorizationEvidence"].get("principal"),
                    owner=operation["authorizationEvidence"].get("principal"),
                    activation={"committed": False, "operationId": operation["operationId"]},
                )
            self.port.create_disabled(copy.deepcopy(operation))
            operation = self._checkpoint(operation, "disabled_objects_created")
        if operation["phase"] == "disabled_objects_created":
            self._restore_legacy_sources(operation)
            for expected in operation["expectedRefs"]:
                actual = self.creators.ref(expected["id"])
                if actual["revision"] != expected["revision"] or actual["digest"] != expected["digest"]:
                    raise contracts.ContractError("reference_conflict", "Expected Creator Ref changed")
            for spec in graph.get("creators", []):
                if spec["mode"] != "reference":
                    continue
                actual = self.creators.ref(spec["ref"]["id"])
                if actual != spec["ref"]:
                    raise contracts.ContractError("reference_conflict", "Shared Creator Ref changed")
            job_id = ids["job"]
            for spec in graph.get("triggers", []):
                for key in spec["creatorKeys"]:
                    self.creators.bind_link(
                        ids["creators"][key], ids["triggers"][spec["key"]], job_id, operation["operationId"],
                    )
            self.port.verify_links(copy.deepcopy(operation))
            operation = self._checkpoint(operation, "links_verified")
        if operation["phase"] == "links_verified":
            token = "activation_" + contracts.digest({
                "operationId": operation["operationId"], "objectIds": ids,
                "oldGraphDigest": operation["oldGraphDigest"],
            })[:48]
            self.port.commit_activation(copy.deepcopy(operation), token)
            for spec in created:
                self.creators.commit_activation(ids["creators"][spec["key"]], operation["operationId"], token)
            operation["activationToken"] = token
            operation = self._checkpoint(operation, "activation_committed")
        if operation["phase"] == "activation_committed":
            for spec in created:
                self.creators.activate_composition(
                    ids["creators"][spec["key"]], operation["operationId"],
                    operation["activationToken"], operation["activate"],
                )
            self.port.activate(copy.deepcopy(operation), operation["activationToken"], operation["activate"])
            operation = self._checkpoint(operation, "active")
        if operation["phase"] == "active":
            self.port.complete(copy.deepcopy(operation))
            operation = self._checkpoint(operation, "complete")
        return operation

    def _restore_legacy_sources(self, operation):
        """Adopt reviewed simple cursors before any composition can activate.

        Pending publications and partially shared cohorts stay historical. All
        old edges must have explicit successors; no implicit half-source switch.
        Runtime only reads the resulting canonical cursor/links afterwards.
        """
        graph = operation["desiredGraph"]
        ids = operation["objectIds"]
        seen = set()
        with self.creators.transaction() as db:
            for migration in operation.get("legacySources", []):
                if not isinstance(migration, dict) or set(migration) != {
                    "creatorKey", "importKey", "sourceId", "timezone", "triggerKeys",
                }:
                    raise ValueError("Invalid legacySources migration binding")
                key = migration["creatorKey"]
                if key in seen:
                    raise ValueError("Duplicate legacy Creator binding")
                seen.add(key)
                spec = next((s for s in graph.get("creators", []) if s["key"] == key), None)
                if spec is None or spec["mode"] != "create":
                    raise contracts.ContractError("reference_conflict", "Legacy migration requires an owned disabled Creator")
                history = self.creators.legacy_history(migration["importKey"])
                if history is None:
                    raise contracts.ContractError("reference_conflict", "Reviewed Scheduler history import required")
                snapshot = history["snapshot"]
                source = next((s for s in snapshot["sources"] if s["id"] == migration["sourceId"]), None)
                if source is None:
                    raise contracts.ContractError("reference_conflict", "Legacy source missing from history")
                definition, occurrence = legacy_source_plan(source, timezone_name=migration["timezone"])
                if definition != spec["definition"] or operation["activate"] and not source["enabled"]:
                    raise contracts.ContractError("reference_conflict", "Migration definition/state differs from the legacy source")
                old_links = [link for link in snapshot["links"] if link["sourceId"] == source["id"]]
                mapping = migration["triggerKeys"]
                if (not isinstance(mapping, dict) or set(mapping) != {link["triggerId"] for link in old_links}
                        or len({link["jobId"] for link in old_links}) > 1
                        or len(set(mapping.values())) != len(mapping)):
                    raise contracts.ContractError("reference_conflict", "Legacy source cohort is incomplete or shared across Jobs")
                for trigger_key in mapping.values():
                    trigger = next((t for t in graph.get("triggers", []) if t["key"] == trigger_key), None)
                    if trigger is None or key not in trigger["creatorKeys"]:
                        raise contracts.ContractError("reference_conflict", "Legacy link has no canonical successor")
                item = self.creators._read_db(db, ids["creators"][key])
                origin = {"importKey": migration["importKey"], "sourceId": source["id"],
                          "sourceDigest": history["sourceDigest"]}
                adopted = db.execute("SELECT importKey,creatorId FROM legacy_scheduler_adoptions WHERE sourceId=?",
                                     (source["id"],)).fetchone()
                if adopted and tuple(adopted) != (migration["importKey"], item["id"]):
                    raise contracts.ContractError("reference_conflict", "Legacy source already has a canonical owner")
                if item.get("legacyHistory") == origin:
                    continue
                if item["state"] != "disabled" or item.get("pendingPublication") or item["drives"]:
                    raise contracts.ContractError("reference_conflict", "Migration Creator has already driven")
                cursor = item["cursor"]["rules"]["legacy"]
                cursor.update(nextFireAt=occurrence["at"] if occurrence else None, nextOccurrence=occurrence)
                item["legacyHistory"] = origin
                db.execute("INSERT INTO legacy_scheduler_adoptions VALUES(?,?,?)",
                           (source["id"], migration["importKey"], item["id"]))
                self.creators._write(db, item)

    def cancel(self, request_key):
        operation = self.creators.composition_for_key(request_key)
        if operation is None:
            raise KeyError(request_key)
        if PHASES.index(operation["phase"]) >= PHASES.index("active"):
            raise contracts.ContractError("unsupported_operation", "Active composition cannot be cancelled")
        self.port.cancel(copy.deepcopy(operation))
        for spec in operation["desiredGraph"].get("creators", []):
            if spec["mode"] == "create":
                self.creators.deactivate_composition(
                    operation["objectIds"]["creators"][spec["key"]], operation["operationId"],
                )
        self.creators.detach_operation_links(operation["operationId"])
        # Referenced/shared Creators are deliberately untouched. Owned drafts
        # remain disabled audit facts and can be purged only by Creator delete.
        operation["status"] = "cancelled"
        operation["error"] = None
        return self.creators.save_composition(operation)

    def instantiate_template(self, template, *, request_key: str, activate=False,
                             authorization_evidence=None):
        value = contracts.validate_definition("template", template)
        graph = {
            "job": value["jobDefinition"], "creators": value["composition"]["creators"],
            "triggers": value["composition"]["triggers"],
        }
        expected = [spec["ref"] for spec in graph["creators"] if spec["mode"] == "reference"]
        return self.compose(
            request_key=request_key, desired_graph=graph, expected_refs=expected,
            authorization_evidence=authorization_evidence, activate=activate,
            operation="instantiate-template",
        )
