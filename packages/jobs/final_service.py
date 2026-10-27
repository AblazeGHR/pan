"""Canonical definition/admission service over the WP02/WP04 ledgers.

The Creator database is the transaction boundary for definitions, Job
aggregates and HTTP operation receipts. FinalRuntime's store port reads those
same aggregates: dispatch never creates an independent Job or a second Run.
Legacy registries are neither imported nor upgraded on startup.
"""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import contextmanager
from pathlib import Path

import jsonschema

from packages.jobs import final_contracts as fc
from packages.jobs.chains import resolve as resolve_bindings
from packages.jobs.final_runtime import FinalRuntime
from packages.jobs.final_runtime.executor import TypedExecutor, WorkerSessionProvider
from packages.jobs.final_runtime.ledger import aggregate, new_id, now, principal
from packages.scheduler.creators import CreatorStore, CanonicalEventStore, CreatorRuntime, EventBus
from packages.scheduler.composition import CompositionCoordinator
from packages.jobs.runtime_owner import OwnerFence


PREFIX = "/api"
GRANTS = {"job": "job", "trigger": "trigger", "event_creator": "eventCreator", "template": "jobTemplate"}
ACTIVE = {"queued", "running", "retry_wait", "paused_at_boundary", "cancelling", "settling", "uncertain"}


def error(code, message):
    raise fc.ContractError(code, message)


def definition(kind, item):
    names = fc.schema_artifacts()[kind + "_definition.schema.json"]["properties"]
    return {key: copy.deepcopy(value) for key, value in item.items() if key in names}


def object_ref(kind, item):
    return {"id": item["id"], "revision": item[fc.VERSION_FIELD[kind]],
            "digest": fc.digest(definition(kind, item))}


class AggregateStore:
    """FinalRuntime's store port; mutations join the definition transaction."""
    def __init__(self, service):
        self.service = service

    def get(self, job_id):
        with self.service.transaction(readonly=True) as db:
            return copy.deepcopy(self.service._get(db, "job", job_id))

    def mutate(self, job_id, change):
        with self.service.transaction() as db:
            job = self.service._get(db, "job", job_id)
            result = change(job)
            self.service._save(db, "job", job_id, job)
            return copy.deepcopy(result)


class TriggerAdmission:
    """WP02 TriggerAdmissionPort implemented by the WP03 admission service."""
    def __init__(self, service):
        self.service = service

    async def admit(self, event, target):
        return await self.service.admit_event_async(event, target)


