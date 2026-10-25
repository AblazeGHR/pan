"""Pure, fail-closed v1 -> WP01 v2 mapping for the offline migration package.

Operator context supplies facts the inventory cannot establish. It is an
attestation, never a replacement for runtime authorization or fencing.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import jsonschema

from packages.jobs import final_contracts as fc

from .inventory import SIMPLE


class ApplyError(ValueError):
    """A migration prerequisite or transaction check failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ApplyError(message)


def timestamp(value, zone: str | None = None) -> str:
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        result = datetime.fromtimestamp(value, timezone.utc)
    else:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            require(bool(zone), "schedule_semantic_loss: timestamp has no timezone")
            tz = ZoneInfo(zone)
            first = result.replace(tzinfo=tz, fold=0)
            second = result.replace(tzinfo=tz, fold=1)
            require(first.utcoffset() == second.utcoffset(),
                    "schedule_semantic_loss: ambiguous/nonexistent local time")
            require(first.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == result,
                    "schedule_semantic_loss: nonexistent local time")
            result = first
    return result.isoformat()


def _literal(arguments: dict) -> dict:
    return {key: {"from": "literal", "value": value} for key, value in arguments.items()}


class Mapper:
    def __init__(self, context: dict):
        require(type(context.get("schemaVersion")) is int and context["schemaVersion"] == 1,
                "unsupported migration context version")
        self.context = context
        self.objects: list[dict] = []
        self.external: dict[tuple[str, str], dict] = {}
        for binding in context["actions"].values():
            action = binding["object"]
            fc.validate_object("action", action)
            definition = {k: v for k, v in action.items()
                          if k in fc.schema_artifacts()["action_definition.schema.json"]["properties"]}
            definition = fc.validate_definition("action", definition)
            require(action["state"] == "published" and action["enabled"],
                    "provider_unavailable: Action must be published and enabled")
            digest = fc.digest(definition)
            require(binding["ref"] == {"id": action["id"], "revision": action["revision"],
                                        "digest": digest}, "Action reference digest/revision mismatch")
            record = {**action, "definition": definition, "digest": digest}
            key = ("action", action["id"])
            require(key not in self.external or self.external[key] == record,
                    "conflicting Action bindings")
            self.external[key] = record

    def step(self, key: str, arguments: dict, step_id: str = "execute",
             completion_kind: str = "ordinary") -> dict:
        binding = self.context["actions"].get(key)
        require(binding is not None, f"missing_reference: Action binding {key}")
        completion = binding["completions"].get(completion_kind)
        require(completion is not None, f"missing completion mapping: {key}/{completion_kind}")
        # Literal values are checked against the pinned Action's input schema;
        # event/input/step bindings are checked structurally by WP01.
        props = binding["object"]["inputSchema"].get("properties", {})
        schema = binding["object"]["inputSchema"]
        require(schema.get("additionalProperties", True) is not False or not (set(arguments) - set(props)),
                "Action arguments contain unknown fields")
        require(not (set(schema.get("required", [])) - set(arguments)), "Action arguments miss required fields")
        try:
            for name, value in arguments.items():
                if value.get("from") == "literal":
                    jsonschema.validate(value["value"], props.get(name, {}))
            if all(v.get("from") == "literal" for v in arguments.values()):
                jsonschema.validate({k: v["value"] for k, v in arguments.items()}, schema)
        except jsonschema.ValidationError as exc:
            raise ApplyError("Action arguments violate pinned input schema") from exc
        return {"stepId": step_id, "actionRef": binding["ref"],
                "arguments": arguments, "completion": copy.deepcopy(completion)}

    def object(self, kind: str, object_id: str, definition: dict, proof: dict,
               source: dict, *, zone: str | None = None, revision: int = 1) -> dict:
        definition = fc.validate_definition(kind, definition)
        created = source.get("createdAt", source.get("created_at", proof["importedAt"]))
        updated = source.get("updatedAt", source.get("updated_at", created))
        value = {**definition, "schemaVersion": 2, "id": object_id,
                 "profileId": self.context["profileId"],
                 "createdBy": proof["principal"], "owner": proof["owner"],
                 "createdAt": timestamp(created, zone), "updatedAt": timestamp(updated, zone),
                 "state": "draft" if kind == "template" else "disabled",
                 "stateRevision": 1, fc.VERSION_FIELD[kind]: revision}
        if kind == "event_creator":
            value.update(ownerEpoch=proof["oldOwnerEpoch"] + 1,
                         providerGeneration=definition["providerRef"]["generation"])
        fc.validate_object(kind, value)
        self.objects.append({"kind": kind, "object": value})
        return value

    def config_job(self, config: dict, proof: dict) -> dict:
        completion_kind = config["completion"]["kind"]
        require(set(config["completion"]) == {"kind"},
                "unsupported_complex_instance: completion signal parameters need explicit mapping")
        old_steps = config.get("steps") or [{"stepId": "execute", "actionId": config["actionId"],
                                            "arguments": config["arguments"]}]
        steps = [self.step(s["actionId"], s["arguments"], s["stepId"], completion_kind)
                 for s in old_steps]
        policies = copy.deepcopy(proof["policies"])
        overlap, recovery = config["overlap"], config["recovery"]
        require(policies["overlap"]["mode"] == overlap["mode"], "overlap mode changed")
        require(policies["overlap"]["maxActive"] == (
            overlap["parallelLimit"] if overlap["mode"] == "parallel" else 1),
            "overlap concurrency changed")
        require(policies["recovery"] == {
            "automatic": recovery["enabled"], "maxAttempts": recovery["maxAttempts"],
            "backoffSeconds": [str(v) for v in recovery["backoffSec"]],
            "stableResetSeconds": str(recovery["stableResetSec"])}, "recovery policy changed")
        states = {"unknown": "uncertain", "cleaning": "settling"}
        require(policies["notification"]["on"] == list(dict.fromkeys(
            states.get(s, s) for s in config["notification"]["on"])),
                "notification states changed")
        require([r["targetRef"] for r in policies["notification"]["recipients"]] ==
                config["notification"].get("sessionIds", []), "notification targets changed")
        job = {"name": config["name"], "description": config["description"], "steps": steps, **policies}
        if config["maxRuns"] is not None:
            job["runLimit"] = {"maxRuns": config["maxRuns"], "counterDomain": "run-admission"}
        return job

    def schedule(self, entry: dict, proof: dict, *, max_events=None) -> dict:
        policy = proof["scheduler"]
        kind, zone = entry["kind"], entry.get("timezone")
        rule = {"ruleId": entry.get("id", "rule"), "type": kind}
        if kind == "once":
            rule["at"] = timestamp(entry["at"], zone)
        elif kind == "interval":
            require(policy["advanceMode"] == "grid", "schedule_semantic_loss: fixed anchor requires grid")
            rule.update(seconds=str(entry.get("intervalSec", entry.get("interval_sec"))),
                        anchor=timestamp(entry["anchor"], zone), advanceMode=policy["advanceMode"])
        elif kind == "cron":
            rule.update(expression=entry["cron"], timezone=zone, dstPolicy=policy["dstPolicy"])
        else:
            raise ApplyError("unsupported schedule kind")
        config = {"rules": [rule], "misfirePolicy": entry["misfirePolicy"],
                  "graceSeconds": str(entry.get("graceSec", policy["graceSeconds"])),
                  "pausePolicy": policy["pausePolicy"]}
        if max_events is not None:
            config["driveLimit"] = {"maxEvents": max_events, "initialProducedCount": 0}
        fc.validate_scheduler_config(config)
        return {"name": proof["name"] + " schedule", "providerRef": policy["providerRef"],
                "configSchemaRef": policy["configSchemaRef"], "config": config,
                "eventTypes": ["scheduler.tick"], "requiredCapabilities": policy["requiredCapabilities"]}

    def map(self, item: dict, source: dict) -> dict:
        require(item["classification"] == SIMPLE, "refusing non-simple record")
        mid = item["migrationId"]
        proof = copy.deepcopy(self.context["records"][mid])
        require(proof["originalDigest"] == item["originalDigest"], "plan_stale: context source changed")
        require(proof["oldOwnerStopped"] is True and type(proof["oldOwnerEpoch"]) is int
                and proof["oldOwnerEpoch"] >= 1 and bool(proof["fenceEvidence"])
                and bool(proof["authorizationEvidence"]), "missing authority/fence attestation")
        fc.check_timestamp(proof["importedAt"], "importedAt")
        authorization = source.get("authorization")
        if authorization:
            principal_id = authorization.get("userId") or authorization.get("agentId") or authorization.get("id")
            require(proof["principal"]["type"] == authorization["type"] and
                    (not principal_id or proof["principal"]["id"] == principal_id),
                    "unverified_authority: source principal changed")
        require(set(proof["policies"]) == {"overlap", "recovery", "notification", "retention"},
                "all four source policies must be explicit")
        proof["name"] = source.get("name", mid)
        def allocated(kind):
            return f"{kind}_{mid.removeprefix('mig1_')}"
        job_id = allocated("job")
        before = len(self.objects)
        typ = item["type"]
        entry = None
        zone = None
        desired = "enabled" if source.get("enabled", True) else "disabled"
        if source.get("paused"):
            desired = "paused"
        if typ in {"MIG-ACTION-JOB", "MIG-TEMPLATE"}:
            config = source["config"]
            job = self.config_job(config, proof)
            desired = "enabled" if config["enabled"] else "disabled"
            if source.get("paused"):
                desired = "paused"
            if config["schedule"]:
                entry = config["schedule"][0]
        elif typ == "MIG-TRIGGER":
            steps = source.get("steps") or [{"stepId": "execute", "actionId": source["actionId"],
                                             "arguments": source["arguments"]}]
            # Event bindings belong on Trigger -> Job admission, never in a Job Step.
            arguments = {}
            mapped = []
            for index, step in enumerate(steps):
                bindings = {}
                for key, value in step["arguments"].items():
                    if value["from"] == "event":
                        name = f"event_{index}_{key}"
                        arguments[name] = value
                        bindings[key] = {"from": "input", "path": name}
                    else:
                        bindings[key] = value
                mapped.append(self.step(step["actionId"], bindings, step["stepId"]))
            job = {"name": source["name"], "description": source.get("description", ""),
                   "steps": mapped, **proof["policies"]}
            require(job["recovery"]["automatic"] == source["retry"]["enabled"] and
                    job["recovery"]["maxAttempts"] == source["retry"]["maxAttempts"],
                    "Trigger retry policy changed")
            mode = source.get("executionMode", "serial")
            require(job["overlap"]["maxActive"] == (source.get("maxConcurrency", 1) if mode == "parallel" else 1),
                    "Trigger concurrency changed")
            trigger_id = allocated("trigger")
            trigger = {"name": source["name"], "description": source.get("description", ""),
                       "eventTypes": source["eventTypes"], "eventFilter": source.get("eventFilter", {}),
                       "condition": source.get("condition"), "arguments": arguments,
                       "creatorSelector": proof["creatorSelector"], "targetJobId": job_id}
            self.object("trigger", trigger_id, trigger, proof, source, revision=source["revision"])
            job["triggerLinks"] = [{"triggerId": trigger_id}]
        else:
            if typ == "MIG-BG":
                key = "process.exec"
                args = {k: source[k] for k in ("argv", "cwd", "hideWindow")}
                desired = "manual-only"
            elif typ == "MIG-MSG":
                key = "session.send-many" if source["kind"] == "session-broadcast" else "session.send"
                args = {"text": source["text"], "source": source["source"]}
                if source["kind"] == "session-broadcast":
                    args["sessionIds"] = list(dict.fromkeys(source["targetSessionIds"]))
                else:
                    args["sessionId"] = source["targetSessionId"]
                entry = {"kind": "once", "at": source["nextRunAt"], "misfirePolicy": "fire_now"}
            elif typ == "MIG-SCHED":
                legacy = item["sourceKind"] == "legacy-scheduled-task"
                entry = copy.deepcopy(source["schedule"] if legacy else source["schedule"][0])
                entry.setdefault("misfirePolicy", source.get("misfirePolicy", source.get("misfire_policy")))
                action = source.get("action", {"api": "assign"})
                api = action["api"]
                key = {"assign": "session.assign", "send_session": "session.send", "shell": "process.shell",
                       "send_qq": "qq.send"}[api]
                args = copy.deepcopy(action.get("args", {}))
                if api in {"assign", "send_session"}:
                    args.update(sessionId=source.get("targetSessionId", source.get("target_session_id")),
                                text=source["text"])
                elif api == "send_qq":
                    args["text"] = source["text"]
            else:
                raise ApplyError(f"unsupported mapping type: {typ}")
            job = {"name": source["name"], "description": source["description"],
                   "steps": [self.step(key, _literal(args))], **proof["policies"]}
        composition = {"creators": [], "triggers": []}
        if entry:
            zone = entry.get("timezone")
            maximum = source.get("maxRuns", source.get("max_runs")) if typ == "MIG-SCHED" else None
            creator = self.schedule(entry, proof, max_events=maximum)
            creator_id, trigger_id = allocated("creator"), allocated("trigger")
            trigger = {"name": source["name"] + " tick", "eventTypes": ["scheduler.tick"],
                       "creatorSelector": {"mode": "ids", "ids": [creator_id]}, "targetJobId": job_id}
            if typ == "MIG-TEMPLATE":
                composition["creators"] = [{"mode": "create", "key": "clock", "definition": creator}]
                composition["triggers"] = [{"key": "tick", "definition": trigger,
                                             "targetJobKey": "job", "creatorKeys": ["clock"]}]
            else:
                self.object("event_creator", creator_id, creator, proof, source, zone=zone)
                self.object("trigger", trigger_id, trigger, proof, source, zone=zone)
                job["triggerLinks"] = [{"triggerId": trigger_id, "creatorId": creator_id}]
        if typ == "MIG-TEMPLATE":
            self.object("template", allocated("template"), {
                "name": source["name"], "description": source["description"], "jobDefinition": job,
                "composition": composition, "provenance": {"sourceObjectId": item["id"],
                    "sourceRevision": source["revision"], "sourceDigest": item["recordDigest"].split(":")[-1]}},
                proof, source, zone=zone, revision=source["revision"])
        else:
            self.object("job", job_id, job, proof, source, zone=zone,
                        revision=source.get("configRevision", 1))
        return {"migrationId": mid, "targetIds": [v["object"]["id"] for v in self.objects[before:]],
                "desiredState": desired, "cursor": item["cursor"], "sourceRevision": source.get("revision"),
                "sourceAudit": {k: source[k] for k in ("authorization", "createdBy", "source", "creatorSessionId",
                    "sourceStruct", "targetStruct", "createdAt", "updatedAt", "created_at", "updated_at") if k in source},
                "historicalEvidenceOnly": {k: source[k] for k in ("exitCode", "logPath", "terminalEventId", "status",
                    "pid", "processCreatedAt", "processTreeEnded", "lastExecution", "lastStatus", "lastError") if k in source},
                "attestation": proof, "runtimeFenceVerified": False}

    def verify(self) -> None:
        records = dict(self.external)
        for record in self.objects:
            value = record["object"]
            key = (record["kind"], value["id"])
            require(key not in records, "duplicate target ID")
            records[key] = value
        for record in self.objects:
            kind, obj = record["kind"], record["object"]
            definition = {k: v for k, v in obj.items()
                          if k in fc.schema_artifacts()[kind + "_definition.schema.json"]["properties"]}
            fc.validate_references(kind, definition, lambda k, i: records.get((k, i)),
                                   object_id=obj["id"], require_enabled=True)