class FinalService:
    def __init__(self, root, auth, *, automation_ledger=None, executor=None, owner=None):
        self.root = Path(root).resolve()
        self.auth = auth
        self.automation_ledger = automation_ledger
        self.creators = CreatorStore(self.root)
        self.profile_id = self.creators.profile_id
        self.final = FinalRuntime(self.root, store=AggregateStore(self))
        self.events = CanonicalEventStore(self.root)
        self.bus = EventBus(self.events, self.creators.get)
        self.creator_runtime = CreatorRuntime(self.creators, self.bus, authorize=self._guard_creator)
        self.compositions = CompositionCoordinator(self.creators, self)
        self.admission = TriggerAdmission(self)
        self.executor = executor or TypedExecutor(session_provider=WorkerSessionProvider())
        self._product_executor = self.executor if executor is None else None
        self.task = None
        self.closed = False
        self.diagnostics = []
        self._dispatch_lock = asyncio.Lock()
        self.state_reader = None
        self.outbox_sender = None
        self._outbox_lock = asyncio.Lock()
        self.owner = owner or OwnerFence(self.root.parent)
        if self.owner.root != self.root.parent:
            error("reference_conflict", "Runtime owner belongs to another profile")
        self._adopt_owner = owner is not None
        self.legacy_roots = []
        # A reopened service can read before claim, but cannot bootstrap writes
        # through an already established authority.
        established = self.owner.status() is not None
        if not established:
            self._initialize_schema()
            self._install_product_actions()
        self.creators.owner_fence = self.owner
        self.events.owner_fence = self.owner

    def _initialize_schema(self):
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS final_documents(kind TEXT,id TEXT,document TEXT NOT NULL,PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS final_operations(id TEXT PRIMARY KEY,scope TEXT,requestKey TEXT,document TEXT NOT NULL,UNIQUE(scope,requestKey))")
            db.execute("CREATE TABLE IF NOT EXISTS final_authorities(kind TEXT,id TEXT,document TEXT NOT NULL,PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS final_event_receipts(eventId TEXT,triggerId TEXT,status TEXT,PRIMARY KEY(eventId,triggerId))")
            db.execute("CREATE TABLE IF NOT EXISTS final_event_operations(eventId TEXT,triggerId TEXT,operationId TEXT,factDigest TEXT,PRIMARY KEY(eventId,triggerId))")

    @contextmanager
    def transaction(self, *, readonly=False):
        if readonly:
            with self.creators.connection(readonly=True) as db:
                yield db
        else:
            with self.owner.writer(canonical=True), self.creators.transaction() as db:
                yield db

    @staticmethod
    def _get(db, kind, object_id):
        row = db.execute("SELECT document FROM final_documents WHERE kind=? AND id=?", (kind, object_id)).fetchone()
        if row is None:
            raise KeyError(object_id)
        return json.loads(row[0])

    @staticmethod
    def _save(db, kind, object_id, value):
        db.execute("INSERT OR REPLACE INTO final_documents VALUES(?,?,?)", (kind, object_id, fc.canonical_json(value)))

    @staticmethod
    def _all(db, kind):
        return [json.loads(row[0]) for row in db.execute("SELECT document FROM final_documents WHERE kind=? ORDER BY id", (kind,))]

    def _permit(self, actor, kind, capability, *, execution=False):
        if actor.get("type") == "user":
            return self.auth.permissions_status()["revision"]
        if actor.get("type") != "agent":
            error("authorization_denied", "Untrusted principal")
        allowed, revision = self.auth.permission_allowed(GRANTS.get(kind, kind), capability)
        if not allowed or (execution and actor.get("revision") != revision):
            error("authorization_denied", "Authorization revoked or capability denied")
        return revision

    def _envelope(self, kind, value, actor, *, object_id=None, state="draft"):
        item = {**copy.deepcopy(value), "schemaVersion": 2, "id": object_id or new_id(kind + "_"),
                "profileId": self.profile_id, "createdBy": principal(actor), "owner": principal(actor),
                "createdAt": now(), "updatedAt": now(), "state": state, "stateRevision": 0,
                fc.VERSION_FIELD[kind]: 1}
        if kind == "action":
            item["enabled"] = True
        return fc.validate_object(kind, item)

    def _public(self, kind, item):
        if kind == "job":
            item = item["canonical"]
        if kind == "event_creator":
            names = fc.schema_artifacts()["event_creator.schema.json"]["properties"]
            item = {key: value for key, value in item.items() if key in names}
        return fc.validate_object(kind, copy.deepcopy(item))

    def _object(self, db, kind, object_id):
        item = self.creators.get(object_id) if kind == "event_creator" else self._get(db, kind, object_id)
        if item is None:
            raise KeyError(object_id)
        return self._public(kind, item)

    def _resolver(self, db):
        def resolve(kind, object_id):
            try:
                if kind == "action":
                    item = self._get(db, kind, object_id)
                    return {**item, "definition": definition(kind, item), "digest": fc.digest(definition(kind, item))}
                return self._object(db, kind, object_id)
            except KeyError:
                return None
        return resolve

    def _references(self, db, kind, value, *, object_id=None, enabled=False):
        if kind == "job" and value.get("recovery", {}).get("automatic"):
            error("capability_unsupported", "Automatic recovery requires a separately bound recovery policy")
        checked = fc.validate_references(kind, value, self._resolver(db), object_id=object_id, require_enabled=enabled)
        if kind == "trigger":
            selector = value["creatorSelector"]
            if selector["mode"] == "ids":
                for cid in selector["ids"]:
                    item = self.creators.get(cid)
                    if item is None or item["state"] == "retired":
                        error("reference_conflict", "Creator unavailable")
        return checked

    def _scope(self, actor, operation):
        return fc.idempotency_scope(principal(actor), self.profile_id, "create" if operation == "run-again" else operation)

    def _operation(self, db, actor, operation, key, payload, kind, mutate):
        fc.check_request_key(key)
        scope = self._scope(actor, operation)
        fingerprint = fc.request_digest(operation, payload)
        row = db.execute("SELECT document FROM final_operations WHERE scope=? AND requestKey=?", (scope, key)).fetchone()
        if row:
            prior = json.loads(row[0])
            fc.idempotency_outcome({"scope": scope, "requestDigest": prior["requestDigest"]},
                                   scope=scope, request_digest=fingerprint)
            return {**copy.deepcopy(prior["response"]), "replayed": True}
        operation_id = new_id("op_")
        result, refs, pending = mutate(operation_id)
        response = {"operationId": operation_id, "requestKey": key, "status": "accepted" if pending else "completed",
                    "objectRefs": refs, "result": result, "links": {"self": PREFIX + "/operations/" + operation_id}}
        for name in ("runId", "admissionId"):
            if name in result:
                response[name] = result[name]
        fc.validate_operation_result(response)
        record = {"principal": principal(actor), "kind": kind, "operation": operation,
                  "requestDigest": fingerprint, "response": response, "createdAt": now()}
        db.execute("INSERT INTO final_operations VALUES(?,?,?,?)", (operation_id, scope, key, fc.canonical_json(record)))
        return response

    def _page(self, values, kind, cursor, limit, actor):
        # Cursor is opaque and bound to the visible principal/profile/kind.
        scope = fc.digest({"principal": principal(actor), "profile": self.profile_id, "kind": kind})
        offset = 0
        if cursor:
            import base64
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor))
                if set(token) != {"scope", "offset"} or token["scope"] != scope or type(token["offset"]) is not int or token["offset"] < 0:
                    raise ValueError()
                offset = token["offset"]
            except (ValueError, KeyError, TypeError):
                error("invalid_schema", "Invalid page cursor")
        items = values[offset:offset + limit]
        next_cursor = None
        if offset + limit < len(values):
            import base64
            next_cursor = base64.urlsafe_b64encode(fc.canonical_json({"scope": scope, "offset": offset + limit}).encode()).decode()
        return {"items": items, "nextCursor": next_cursor}

    def list(self, kind, *, actor, cursor=None, limit=100):
        self._permit(actor, kind, "read")
        with self.transaction(readonly=True) as db:
            items = self.creators.list() if kind == "event_creator" else self._all(db, kind)
            return self._page([self._public(kind, item) for item in items], kind, cursor, limit, actor)

    def get(self, kind, object_id, *, actor):
        self._permit(actor, kind, "read")
        with self.transaction(readonly=True) as db:
            return self._object(db, kind, object_id)

    def creator_providers(self, *, actor):
        self._permit(actor, "event_creator", "read")
        provider = self.creators.registry.providers[("time", "scheduler")]
        return {"items": [{"providerRef": {"type": provider.provider_type, "id": provider.provider_id,
            "generation": provider.generation, "releaseDigest": provider.release_digest},
            "configSchemaRef": provider.config_schema_ref, "requiredCapabilities": list(provider.capabilities),
            "eventTypes": ["scheduler.time.v2"], "configSchema": fc.schema_artifacts()["scheduler_config.schema.json"]}]}

    def _creator_references(self, db, creator_id):
        creator = self.creators.get(creator_id)
        if creator is None:
            raise KeyError(creator_id)
        references = []
        for trigger in self._all(db, "trigger"):
            selector = trigger["creatorSelector"]
            selected = (creator_id in selector.get("ids", []) if selector["mode"] == "ids" else
                selector.get("providerType") == creator["providerRef"]["type"] and
                bool(set(trigger["eventTypes"]) & set(creator["eventTypes"])))
            if not selected or trigger["state"] == "retired":
                continue
            job = self._get(db, "job", trigger["targetJobId"])["canonical"]
            references.append({"triggerId": trigger["id"], "name": trigger["name"],
                "revision": trigger["revision"], "stateRevision": trigger["stateRevision"],
                "jobId": job["id"], "jobName": job["name"], "jobRevision": job["configRevision"],
                "enabled": trigger["state"] == "enabled", "selector": copy.deepcopy(selector)})
        references.sort(key=lambda value: value["triggerId"])
        return {"references": references, "referenceDigest": fc.digest(references)}

    def creator_references(self, creator_id, *, actor):
        self._permit(actor, "event_creator", "read")
        self._permit(actor, "trigger", "read")
        self._permit(actor, "job", "read")
        with self.transaction(readonly=True) as db:
            return self._creator_references(db, creator_id)

    def _check_creator_references(self, db, creator_id, expected):
        if not isinstance(expected, str) or expected != self._creator_references(db, creator_id)["referenceDigest"]:
            error("reference_conflict", "Source subscribers changed; review references again")

    def write(self, kind, object_id, body, *, actor):
        self._permit(actor, kind, "create" if object_id is None else "updateConfig")
        allowed = {"definition", "expectedRevision", "requestKey", "requestDigest"}
        if kind == "event_creator":
            allowed.add("expectedReferences")
        if kind == "trigger":
            allowed.add("expectedCreatorRevisions")
        if set(body) - allowed:
            error("invalid_schema", "Unknown write fields")
        value = fc.validate_write(kind, {key: body[key] for key in ("definition", "expectedRevision", "requestKey")}, create=object_id is None)
        if "expectedReferences" in body:
            value["expectedReferences"] = body["expectedReferences"]
        if "expectedCreatorRevisions" in body:
            value["expectedCreatorRevisions"] = body["expectedCreatorRevisions"]
        with self.transaction() as db:
            def mutate(operation_id):
                if kind == "trigger" and "expectedCreatorRevisions" in body:
                    expected = body["expectedCreatorRevisions"]
                    selector = value["definition"]["creatorSelector"]
                    if not isinstance(expected, dict) or set(expected) != set(selector.get("ids", [])):
                        error("reference_conflict", "Review every selected Creator revision")
                    for cid, revision in expected.items():
                        creator = self.creators.get(cid)
                        if creator is None or type(revision) is not int or creator["revision"] != revision:
                            error("reference_conflict", "Selected Creator revision changed")
                old = self._object(db, kind, object_id) if object_id is not None else None
                if old:
                    if kind == "event_creator" and "expectedReferences" in body:
                        self._check_creator_references(db, object_id, body["expectedReferences"])
                    fc.check_revision(old[fc.VERSION_FIELD[kind]], value["expectedRevision"])
                    if old["state"] == "retired" or (kind == "template" and old["state"] == "published"):
                        error("reference_conflict", "Immutable or retired definition")
                self._references(db, kind, value["definition"], object_id=object_id)
                if kind == "event_creator":
                    if old is None:
                        item = self.creators.create(value["definition"], request_key=operation_id,
                                                    created_by=principal(actor), owner=principal(actor))
                    else:
                        item = self.creators.update(object_id, value["definition"], expected_revision=value["expectedRevision"], request_key=operation_id)
                    item = self._public(kind, item)
                else:
                    item = self._envelope(kind, value["definition"], actor) if old is None else {
                        **value["definition"], **{key: old[key] for key in fc.SERVER_FIELDS if key in old},
                        fc.VERSION_FIELD[kind]: old[fc.VERSION_FIELD[kind]] + 1, "updatedAt": now()}
                    item = fc.validate_object(kind, item)
                    stored = item
                    if kind == "job":
                        stored = self._get(db, kind, object_id) if old else {"jobId": item["id"]}
                        stored.update(canonical=item, config=copy.deepcopy(value["definition"]), configRevision=item["configRevision"])
                        aggregate(stored, self.root)["profileId"] = self.profile_id
                    self._save(db, kind, item["id"], stored)
                if kind == "trigger":
                    db.execute("DELETE FROM creator_links WHERE triggerId=?", (item["id"],))
                    if item["creatorSelector"]["mode"] == "ids":
                        for cid in item["creatorSelector"]["ids"]:
                            self.creators.bind_link(cid, item["id"], item["targetJobId"])
                db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)", (kind, item["id"], fc.canonical_json(actor)))
                return {"object": item}, [object_ref(kind, item)], False
            return self._operation(db, actor, "create" if object_id is None else "update", value["requestKey"],
                                   {"kind": kind, "objectId": object_id, **value}, kind, mutate)

    def control(self, kind, object_id, body, *, actor, capability=None):
        self._permit(actor, kind, capability or ("updateConfig" if kind == "template" else "lifecycle"))
        op = body["operation"]
        with self.transaction() as db:
            def mutate(operation_id):
                item = self._object(db, kind, object_id)
                fc.check_revision(item[fc.VERSION_FIELD[kind]], body["expectedRevision"])
                fc.check_revision(item["stateRevision"], body["expectedStateRevision"])
                transition = fc.check_definition_operation(kind, item["state"], op)
                parameters = dict(body["parameters"])
                if kind == "event_creator" and "expectedReferences" in parameters:
                    self._check_creator_references(db, object_id, parameters.pop("expectedReferences"))
                    if op == "retire" and (item["state"] == "enabled" or self._creator_references(db, object_id)["references"]):
                        error("reference_conflict", "Disable source and detach subscribers before retirement")
                if parameters:
                    error("invalid_schema", "Operation has no supported parameters")
                if op == "transfer-owner":
                    error("unsupported_operation", "Owner transfer needs its explicit composition contract")
                if transition["state"] == "enabled":
                    if kind == "event_creator":
                        source = self.creators.get(object_id)
                        adoption = source.get("pendingAdoption")
                        if adoption and not adoption["publicationSettled"]:
                            error("pending_publication", "Explicitly settle source publication before activation")
                        from packages.scheduler.source_migration import validate_successors
                        validate_successors(self, source)
                    self._references(db, kind, definition(kind, item), object_id=object_id, enabled=True)
                    db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)", (kind, object_id, fc.canonical_json(actor)))
                if kind == "event_creator":
                    self.creators.set_state(object_id, transition["state"], expected_state_revision=body["expectedStateRevision"])
                    item = self._object(db, kind, object_id)
                else:
                    item.update(state=transition["state"], stateRevision=item["stateRevision"] + transition["stateRevisionDelta"],
                                updatedAt=now())
                    item[fc.VERSION_FIELD[kind]] += transition["versionDelta"]
                    item = fc.validate_object(kind, item)
                    if kind == "job":
                        stored = self._get(db, kind, object_id)
                        stored.update(canonical=item, configRevision=item["configRevision"])
                    else:
                        stored = item
                    self._save(db, kind, object_id, stored)
                return {"object": item}, [object_ref(kind, item)], False
            return self._operation(db, actor, op, body["requestKey"], {"kind": kind, "id": object_id, **body}, kind, mutate)

    def retire(self, kind, object_id, body, *, actor):
        return self.control(kind, object_id, body, actor=actor, capability="delete")

    def _admit(self, db, job, value, actor, operation_id, *, cause=None):
        item = job["canonical"]
        fc.check_revision(item["configRevision"], value["expectedRevision"])
        if item["state"] != "enabled":
            error("reference_conflict", "Job is not enabled")
        self._permit(actor, "job", "execute", execution=True)
        cfg = definition("job", item)
        actions = self._references(db, "job", cfg, object_id=item["id"], enabled=True)
        input_value = copy.deepcopy(value["input"])
        if not jsonschema.Draft202012Validator(cfg["inputSchema"]).is_valid(input_value):
            error("invalid_schema", "Input does not match Job schema")
        state = aggregate(job, self.root)
        active = [run for run in state["runs"].values() if run["state"] in ACTIVE]
        overlap = cfg["overlap"]
        if overlap["mode"] == "forbid" and active:
            admission = self.final.ledger.admit(job, self.root, plan={}, cause=cause or {"type": "manual"},
                authority=actor, state="skipped", reason="overlap", operation_id=operation_id)
            self._save(db, "job", item["id"], job)
            return {"admissionId": admission["admissionId"], "configRevision": item["configRevision"], "skipped": True}
        if len(active) >= overlap["maxActive"] + overlap["maxPending"]:
            error("capacity_exceeded", "Job pending capacity reached")
        if cfg.get("runLimit") and len(state["runs"]) >= cfg["runLimit"]["maxRuns"]:
            error("capacity_exceeded", "Job Run limit reached")
        rerun = value.get("rerunOf")
        if rerun:
            self.final.ledger.run(job, self.root, rerun)
        frozen = {"steps": copy.deepcopy(cfg["steps"]), "actions": {a["id"]: a for a in actions if a.get("definition")},
                  "input": input_value, "event": copy.deepcopy(value.get("event", {})),
                  "authority": copy.deepcopy(actor), "configRevision": item["configRevision"]}
        for step in cfg["steps"]:
            if any(binding["from"] == "step" for binding in step["arguments"].values()):
                continue  # Dynamic output mapping is checked at its Step boundary.
            try:
                arguments = resolve_bindings(step["arguments"], input_value, frozen["event"], {})
            except ValueError:
                error("invalid_schema", "Step input mapping cannot be resolved")
            action = frozen["actions"][step["actionRef"]["id"]]
            if not jsonschema.Draft202012Validator(action["inputSchema"]).is_valid(arguments):
                error("invalid_schema", "Step input does not match Action schema")
        admission = self.final.ledger.admit(job, self.root, plan=frozen, cause=cause or {"type": "manual"},
                                          authority=actor, operation_id=operation_id, rerun_of=rerun,
                                          authorization_revision=max(1, self.auth.permissions_status()["revision"]))
        run = next(r for r in state["runs"].values() if r["admissionId"] == admission["admissionId"])
        self._save(db, "job", item["id"], job)
        return {"runId": run["runId"], "admissionId": admission["admissionId"], "configRevision": run["configRevision"]}

    def create_run(self, job_id, body, *, actor):
        self._permit(actor, "job", "execute")
        if set(body) - {"expectedRevision", "requestKey", "input", "cause", "rerunOf"} or not {"expectedRevision", "requestKey", "input", "cause"} <= set(body) or body["cause"] != {"type": "manual"}:
            error("invalid_schema", "Invalid manual Run request")
        with self.transaction() as db:
            def mutate(operation_id):
                job = self._get(db, "job", job_id)
                result = self._admit(db, job, body, actor, operation_id)
                return result, [object_ref("job", job["canonical"])], not result.get("skipped", False)
            return self._operation(db, actor, "create", body["requestKey"], {"jobId": job_id, **body}, "job", mutate)

    def _find_run(self, db, run_id):
        for job in self._all(db, "job"):
            if run_id in aggregate(job, self.root)["runs"]:
                return job, job["finalRuntime"]["runs"][run_id]
        raise KeyError(run_id)

    def get_run(self, run_id, *, actor):
        self._permit(actor, "job", "read")
        with self.transaction(readonly=True) as db:
            return copy.deepcopy(self._find_run(db, run_id)[1])

    def runs(self, job_id, *, actor, cursor=None, limit=100):
        self._permit(actor, "job", "read")
        return self._page(self.final.runs(job_id), "runs:" + job_id, cursor, limit, actor)

    def control_run(self, run_id, body, *, actor):
        self._permit(actor, "job", "execute")
        operation = body["operation"]
        if operation == "clone-and-run":
            self._permit(actor, "job", "create")
        with self.transaction() as db:
            def mutate(operation_id):
                job, run = self._find_run(db, run_id)
                fc.check_revision(run["stateRevision"], body["expectedStateRevision"])
                state = aggregate(job, self.root)
                result = {"runId": run_id}
                pending = False
                if operation in {"run-again", "clone-and-run"}:
                    result = {}
                    fc.check_revision(job["configRevision"], body["expectedRevision"])
                    params = body.get("parameters", {})
                    if set(params) - {"input", "overrides"} or (operation == "run-again" and "overrides" in params):
                        error("invalid_schema", "Invalid execution parameters")
                    input_value = params.get("input", definition("job", job["canonical"])["arguments"])
                    if operation == "clone-and-run":
                        overrides = params.get("overrides", {})
                        if not isinstance(overrides, dict) or set(overrides) - {"name", "description"}:
                            error("invalid_schema", "Clone overrides only accept metadata")
                        graph = {"job": {**definition("job", job["canonical"]), **overrides, "triggerLinks": []}, "creators": [], "triggers": []}
                        composed = self.compositions.compose(request_key=operation_id, desired_graph=graph,
                            authorization_evidence={"principal": principal(actor)}, activate=True, operation="clone-and-run")
                        job = self._get(db, "job", composed["objectIds"]["job"])
                        result["jobId"] = job["jobId"]
                    request = {"expectedRevision": job["configRevision"], "input": input_value}
                    if operation == "run-again":
                        request["rerunOf"] = run_id
                    result.update(self._admit(db, job, request, actor, operation_id))
                    pending = not result.get("skipped", False)
                elif operation == "retry":
                    if run["state"] == "uncertain":
                        error("reconciliation_required", "Unknown effect must be reconciled")
                    if run["state"] != "failed":
                        error("not_retryable", "Run is not failed")
                    failed = [step for step in state["steps"][run_id].values() if step["state"] == "failed"]
                    if body.get("stepId") and (not failed or body["stepId"] != failed[0]["stepId"]):
                        error("not_retryable", "Retry must resume the failed boundary")
                    if not failed or run_id not in state["plans"]:
                        error("not_retryable", "Frozen snapshot unavailable")
                    if any(e["outcome"] in {"unknown", "partial"} for e in state["effects"] if e["effectKey"]["runId"] == run_id):
                        error("reconciliation_required", "Unknown Effect cannot be retried")
                    frozen = state["plans"][run_id]["frozen"]
                    failed_id = failed[0]["stepId"]
                    step = next(s for s in frozen["steps"] if s["stepId"] == failed_id)
                    policy = frozen["actions"][step["actionRef"]["id"]]["retry"]
                    prior = [e for e in state["effects"] if e["effectKey"]["runId"] == run_id and e["effectKey"]["stepId"] == failed_id]
                    if any(e["outcome"] == "succeeded" for e in prior):
                        error("not_retryable", "Successful effect cannot be repeated")
                    if prior and (not (policy["idempotent"] or policy["sameKeySupported"]) or
                                  (policy["requiresProof"] and not prior[-1]["receipt"].get("cleanupProof", {}).get("confirmed"))):
                        error("not_retryable", "Effect lacks retry/cleanup proof")
                    self.final.ledger.transition_run(job, self.root, run_id, "retry_wait", operation="retry")
                    result["attemptId"] = None
                    pending = True
                elif operation == "cancel":
                    if run["state"] not in ACTIVE:
                        error("cancellation_window_closed", "Run already settled")
                    targets = self._external_targets(job, run_id)
                    if run["state"] == "uncertain" and not targets:
                        error("reconciliation_required", "Uncertain cancellation needs original resource identity")
                    if run["state"] == "uncertain":
                        run["stateRevision"] += 1
                        run["transitionSeq"] += 1
                    elif run["state"] != "cancelling":
                        self.final.ledger.transition_run(job, self.root, run_id, "cancelling", operation="cancel")
                    previous = state.setdefault("cancellations", {}).get(run_id)
                    # Another in-flight request cannot authorize a second
                    # destructive call. Only a fresh request after unknown can.
                    active = previous and previous["status"] in {"intent", "requested"}
                    if not active:
                        state["cancellations"][run_id] = {"operationId": operation_id,
                            "status": "intent", "targets": copy.deepcopy(targets),
                            "revision": (previous or {}).get("revision", 0) + 1}
                    result.update(cleanupStatus="pending", effectKeys=[copy.deepcopy(t["effectKey"]) for t in targets],
                                  attemptIds=[t["attempt"]["attemptId"] for t in targets])
                    pending = True
                elif operation == "pause":
                    if run["state"] not in {"queued", "retry_wait", "running"}:
                        error("unsupported_operation", "Run cannot pause")
                    state["plans"][run_id]["pauseRequested"] = True
                    run["stateRevision"] += 1
                    run["transitionSeq"] += 1
                    pending = True
                elif operation == "resume":
                    if not state["plans"][run_id].get("pauseRequested") and run["state"] != "paused_at_boundary":
                        error("unsupported_operation", "Run is not paused")
                    state["plans"][run_id]["pauseRequested"] = False
                    run["stateRevision"] += 1
                    run["transitionSeq"] += 1
                    pending = True
                else:
                    error("unsupported_operation", "Unknown Run operation")
                if operation not in {"run-again", "clone-and-run"}:
                    self._save(db, "job", job["jobId"], job)
                return result, [object_ref("job", job["canonical"])], pending
            return self._operation(db, actor, operation, body["requestKey"], {"runId": run_id, **body}, "job", mutate)

    def _external_adapter(self, job_id):
        adapter = getattr(self.executor, "independent_executor", None) or self.executor
        if self.executor is self._product_executor:
            from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, IndependentExecutor
            return IndependentExecutor(job_id=job_id, client=ExecutionHost(ExecutionStore(self.root / "executions")))
        return adapter

    def _external_targets(self, job, run_id=None):
        state = aggregate(job, self.root)
        keys = {row["attemptId"]: row["effectKey"] for row in state["effects"] if row["outcome"] in {"unknown", "partial"}}
        keys.update({row["attemptId"]: row["effectKey"] for row in state["effectAdmissions"].values()})
        return [{"jobId": job["jobId"], "attempt": copy.deepcopy(state["attempts"][aid]), "effectKey": copy.deepcopy(key)}
                for aid, key in keys.items() if state["attempts"][aid]["state"] in {"running", "uncertain"}
                and (run_id is None or state["attempts"][aid]["runId"] == run_id)]

    async def control_run_postcommit(self, run_id, body, *, actor):
        self._permit(actor, "job", "execute")
        if body["operation"] == "cancel":
            # Observe a previous asynchronous unknown before admitting an
            # explicit new recheck. This query cannot perform cleanup itself.
            await self.recover_external_receipts()
        response = self.control_run(run_id, body, actor=actor)
        if body["operation"] != "cancel" or response.get("replayed"):
            return response
        with self.transaction() as db:
            job, _ = self._find_run(db, run_id)
            intent = aggregate(job, self.root)["cancellations"][run_id]
            if intent["operationId"] != response["operationId"] or intent["status"] != "intent":
                return response
            intent["status"] = "requested"  # persist BEFORE external await; never replay after crash
            job_id, targets = job["jobId"], copy.deepcopy(intent["targets"])
            self._save(db, "job", job_id, job)
        adapter = self._external_adapter(job_id)
        failure = None
        for target in targets:
            try:
                cleanup = getattr(adapter, "cleanup_execution", None)
                if cleanup is None:
                    raise ValueError("original executor does not support owned cleanup")
                await cleanup(attempt=target["attempt"], effect_key=target["effectKey"])
            except Exception:
                failure = "cleanup_request_unknown"
        if failure:
            with self.transaction() as db:
                job = self._get(db, "job", job_id)
                current = aggregate(job, self.root)["cancellations"][run_id]
                if current["operationId"] == response["operationId"]:
                    current.update(status="unknown", error=failure)
                    self._cancel_status(db, job, run_id, "unknown")
                    self._save(db, "job", job_id, job)
        await self.recover_external_receipts()
        # Request acceptance is stable even if the terminal result arrived
        # during the await. Query the original operation for its latest state.
        return response

    def _cancel_status(self, db, job, run_id, status):
        intent = aggregate(job, self.root)["cancellations"][run_id]
        for row in db.execute("SELECT id,document FROM final_operations").fetchall():
            op = json.loads(row[1])
            if op["operation"] != "cancel" or op["response"].get("runId") != run_id:
                continue
            if op["response"]["status"] not in {"accepted", "pending", "unknown"}:
                continue
            op["response"]["status"] = status
            op["response"]["result"].update(cleanupStatus=intent["status"], runState=aggregate(job, self.root)["runs"][run_id]["state"])
            op["response"]["result"]["cleanupRevision"] = intent["revision"]
            if status == "unknown":
                op["response"]["error"] = {"code": "reconciliation_required", "message": "Inspect original execution; explicit cancellation recheck required"}
            else:
                op["response"].pop("error", None)
            db.execute("UPDATE final_operations SET document=? WHERE id=?", (fc.canonical_json(op), row[0]))

    def _cancel_boundary(self, db, job, run_id):
        state = aggregate(job, self.root)
        intent = state.get("cancellations", {}).get(run_id)
        if self._external_targets(job, run_id) or (intent and intent["targets"] and intent["status"] != "confirmed"):
            return  # Neither a state label nor a lost host is resource proof.
        self.final.ledger.transition_run(job, self.root, run_id, "cancelled", operation="cancel")
        if intent:
            intent["status"] = "confirmed"
            self._cancel_status(db, job, run_id, "completed")
        self._settle(db, job, run_id)

    def operation(self, operation_id, *, actor):
        with self.transaction(readonly=True) as db:
            row = db.execute("SELECT document FROM final_operations WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                raise KeyError(operation_id)
            record = json.loads(row[0])
            if record["principal"] != principal(actor):
                raise KeyError(operation_id)
            self._permit(actor, record["kind"], "read")
            return copy.deepcopy(record["response"])

    def operation_for_key(self, operation, request_key, *, actor):
        with self.transaction(readonly=True) as db:
            row = db.execute("SELECT id FROM final_operations WHERE scope=? AND requestKey=?", (self._scope(actor, operation), request_key)).fetchone()
            if row is None:
                raise KeyError(request_key)
            return self.operation(row[0], actor=actor)

    # WP04 CompositionPort. All callbacks share the outer Creator transaction.
    def create_disabled(self, operation):
        with self.transaction() as db:
            ids, graph = operation["objectIds"], operation["desiredGraph"]
            who = operation["authorizationEvidence"]["principal"]
            item = self._envelope("job", fc.validate_definition("job", graph["job"]), who,
                                  object_id=ids["job"], state="disabled")
            self._save(db, "job", item["id"], {"jobId": item["id"], "canonical": item,
                "config": definition("job", item), "configRevision": 1})
            for spec in graph["triggers"]:
                trigger = self._envelope("trigger", fc.validate_definition("trigger", spec["definition"]), who,
                                         object_id=ids["triggers"][spec["key"]], state="disabled")
                self._save(db, "trigger", trigger["id"], trigger)

    def verify_links(self, operation):
        with self.transaction() as db:
            job = self._object(db, "job", operation["objectIds"]["job"])
            self._references(db, "job", definition("job", job), object_id=job["id"])
            for spec in operation["desiredGraph"]["triggers"]:
                self._references(db, "trigger", spec["definition"])

    def commit_activation(self, operation, token):
        with self.transaction() as db:
            job = self._get(db, "job", operation["objectIds"]["job"])
            job["activationToken"] = token
            self._save(db, "job", job["jobId"], job)

    def activate(self, operation, token, enabled):
        with self.transaction() as db:
            job = self._get(db, "job", operation["objectIds"]["job"])
            if job.get("activationToken") != token:
                error("reference_conflict", "Activation token missing")
            job["canonical"]["state"] = "enabled" if enabled else "disabled"
            self._save(db, "job", job["jobId"], job)
            for tid in operation["objectIds"]["triggers"].values():
                trigger = self._get(db, "trigger", tid)
                trigger["state"] = "enabled" if enabled else "disabled"
                self._save(db, "trigger", tid, trigger)

    def complete(self, operation):
        pass  # WP04 persists the terminal composition receipt in this transaction.

    def cancel(self, operation):
        self.activate(operation, operation.get("activationToken"), False)

    def publish_action(self, action_id, value):
        """Trusted product/provider publication; deliberately not a public route."""
        value = fc.validate_definition("action", value)
        with self.transaction() as db:
            try:
                old = self._get(db, "action", action_id)
            except KeyError:
                old = None
            if old is not None:
                if definition("action", old) != value:
                    error("reference_conflict", "Published Action content is immutable")
                return old
            item = self._envelope("action", value, {"type": "user", "id": "local"}, object_id=action_id, state="published")
            item["enabled"] = True
            self._save(db, "action", action_id, fc.validate_object("action", item))
            return item

    def _install_product_actions(self):
        products = [("process", "write_local", "process-exit"),
                                     ("session-message", "write_local", "durable-queue-accepted"),
                                     ("session-broadcast", "write_local", "durable-queue-accepted")]
        if getattr(self.executor, "channel_provider", None) is not None:
            products.append(("channel", "write_external", "provider-receipt"))
        string = {"type": "string", "minLength": 1}
        schemas = {
            "process": {"argv": {"type": "array", "minItems": 1, "items": string}, "cwd": string, "hideWindow": {"type": "boolean"}},
            "session-message": {"sessionId": string, "text": string},
            "session-broadcast": {"sessionIds": {"type": "array", "minItems": 1, "uniqueItems": True, "items": string}, "text": string},
            "channel": {"targetType": {"enum": ["user", "group"]}, "targetId": {"type": "string", "pattern": "^[0-9]+$"},
                        "botUin": {"type": "string", "pattern": "^[0-9]+$"}, "text": string},
        }
        for op, effect, contract in products:
            required = [key for key in schemas[op] if key not in {"hideWindow", "botUin"}]
            self.publish_action("product_" + op.replace("-", "_"), {
                "name": op, "inputSchema": {"type": "object", "properties": schemas[op], "required": required, "additionalProperties": False}, "outputSchema": {"type": "object"},
                "providerRef": {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.v2"), "generation": 1},
                "execution": {"operation": op, "config": {}}, "effectClass": effect,
                "requiredCapabilities": [], "completionContracts": [{"id": contract, "schema": {"type": "object"}, "terminalProofSchema": {"type": "object"}}],
                "defaultCompletion": contract, "cancel": {"supported": False}, "reconcile": {"supported": False},
                "retry": {"idempotent": False, "sameKeySupported": True, "requiresProof": True}, "system": True})
            if op == "process":
                # A new immutable release, never overwrite product_process or
                # alter Actions embedded in an already-frozen Run.
                with self.transaction(readonly=True) as db:
                    value = definition("action", self._get(db, "action", "product_process"))
                value.update(cancel={"supported": True}, reconcile={"supported": True},
                    providerRef={"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.process-cleanup.v1"), "generation": 2})
                self.publish_action("product_process_cleanup_v1", value)

    def actions(self, *, actor):
        self._permit(actor, "action", "read")
        with self.transaction(readonly=True) as db:
            items = self._all(db, "action")
            return {"items": items, "nextCursor": None,
                    "references": {item["id"]: object_ref("action", item) for item in items}}

    def _guard_effect(self, db, job, run_id, frozen, action):
        self._permit(frozen["authority"], "job", "execute", execution=True)
        if action.get("system"):
            self._permit(frozen["authority"], "action", "manageSystemActions", execution=True)
        # Definition retirement/disable blocks new admission. Already frozen
        # Runs remain valid; pause is checked at each boundary by the driver.
        current = self._get(db, "action", action["id"])
        if not current["enabled"] or current["state"] != "published":
            error("authorization_denied", "Action unavailable before effect")
        if action.get("effectClass") == "high_risk":
            # The product-owned lifecycle Action is the sole high-risk
            # definition that may pass this boundary.  Its executor still
            # requires the protected supervisor and a one-step grant; user or
            # plugin supplied high-risk Actions remain rejected.
            fixed_lifecycle = (
                action.get("system") is True
                and action.get("id") == "system_main_lifecycle"
                and (action.get("execution") or {}).get("operation") == "main-lifecycle"
            )
            if not fixed_lifecycle:
                error("authorization_denied", "Arbitrary high-risk effects are forbidden")

    async def dispatch_once(self):
        """Drive WP02 records through WP05, one serial boundary per Run."""
        await self.recover_external_receipts()
        async with self._dispatch_lock:
            with self.transaction() as db:
                candidates = []
                for job in self._all(db, "job"):
                    runs = list(aggregate(job, self.root)["runs"].values())
                    # Unknown effects still occupy concurrency capacity.
                    # Queued cancellation, however, needs no effect slot.
                    candidates.extend((job["jobId"], run["runId"]) for run in runs if run["state"] == "cancelling")
                    occupied = sum(run["state"] in {"running", "uncertain", "settling"} for run in runs)
                    ready = [r for r in runs if r["state"] in {"queued", "retry_wait", "paused_at_boundary"}]
                    # Canonical JSON sorts map keys, so dictionary iteration
                    # after reload is not admission order.
                    ready.sort(key=lambda run: (run["createdAt"], run["runId"]))
                    slots = max(0, job["config"]["overlap"]["maxActive"] - occupied)
                    candidates.extend((job["jobId"], run["runId"]) for run in ready[:slots])
            await asyncio.gather(*(self._drive_run(job_id, run_id) for job_id, run_id in candidates))

    async def _drive_run(self, job_id, run_id):
        with self.transaction() as db:
            job = self._get(db, "job", job_id)
            state = aggregate(job, self.root)
            run = state["runs"][run_id]
            plan = state["plans"][run_id]
            if run["state"] == "cancelling":
                self._cancel_boundary(db, job, run_id)
                return
            if plan.get("pauseRequested"):
                if run["state"] != "paused_at_boundary":
                    self.final.ledger.start(job, self.root, run_id)
                    self.final.ledger.transition_run(job, self.root, run_id, "paused_at_boundary", operation="pause")
                self._save(db, "job", job_id, job)
                self._complete_boundary_controls(db, run_id, {"pause"})
                return
            if job["canonical"]["state"] == "paused":
                return
            self.final.ledger.start(job, self.root, run_id)
            self._save(db, "job", job_id, job)
            self._complete_boundary_controls(db, run_id, {"resume"})
        while True:
            with self.transaction() as db:
                job = self._get(db, "job", job_id)
                state = aggregate(job, self.root)
                plan = state["plans"][run_id]
                run = state["runs"][run_id]
                if run["state"] == "cancelling":
                    self._cancel_boundary(db, job, run_id)
                    return
                if plan.get("pauseRequested") or job["canonical"]["state"] == "paused":
                    self.final.ledger.transition_run(job, self.root, run_id, "paused_at_boundary", operation="pause")
                    self._save(db, "job", job_id, job)
                    self._complete_boundary_controls(db, run_id, {"pause"})
                    return
                frozen = plan["frozen"]
                step = next((s for s in frozen["steps"] if state["steps"][run_id][s["stepId"]]["state"] != "completed"), None)
                if step is None:
                    self.final.ledger.transition_run(job, self.root, run_id, "completed")
                    self._settle(db, job, run_id)
                    return
                action = frozen["actions"][step["actionRef"]["id"]]
                try:
                    self._guard_effect(db, job, run_id, frozen, action)
                    args = resolve_bindings(step["arguments"], frozen["input"], frozen["event"], plan.get("outputs", {}))
                    if not jsonschema.Draft202012Validator(action["inputSchema"]).is_valid(args):
                        error("invalid_schema", "Action input schema rejected mapping")
                except (fc.ContractError, ValueError) as exc:
                    self.final.ledger.fail_step_without_attempt(job, self.root, run_id, step["stepId"])
                    self.final.ledger.transition_run(job, self.root, run_id, "failed")
                    self._settle(db, job, run_id, failure=getattr(exc, "code", "invalid_schema"))
                    return
                attempt = self.final.ledger.begin_attempt(
                    job, self.root, run_id, step["stepId"],
                    owner_epoch=self.owner.token[0] if self.owner.token else 1,
                )
                for row in db.execute("SELECT id,document FROM final_operations").fetchall():
                    op = json.loads(row[1])
                    if op["operation"] == "retry" and op["response"].get("runId") == run_id and op["response"]["status"] == "accepted":
                        op["response"]["result"]["attemptId"] = attempt["attemptId"]
                        db.execute("UPDATE final_operations SET document=? WHERE id=?", (fc.canonical_json(op), row[0]))
                effect_key = self.final.ledger.admit_effect(job, self.root, attempt["attemptId"], action["id"])
                executable = {**copy.deepcopy(action["execution"]["config"]),
                              "operation": action["execution"]["operation"], "effect": action["effectClass"], "arguments": args}
                if executable["operation"] == "channel":
                    executable["action"] = {"api": "send_qq", "args": {key: args[key] for key in ("targetType", "targetId", "botUin") if key in args}}
                self._save(db, "job", job_id, job)
            try:
                executor = self._external_adapter(job_id) if executable["operation"] == "process" and self.executor is self._product_executor else self.executor
                receipt = await executor.execute(attempt=attempt, effect_key=effect_key, frozen_plan=executable,
                                                      retry=attempt["generation"] > 1)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                receipt = {"outcome": "unknown", "receipt": {"code": "executor_interrupted"}}
            outcome = receipt.get("outcome", "unknown")
            mapped = {"succeeded": "completed", "rejected": "failed", "unknown": "uncertain", "partial": "uncertain"}.get(outcome, "uncertain")
            with self.transaction() as db:
                job = self._get(db, "job", job_id)
                state = aggregate(job, self.root)
                # A fenced lookup may have already committed this very receipt
                # while the original callback was delayed in transport.
                if state["attempts"][attempt["attemptId"]]["state"] != "running":
                    return
                self.final.ledger.finish_attempt(job, self.root, attempt["attemptId"], mapped,
                    expected_epoch=attempt["ownerEpoch"], expected_generation=attempt["generation"])
                self.final.ledger.record_effect(job, self.root, attempt["attemptId"], "partial" if outcome == "partial" else mapped, receipt)
                state = aggregate(job, self.root)
                if run_id in state.get("cancellations", {}):
                    self._save(db, "job", job_id, job)
                    return  # Only the explicit cleanup proof path settles cancel.
                if mapped == "completed":
                    state["plans"][run_id].setdefault("outputs", {})[step["stepId"]] = copy.deepcopy(receipt.get("receipt", {}))
                    self._save(db, "job", job_id, job)
                else:
                    if state["runs"][run_id]["state"] == "cancelling" and mapped == "failed":
                        self.final.ledger.transition_run(job, self.root, run_id, "settling")
                    self.final.ledger.transition_run(job, self.root, run_id, mapped)
                    self._settle(db, job, run_id)
                    return

    @staticmethod
    def _complete_boundary_controls(db, run_id, operations):
        for row in db.execute("SELECT id,document FROM final_operations").fetchall():
            op = json.loads(row[1])
            if op["operation"] in operations and op["response"].get("runId") == run_id and op["response"]["status"] == "accepted":
                op["response"]["status"] = "completed"
                db.execute("UPDATE final_operations SET document=? WHERE id=?", (fc.canonical_json(op), row[0]))

    def _settle(self, db, job, run_id, *, failure=None):
        run = aggregate(job, self.root)["runs"][run_id]
        self.final.ledger.ensure_outbox(job, self.root, run_id, "canonical:" + run_id,
                                      {"runId": run_id, "status": run["state"]})
        self._save(db, "job", job["jobId"], job)
        for row in db.execute("SELECT id,document FROM final_operations").fetchall():
            op = json.loads(row[1])
            if op["operation"] == "cancel" and aggregate(job, self.root).get("cancellations", {}).get(run_id, {}).get("status") != "confirmed":
                continue
            if (op["response"].get("runId") == run_id and
                    (op["response"]["status"] in {"accepted", "pending"} or
                     (op["response"]["status"] == "unknown" and run["state"] in {"completed", "failed", "cancelled"}))):
                op["response"]["status"] = "unknown" if run["state"] == "uncertain" else "completed"
                op["response"]["result"]["runState"] = run["state"]
                if run["state"] in {"failed", "uncertain"}:
                    code = failure or ("reconciliation_required" if run["state"] == "uncertain" else "provider_unavailable")
                    op["response"]["error"] = {"code": code, "message": "Run requires inspection of its original receipt"}
                    if run["state"] == "failed":
                        op["response"]["status"] = "rejected"
                else:
                    op["response"].pop("error", None)
                db.execute("UPDATE final_operations SET document=? WHERE id=?", (fc.canonical_json(op), row[0]))

    def recover(self):
        """Never replay an effect whose host/receipt outcome was lost."""
        with self.transaction() as db:
            if self.owner.token is not None:
                for creator in self.creators.list():
                    creator["ownerEpoch"] = max(creator["ownerEpoch"] + 1, self.owner.token[0])
                    creator["lease"] = None
                    self.creators._write(db, creator)
            for job in self._all(db, "job"):
                state = aggregate(job, self.root)
                for rid, intent in state.get("cancellations", {}).items():
                    if intent["status"] in {"intent", "requested"}:
                        intent["status"] = "unknown"
                        self._cancel_status(db, job, rid, "unknown")
                for row in state["outbox"].values():
                    if row["status"] == "sending":
                        row.update(status="uncertain", receipt={"code": "notification_host_restarted"})
                self._save(db, "job", job["jobId"], job)
                for run in state["runs"].values():
                    active_attempts = [attempt for attempt in state["attempts"].values()
                                       if attempt["runId"] == run["runId"] and attempt["state"] == "running"]
                    if run["state"] != "running" and not active_attempts:
                        continue
                    # A cancelling Run can still own an in-flight effect. A
                    # lost host is not proof that cancellation stopped it.
                    for attempt in active_attempts:
                        self.final.ledger.finish_attempt(job, self.root, attempt["attemptId"], "uncertain")
                        self.final.ledger.record_effect(job, self.root, attempt["attemptId"], "uncertain", {"code": "host_restarted"})
                    self.final.ledger.transition_run(job, self.root, run["runId"], "uncertain")
                    self._settle(db, job, run["runId"])

    @staticmethod
    def _cleanup_complete(doc):
        receipt = doc.get("result") or {}
        proof = receipt.get("cleanupProof", {})
        if receipt.get("outcome") not in {"succeeded", "rejected"} or proof.get("confirmed") is not True:
            return False
        if proof.get("effectStarted") is False:
            return doc.get("phase") == "cancelled" and (not doc.get("supervisorIdentity")
                or doc.get("supervisorExitProof", {}).get("confirmed") is True)
        tree = proof.get("ownedTree", {})
        return (tree.get("confirmed") is True and tree.get("remaining") == []
                and proof.get("identityVerified") is True and proof.get("treeTerminated") is True
                and doc.get("supervisorExitProof", {}).get("confirmed") is True)

    def _apply_original_receipt(self, job, target, receipt):
        from packages.jobs.independent_executor import fence
        state = aggregate(job, self.root)
        expected, key = target["attempt"], target["effectKey"]
        attempt = state["attempts"][expected["attemptId"]]
        if (target["jobId"] != job["jobId"] or fence(attempt) != fence(expected)
                or receipt.get("effectKey") != key):
            error("revision_conflict", "Original external receipt fence rejected")
        outcome = "completed" if receipt["outcome"] == "succeeded" else "failed"
        if attempt["state"] == "running":
            admission = next((r for r in state["effectAdmissions"].values() if r["attemptId"] == attempt["attemptId"]), None)
            if admission is None or admission["effectKey"] != key:
                error("revision_conflict", "Original admission missing")
            self.final.ledger.finish_attempt(job, self.root, attempt["attemptId"], outcome,
                expected_epoch=expected["ownerEpoch"], expected_generation=expected["generation"])
            self.final.ledger.record_effect(job, self.root, attempt["attemptId"], outcome, receipt)
        elif attempt["state"] == "uncertain":
            self.final.ledger.reconcile_attempt(job, self.root, expected_attempt=expected, effect_key=key, receipt=receipt)
        else:
            prior = [r for r in state["effects"] if r["attemptId"] == attempt["attemptId"]]
            if len(prior) != 1 or prior[0]["effectKey"] != key or prior[0]["receipt"] != receipt:
                error("revision_conflict", "Late receipt differs from committed original")
        if outcome == "completed":
            state["plans"][expected["runId"]].setdefault("outputs", {})[expected["stepId"]] = copy.deepcopy(receipt.get("receipt", {}))
        return outcome

    async def recover_external_receipts(self):
        # Query only. Never submit execution or repeat a cleanup request here.
        with self.transaction(readonly=True) as db:
            pending = []
            for job in self._all(db, "job"):
                state = aggregate(job, self.root)
                cancelled = state.get("cancellations", {})
                for rid, intent in cancelled.items():
                    if intent["status"] != "confirmed" and intent["targets"]:
                        pending.append((job["jobId"], rid, copy.deepcopy(intent), copy.deepcopy(intent["targets"])))
                for target in self._external_targets(job):
                    rid = target["attempt"]["runId"]
                    if rid not in cancelled:
                        pending.append((job["jobId"], rid, None, [target]))
        for job_id, rid, intent, targets in pending:
            adapter = self._external_adapter(job_id)
            documents = []
            try:
                for target in targets:
                    lookup_doc = getattr(adapter, "lookup_execution", None)
                    if lookup_doc is not None:
                        doc = await lookup_doc(attempt=target["attempt"], effect_key=target["effectKey"])
                    elif intent is None and callable(getattr(adapter, "lookup_receipt", None)):
                        doc = {"result": await adapter.lookup_receipt(attempt=target["attempt"], effect_key=target["effectKey"])}
                    else:
                        raise ValueError("original executor lookup unavailable")
                    documents.append(doc)
            except Exception:
                self.diagnostics.append({"at": now(), "code": "external_receipt_unavailable"})
                del self.diagnostics[:-100]
                if intent is not None:
                    with self.transaction() as db:
                        job = self._get(db, "job", job_id)
                        current = aggregate(job, self.root)["cancellations"][rid]
                        if current["operationId"] == intent["operationId"]:
                            current["status"] = "unknown"
                            self._cancel_status(db, job, rid, "unknown")
                            self._save(db, "job", job_id, job)
                continue
            with self.transaction() as db:
                job = self._get(db, "job", job_id)
                state = aggregate(job, self.root)
                if intent is not None:
                    current = state["cancellations"][rid]
                    if current["operationId"] != intent["operationId"] or current["status"] == "confirmed":
                        continue
                    current["observations"] = copy.deepcopy(documents)
                    if not all(self._cleanup_complete(doc) for doc in documents):
                        unknown = any(doc.get("cleanupRequest", {}).get("status") == "unknown"
                                      or (doc.get("result") or {}).get("outcome") in {"unknown", "partial"} for doc in documents)
                        if unknown:
                            current["status"] = "unknown"
                            self._cancel_status(db, job, rid, "unknown")
                        self._save(db, "job", job_id, job)
                        continue
                    for target, doc in zip(targets, documents):
                        self._apply_original_receipt(job, target, doc["result"])
                    current["status"] = "confirmed"
                    self.final.ledger.transition_run(job, self.root, rid, "cancelled",
                        operation="reconcile" if state["runs"][rid]["state"] == "uncertain" else "cancel")
                    self._cancel_status(db, job, rid, "completed")
                    self._settle(db, job, rid)
                else:
                    receipt = documents[0].get("result")
                    if not receipt or receipt.get("outcome") not in {"succeeded", "rejected"}:
                        continue
                    # A control may have arrived while lookup was in flight.
                    if rid in state.get("cancellations", {}):
                        continue
                    outcome = self._apply_original_receipt(job, targets[0], receipt)
                    target_state = ("completed" if all(s["state"] == "completed" for s in state["steps"][rid].values())
                                    else "retry_wait") if outcome == "completed" else "failed"
                    run = state["runs"][rid]
                    self.final.ledger.transition_run(job, self.root, rid, target_state,
                        operation="reconcile" if run["state"] == "uncertain" else None)
                    self._save(db, "job", job_id, job)
                    if target_state != "retry_wait":
                        self._settle(db, job, rid)

    def targets(self, event):
        with self.transaction(readonly=True) as db:
            return [{**item, "_final": True, "enabled": item["state"] == "enabled"}
                    for item in self._all(db, "trigger") if event["type"] in item["eventTypes"]]

    def admit_event(self, event, target, *, condition_result=None):
        """WP03 admission only: no Action or provider effect is invoked here."""
        result = self._admit_event(event, target, condition_result=condition_result)
        if self.automation_ledger is not None and self.owner.token is None and "operationId" in result:
            self.automation_ledger.record_final_admission(event.get("eventId", event.get("id")), target["id"], result)
        return result

    def _admit_event(self, event, target, *, condition_result=None):
        with self.transaction() as db:
            eid = event.get("eventId", event.get("id"))
            fact_digest = fc.digest(event)
            prior = db.execute("SELECT operationId,factDigest FROM final_event_operations WHERE eventId=? AND triggerId=?",
                               (eid, target["id"])).fetchone()
            if prior:
                if prior["factDigest"] != fact_digest:
                    error("idempotency_conflict", "Event fact changed")
                record = json.loads(db.execute("SELECT document FROM final_operations WHERE id=?", (prior["operationId"],)).fetchone()[0])
                result = {**record["response"], "replayed": True}
                return result
            trigger = self._get(db, "trigger", target["id"])
            if trigger["revision"] != target["revision"]:
                return {"status": "skipped"}
            key = "event:" + fc.digest({"eventId": eid, "triggerId": trigger["id"]})
            basis = db.execute("SELECT document FROM final_authorities WHERE kind='trigger' AND id=?", (trigger["id"],)).fetchone()
            if basis is None:
                error("authorization_denied", "Trigger authority missing")
            actor = json.loads(basis[0])
            selector = trigger["creatorSelector"]
            creator_id = event.get("creatorId")
            if selector["mode"] == "ids" and creator_id not in selector["ids"]:
                return {"status": "skipped"}
            if selector["mode"] == "registeredType":
                provider_type = event.get("creatorType", event.get("providerType"))
                if creator_id:
                    source = self.creators.get(creator_id)
                    provider_type = source["providerRef"]["type"] if source else None
                elif isinstance(event.get("source"), dict):
                    provider_type = event["source"].get("type")
                if provider_type != selector["providerType"]:
                    return {"status": "skipped"}
            if trigger["state"] != "enabled":
                return {"status": "skipped"}
            # Reuse the existing condition/filter evaluator, never an effect host.
            from packages.automation.engine import matches_event
            legacy_event = {**event, "type": event.get("eventType", event.get("type"))}
            if not matches_event(trigger.get("eventFilter", {}), legacy_event):
                return {"status": "skipped"}
            if trigger.get("condition") is not None and condition_result is None:
                error("provider_unavailable", "State condition requires the host State reader")
            if condition_result is False:
                return {"status": "skipped"}
            job = self._get(db, "job", trigger["targetJobId"])
            args = resolve_bindings(trigger.get("arguments", {}), {}, legacy_event, {}) if trigger.get("arguments") else definition("job", job["canonical"])["arguments"]
            def mutate(operation_id):
                result = self._admit(db, job, {"input": args, "event": legacy_event,
                    "expectedRevision": job["configRevision"]}, actor, operation_id,
                    cause={"type": "event", "eventId": eid, "triggerId": trigger["id"]})
                return result, [object_ref("job", job["canonical"])], not result.get("skipped", False)
            result = self._operation(db, actor, "create", key,
                {"eventId": eid, "triggerId": trigger["id"], "factDigest": fact_digest}, "job", mutate)
            db.execute("INSERT INTO final_event_operations VALUES(?,?,?,?)",
                       (eid, trigger["id"], result["operationId"], fact_digest))
        return result

    async def admit_event_async(self, event, target):
        from packages.automation.engine import evaluate
        if target.get("condition") is not None and self.state_reader is None:
            error("provider_unavailable", "State reader unavailable")
        result = await evaluate(target.get("condition"), self.state_reader, {})
        return await asyncio.to_thread(self.admit_event, event, target, condition_result=result)

    def refresh_subscriptions(self):
        with self.transaction() as db:
            self.bus.subscriptions = {item["id"]: {**item, "subscriptionId": item["id"],
                "creatorIds": item["creatorSelector"].get("ids", [])} for item in self._all(db, "trigger")}

    def scheduler_migration(self, legacy_events):
        """Explicit reviewed handoff port; never instantiated by startup scans."""
        from packages.scheduler.source_migration import SourceMigration
        return SourceMigration(self, legacy_events)

    async def tick(self):
        self.refresh_subscriptions()
        await self.creator_runtime.tick()
        for event in await asyncio.to_thread(self.events.page):
            snapshot = await asyncio.to_thread(self.events.get, event["eventId"])
            for target in snapshot["targets"]:
                with self.transaction() as db:
                    if db.execute("SELECT 1 FROM final_event_receipts WHERE eventId=? AND triggerId=?", (event["eventId"], target["id"])).fetchone():
                        continue
                try:
                    result = await self.admit_event_async(event, target)
                except (fc.ContractError, KeyError):
                    self.diagnostics.append({"at": now(), "code": "event_admission_blocked"})
                    continue
                with self.transaction() as db:
                    db.execute("INSERT OR IGNORE INTO final_event_receipts VALUES(?,?,?)", (event["eventId"], target["id"], result["status"]))
        await self.dispatch_once()
        await self.deliver_outbox()

    async def deliver_outbox(self):
        if self.outbox_sender is None:
            return
        async with self._outbox_lock:
            with self.transaction() as db:
                pending = [(job["jobId"], copy.deepcopy(row)) for job in self._all(db, "job")
                           for row in aggregate(job, self.root)["outbox"].values() if row["status"] == "pending"]
            for job_id, row in pending:
                with self.transaction() as db:
                    job = self._get(db, "job", job_id)
                    current = aggregate(job, self.root)["outbox"][row["outboxId"]]
                    if current["status"] != "pending":
                        continue
                    if row["channel"] != "ws":
                        frozen = aggregate(job, self.root)["plans"][row["runId"]]["frozen"]
                        try:
                            self._permit(frozen["authority"], "job", "execute", execution=True)
                        except fc.ContractError:
                            current["status"] = "suppressed"
                            self._save(db, "job", job_id, job)
                            continue
                    current["status"] = "sending"
                    self._save(db, "job", job_id, job)
                try:
                    receipt = await self.outbox_sender({**row, "jobId": job_id})
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    receipt = {"outcome": "unknown"}
                if not isinstance(receipt, dict):
                    receipt = {"outcome": "unknown", "code": "invalid_notification_receipt"}
                with self.transaction() as db:
                    job = self._get(db, "job", job_id)
                    current = aggregate(job, self.root)["outbox"][row["outboxId"]]
                    current.update(status="acked" if receipt.get("outcome") == "succeeded" else "uncertain",
                                   receipt=receipt)
                    self._save(db, "job", job_id, job)

    def _guard_creator(self, item):
        from packages.scheduler.source_migration import validate_successors
        validate_successors(self, item)
        with self.transaction(readonly=True) as db:
            row = db.execute("SELECT document FROM final_authorities WHERE kind='event_creator' AND id=?", (item["id"],)).fetchone()
            if row is None:
                error("authorization_denied", "Creator authority unavailable")
            self._permit(json.loads(row[0]), "event_creator", "lifecycle", execution=True)

    async def start(self, *, activation=None, expected_epoch=None):
        status = self.owner.status()
        epoch = (status["epoch"] if status else 0) if expected_epoch is None else expected_epoch
        if self._adopt_owner:
            if status is None or status["state"] != "activating" or self.owner.token is None:
                error("revision_conflict", "Startup owner handoff is not activating")
            with self.owner.writer(canonical=True):
                self._adopt_owner = False
        else:
            self.owner.claim(epoch)
        try:
            if any(Path(root).resolve().parent != self.owner.root for root in self.legacy_roots):
                error("reference_conflict", "Legacy store belongs to a different owner profile")
            if self.automation_ledger is not None and self.automation_ledger.path.resolve().parent.parent != self.owner.root:
                error("reference_conflict", "Automation ledger belongs to a different owner profile")
            self._initialize_schema()
            self._install_product_actions()
            if activation is not None:
                self.owner.activation_intent(fc.digest(activation))
                from packages.jobs.runtime_activation import activate_bundle
                activate_bundle(self, **activation)
            elif self.owner.activation_intent() and not self._has_activation():
                error("reference_conflict", "Incomplete activation requires its original reviewed bundle")
            await self.recover_external_receipts()
            await asyncio.to_thread(self.recover)
            self.owner.set_state("active")
        except BaseException:
            self.owner.release(failed=True)
            raise
        self.closed = False
        async def loop():
            while not self.closed:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.diagnostics.append({"at": now(), "code": "dispatch_failed"})
                    del self.diagnostics[:-100]
                await asyncio.sleep(0.25)
        self.task = asyncio.create_task(loop())

    def _has_activation(self):
        with self.transaction(readonly=True) as db:
            return bool(db.execute("SELECT 1 FROM final_documents WHERE kind='runtime_activation'").fetchone())

    async def close(self):
        self.closed = True
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.owner.release()


def bind(app, automation, root, *, owner=None):
    """Production and isolated HTTP tests use the same host binding."""
    service = FinalService(root, automation.auth, automation_ledger=automation.ledger, owner=owner)
    import os
    from packages.core import background_jobs
    from packages.core.platform.data_paths import job_root as data_root
    service.legacy_roots = [Path(os.environ.get("PAN_BACKGROUND_JOBS_DIR") or background_jobs.DEFAULT_ROOT), data_root()]
    service.state_reader = automation.engine.state_reader
    automation.events.owner_fence = service.owner
    automation.final_jobs = service
    app.state.job_final_service = service
    return service
