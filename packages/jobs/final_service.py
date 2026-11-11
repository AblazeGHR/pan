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
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from contextlib import contextmanager
from pathlib import Path

import jsonschema

from packages.jobs import final_contracts as fc
from packages.jobs.chains import resolve as resolve_bindings
from packages.jobs.final_runtime import FinalRuntime
from packages.jobs.final_runtime.executor import TypedExecutor, WorkerSessionProvider
from packages.actions.typed_executor import WorkerAssignProvider
from packages.jobs.final_runtime.ledger import aggregate, new_id, now, principal
from packages.scheduler.creators import CreatorStore, CanonicalEventStore, CreatorRuntime, EventBus
from packages.scheduler.composition import CompositionCoordinator
from packages.jobs.runtime_owner import OwnerFence


PREFIX = "/api"
GRANTS = {"job": "job", "trigger": "trigger", "event_creator": "eventCreator", "template": "jobTemplate",
          "action": "action"}
ACTIVE = {"queued", "running", "retry_wait", "paused_at_boundary", "cancelling", "settling", "uncertain"}


def error(code, message):
    raise fc.ContractError(code, message)


def frozen_action(frozen, step_id, action_id):
    """Read the exact Step version; old frozen plans keep their original map."""
    return frozen.get("stepActions", {}).get(step_id) or frozen["actions"][action_id]


def definition(kind, item):
    names = fc.schema_artifacts()[kind + "_definition.schema.json"]["properties"]
    return {key: copy.deepcopy(value) for key, value in item.items() if key in names}


def object_ref(kind, item):
    return {"id": item["id"], "revision": item[fc.VERSION_FIELD[kind]],
            "digest": fc.digest(definition(kind, item))}


def normalize_legacy_form_fields(fields, *, kind=None, canonical=None):
    """Map the old assign/shell form into canonical Action arguments.

    This is a pure form boundary adapter. It does not publish, persist, spawn,
    send, or select a provider. Callers still compose the returned arguments
    with the immutable product Action and pass them through normal admission.
    Unknown fields and disagreement with an explicit canonical value fail
    before persistence. ``command`` is converted to an explicit argv boundary
    using the platform shell; an argv supplied by the caller is preserved.
    """
    if not isinstance(fields, dict):
        raise fc.ContractError("invalid_schema", "Legacy form fields must be an object")
    if kind is None:
        kind = "shell" if any(key in fields for key in ("command", "argv", "cwd", "hideWindow")) else "assign"
    if kind not in {"assign", "shell"}:
        raise fc.ContractError("invalid_schema", "Only assign and shell legacy forms are supported")
    allowed = ({"sessionId", "targetSessionId", "target", "text", "message", "source",
                "sourceSessionId", "taskId"} if kind == "assign" else
               {"command", "argv", "cwd", "hideWindow"})
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise fc.ContractError("invalid_schema", "Unknown legacy form field(s): " + ", ".join(unknown))

    def agree(name, value):
        if canonical is not None and name in canonical and canonical[name] != value:
            raise fc.ContractError("reference_conflict", f"Legacy and canonical {name} disagree")

    if kind == "assign":
        target = fields.get("sessionId", fields.get("targetSessionId"))
        if "sessionId" in fields and "targetSessionId" in fields and fields["sessionId"] != fields["targetSessionId"]:
            raise fc.ContractError("reference_conflict", "sessionId and targetSessionId disagree")
        if "target" in fields:
            target_value = fields["target"]
            if isinstance(target_value, dict):
                if set(target_value) != {"sessionId"} or not isinstance(target_value["sessionId"], str):
                    raise fc.ContractError("invalid_schema", "target must contain only sessionId")
                target_value = target_value["sessionId"]
            if not isinstance(target_value, str):
                raise fc.ContractError("invalid_schema", "target must be a Session id")
            if target is not None and target != target_value:
                raise fc.ContractError("reference_conflict", "target and sessionId disagree")
            target = target_value
        text = fields.get("text", fields.get("message"))
        if "text" in fields and "message" in fields and fields["text"] != fields["message"]:
            raise fc.ContractError("reference_conflict", "text and message disagree")
        if not isinstance(target, str) or not target:
            raise fc.ContractError("invalid_schema", "assign form requires sessionId")
        if not isinstance(text, str) or not text:
            raise fc.ContractError("invalid_schema", "assign form requires text")
        result = {"sessionId": target, "text": text}
        for name in ("source", "sourceSessionId", "taskId"):
            if name in fields:
                if not isinstance(fields[name], str) or not fields[name]:
                    raise fc.ContractError("invalid_schema", f"{name} must be non-empty text")
                result[name] = fields[name]
        if canonical is not None:
            for name, value in result.items():
                agree(name, value)
            result = {**result, **canonical}
        return result

    if "command" in fields and "argv" in fields:
        raise fc.ContractError("reference_conflict", "command and argv cannot both be supplied")
    argv = fields.get("argv")
    if argv is None:
        command = fields.get("command")
        if not isinstance(command, str) or not command:
            raise fc.ContractError("invalid_schema", "shell form requires command or argv")
        from packages.core.platform.worker_runtime import shell_command_argv
        argv = shell_command_argv(command)
    if (not isinstance(argv, list) or not argv or
            any(not isinstance(item, str) or not item or "\x00" in item for item in argv)):
        raise fc.ContractError("invalid_schema", "argv must be a non-empty list of text")
    cwd = fields.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise fc.ContractError("invalid_schema", "shell form requires cwd")
    hide = fields.get("hideWindow", True)
    if type(hide) is not bool:
        raise fc.ContractError("invalid_schema", "hideWindow must be boolean")
    result = {"argv": list(argv), "cwd": cwd, "hideWindow": hide}
    if canonical is not None:
        for name, value in result.items():
            agree(name, value)
        result = {**result, **canonical}
    return result


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
        # The immutable inbox owns target IDs, not a permanently stale policy
        # revision. Reuse the coordinator's current-target read on retry.
        targets = await asyncio.to_thread(self.service.targets, event)
        current = next((item for item in targets if item['id'] == target['id']), None)
        if current is None:
            return {'status': 'skipped'}
        return await self.service.admit_event_async(event, current)


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
        self.executor = executor or TypedExecutor(session_provider=WorkerSessionProvider(), assign_provider=WorkerAssignProvider())
        self._product_executor = self.executor if executor is None else None
        self.quota_native = None
        self.task = None
        self.closed = False
        self.diagnostics = []
        self._dispatch_lock = asyncio.Lock()
        self.state_reader = None
        self.session_pause = None
        self.outbox_sender = None
        self.on_committed_change = None
        self._change_local = threading.local()
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
            self._ensure_action_schema(db)

    @staticmethod
    def _ensure_action_schema(db):
        """Create the Action version side tables in the existing document store.

        ``final_documents`` remains the logical Action head used by the Job
        service.  Definitions are copied into the version table once and are
        never rewritten; mutable lifecycle state lives in its own table.
        This keeps old frozen Job plans readable while allowing a head to carry
        an unrelated draft revision.
        """
        db.execute("""CREATE TABLE IF NOT EXISTS final_action_versions(
            actionId TEXT NOT NULL,
            revision INTEGER NOT NULL,
            digest TEXT NOT NULL,
            state TEXT NOT NULL,
            definition TEXT NOT NULL,
            createdAt TEXT NOT NULL,
            publishedAt TEXT,
            PRIMARY KEY(actionId, revision)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS final_action_lifecycle(
            actionId TEXT NOT NULL,
            revision INTEGER NOT NULL,
            enabled INTEGER NOT NULL,
            state TEXT NOT NULL,
            stateRevision INTEGER NOT NULL DEFAULT 0,
            updatedAt TEXT NOT NULL,
            PRIMARY KEY(actionId, revision)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS final_action_aliases(
            legacyId TEXT PRIMARY KEY,
            actionId TEXT NOT NULL,
            revision INTEGER NOT NULL,
            digest TEXT NOT NULL,
            source TEXT NOT NULL,
            owner TEXT NOT NULL,
            createdAt TEXT NOT NULL
        )""")

    @contextmanager
    def transaction(self, *, readonly=False):
        if not readonly and self.closed:
            error('provider_unavailable', 'Canonical service is closed')
        if readonly:
            with self.creators.connection(readonly=True) as db:
                yield db
        else:
            outer = getattr(self._change_local, "pending", None) is None
            if outer:
                self._change_local.pending = []
            changes = self._change_local.pending
            try:
                with self.owner.writer(canonical=True), self.creators.transaction() as db:
                    if self.closed:
                        error('provider_unavailable', 'Canonical service is closed')
                    yield db
                    if outer and changes:
                        committed = copy.deepcopy(changes)
                        self.creators.after_commit(lambda: self._publish_committed_changes(committed))
            finally:
                if outer:
                    del self._change_local.pending

    def _publish_committed_changes(self, changes):
        # These immutable observations cannot execute or settle anything.
        if self.on_committed_change is not None:
            for change in changes:
                try:
                    self.on_committed_change(copy.deepcopy(change))
                except Exception as exc:
                    self.diagnostics.append({"at": now(), "code": "change_publication_failed",
                                             "message": str(exc)})
                    del self.diagnostics[:-100]

    @staticmethod
    def _get(db, kind, object_id):
        row = db.execute("SELECT document FROM final_documents WHERE kind=? AND id=?", (kind, object_id)).fetchone()
        if row is None:
            raise KeyError(object_id)
        return json.loads(row[0])

    def _save(self, db, kind, object_id, value):
        pending = getattr(self._change_local, "pending", None)
        change = None
        if pending is not None and self.on_committed_change is not None:
            def projection(document):
                result = {"kind": kind, "object": self._public(kind, document)}
                if kind == "job":
                    result["runs"] = list(copy.deepcopy(document.get("finalRuntime", {}).get("runs", {})).values())
                return result
            change = projection(value)
            row = db.execute("SELECT document FROM final_documents WHERE kind=? AND id=?", (kind, object_id)).fetchone()
            if row is not None and projection(json.loads(row[0])) == change:
                change = None
        db.execute("INSERT OR REPLACE INTO final_documents VALUES(?,?,?)", (kind, object_id, fc.canonical_json(value)))
        if change is not None:
            pending.append(copy.deepcopy(change))

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

    #: User-visible objects keep their public schema.
    _OBJECT_KINDS = frozenset({"action", "job", "template", "trigger", "event_creator"})
    #: Compatibility documents stored beside objects. They are never projected
    #: to a public surface; anything outside both sets stays an error.
    _INTERNAL_KINDS = frozenset({
        "json_scheduler_handoff", "legacy_template_alias", "migration_receipt",
        "trigger_receipt_handoff", "runtime_activation", "runtime_archive", "retention_settings",
    })

    def _public(self, kind, item):
        if kind in self._INTERNAL_KINDS:
            return copy.deepcopy(item)
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

    @staticmethod
    def _action_definition_record(action_id, revision, digest, definition_value, *, state, enabled):
        """Return the resolver shape consumed by final_contracts and Runtime."""
        definition_value = copy.deepcopy(definition_value)
        return {
            **definition_value,
            "id": action_id,
            "revision": revision,
            "digest": digest,
            "state": state,
            "enabled": bool(enabled),
            "definition": definition_value,
        }

    def _action_version_record(self, db, action_id, revision, *, digest=None, for_ref=False):
        try:
            self._ensure_action_schema(db)
        except sqlite3.OperationalError:
            # A profile created before P1 may not have side tables yet.  Its
            # one logical head remains a readable compatibility version until
            # the next owner-fenced write creates the tables.
            item = self._get(db, "action", action_id)
            if item["revision"] != revision:
                raise KeyError(action_id)
            definition_value = definition("action", item)
            current_digest = fc.digest(definition_value)
            if digest is not None and digest != current_digest:
                error("reference_conflict", "Action digest does not match the fixed reference")
            return self._action_definition_record(action_id, revision, current_digest, definition_value,
                                                  state=item["state"], enabled=item.get("enabled", False))
        query = "SELECT revision,digest,state,definition FROM final_action_versions WHERE actionId=? AND revision=?"
        row = db.execute(query, (action_id, revision)).fetchone()
        if row is None:
            raise KeyError(action_id)
        if digest is not None and row[1] != digest:
            error("reference_conflict", "Action digest does not match the fixed reference")
        life = db.execute(
            "SELECT enabled,state FROM final_action_lifecycle WHERE actionId=? AND revision=?",
            (action_id, revision),
        ).fetchone()
        state = life[1] if life is not None else row[2]
        enabled = bool(life[0]) if life is not None else False
        if for_ref:
            try:
                head = self._get(db, "action", action_id)
            except KeyError:
                head = None
            if head is not None and head.get("state") == "retired":
                state = "retired"
                enabled = False
        definition_value = json.loads(row[3])
        return self._action_definition_record(action_id, int(row[0]), row[1], definition_value,
                                              state=state, enabled=enabled)

    def _action_head_record(self, db, action_id):
        item = self._get(db, "action", action_id)
        digest = fc.digest(definition("action", item))
        return self._action_definition_record(action_id, item["revision"], digest,
                                              definition("action", item), state=item["state"],
                                              enabled=item.get("enabled", False))

    def _resolver(self, db):
        def resolve(kind, object_id):
            try:
                if kind == "action":
                    return self._action_head_record(db, object_id)
                return self._object(db, kind, object_id)
            except KeyError:
                return None
        def resolve_ref(kind, ref):
            try:
                if kind == "action":
                    return self._action_version_record(db, ref["id"], ref["revision"], digest=ref.get("digest"), for_ref=True)
                return resolve(kind, ref["id"])
            except KeyError:
                return None
        # final_contracts uses this optional capability to resolve an older
        # published revision while the logical head is carrying a newer draft.
        resolve.resolve_ref = resolve_ref
        return resolve

    def _references(self, db, kind, value, *, object_id=None, enabled=False):
        checked = fc.validate_references(kind, value, self._resolver(db), object_id=object_id, require_enabled=enabled)
        completion_job = value if kind == "job" else value.get("jobDefinition", {}) if kind == "template" else {}
        from packages.jobs.independent_executor import validate_monitoring_completion
        for step in completion_job.get("steps", []):
            try:
                validate_monitoring_completion(step["completion"])
            except ValueError as exc:
                error("invalid_schema", str(exc))
        if kind == "job":
            recovery = value.get("recovery", {})
            if recovery.get("automatic"):
                if (not recovery.get("retryIf")
                        or any(a["execution"]["operation"] != "process" or a.get("effectClass") == "high_risk"
                               or not (a["retry"]["idempotent"] or a["retry"]["sameKeySupported"]) for a in checked[:len(value["steps"])])):
                    error("capability_unsupported", "Automatic recovery requires process Actions with a nonempty policy, retry safety and trusted cleanup")
            self._check_literal_product_sessions(value, checked)
            self._check_literal_product_qq(value, checked)
            from packages.jobs.session_delivery_runtime import validate_references
            validate_references(value, checked)
        if kind == "trigger":
            selector = value["creatorSelector"]
            if selector["mode"] == "ids":
                for cid in selector["ids"]:
                    item = self.creators.get(cid)
                    if item is None or item["state"] == "retired":
                        error("reference_conflict", "Creator unavailable")
        return checked

    @staticmethod
    def _check_literal_product_sessions(value, checked):
        # Reference validation returns step Actions first, in step order.
        # Restrict business lookup to known product operations, never argument
        # names on arbitrary custom Actions. No Worker or history is needed.
        products = {"product_session_message": ("session-message", "sessionId"),
                    "product_session_broadcast": ("session-broadcast", "sessionIds"),
                    "product_session_message_v2": ("session-message", "sessionId"),
                    "product_session_broadcast_v2": ("session-broadcast", "sessionIds"),
                    "product_assign_v2": ("assign", "sessionId"),
                    "product_assign_runtime_v1": ("assign", "sessionId"),
                    "product_session_message_runtime_v1": ("session-message", "sessionId"),
                    "product_session_broadcast_runtime_v1": ("session-broadcast", "sessionIds")}
        for index, (step, action) in enumerate(zip(value["steps"], checked)):
            product = products.get(action["id"])
            config = action.get("definition") or {}
            if product is None or config.get("system") is not True:
                continue
            operation, target = product
            if config.get("execution", {}).get("operation") != operation:
                continue
            binding = step["arguments"].get(target, {})
            if binding.get("from") != "literal":
                continue
            path = f"definition.steps.{index}.arguments.{target}"
            # Validate this fixed target only; dynamic text is not a complete
            # arguments object. Reuse the same schema validator as contracts.
            fc._validate_instance(config["inputSchema"]["properties"][target], binding["value"], path=path)
            ids = binding["value"] if target == "sessionIds" else [binding["value"]]
            from packages.core import session
            for sid in ids:
                if session.get(sid, load_history=False) is None:
                    raise fc.ContractError("invalid_schema", "Session target does not exist",
                        path=path,
                        details={"reason": "session_not_found", "id": sid})

    @staticmethod
    def _check_literal_product_qq(value, checked):
        """Validate known literals on the immutable trusted QQ publication only."""
        from packages.jobs.actions import normalize_scheduled_qq_action
        provider = {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.v2"), "generation": 1}
        for index, (step, action) in enumerate(zip(value["steps"], checked)):
            config = action.get("definition") or {}
            expected = ({"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.qq-runtime.v1"), "generation": 2}
                if action["id"] == "product_channel_runtime_v1" else provider)
            if (action["id"] not in {"product_channel", "product_channel_runtime_v1"} or config.get("system") is not True
                    or config.get("providerRef") != expected
                    or config.get("execution", {}).get("operation") != "channel"):
                continue
            arguments = step["arguments"]
            fixed = {key: arguments[key]["value"] for key in ("targetType", "targetId", "botUin")
                     if arguments.get(key, {}).get("from") == "literal"}
            if action['id'] == 'product_channel_runtime_v1' and fixed.get('botUin', '') is None:
                fixed.pop('botUin')  # explicit new-release null means original default bot
            if fixed:
                # Supply valid validation-only values for unresolved fields;
                # never persist them or turn a dynamic binding into a target.
                _, invalid = normalize_scheduled_qq_action({"api": "send_qq", "args": {
                    "targetType": "user", "targetId": "10000", **fixed}})
                if invalid:
                    raise fc.ContractError("invalid_schema", invalid,
                        path=f"definition.steps.{index}.arguments")
            text = arguments.get("text", {})
            if text.get("from") == "literal":
                raw = text["value"]
                if not isinstance(raw, str) or not raw.strip():
                    raise fc.ContractError("invalid_schema", "QQ text must contain a non-whitespace character",
                        path=f"definition.steps.{index}.arguments.text")

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

    def preview_creator(self, body, *, actor):
        """Read-only schedule calculation through the original time provider."""
        self._permit(actor, "event_creator", "read")
        if (not isinstance(body, dict) or set(body) != {"definition", "at", "count"}
                or type(body["count"]) is not int or not 1 <= body["count"] <= 100):
            error("invalid_schema", "Preview requires definition, at and count in 1..100")
        fc.check_timestamp(body["at"], "at")
        if not isinstance(body["definition"], dict):
            error("invalid_schema", "Preview definition must be an object")
        value = self.creators.registry.validate_definition(body["definition"])
        provider = self.creators.registry.get(value["providerRef"])
        if (provider.provider_type, provider.provider_id) != ("time", "scheduler"):
            error("capability_unsupported", "This Event Creator provider has no time preview")
        from packages.scheduler.providers import instant
        import heapq
        rules = value["config"]["rules"]
        pending, items = [], []
        for index, rule in enumerate(rules):
            point = provider.next_after(rule, body["at"])
            if point is not None:
                heapq.heappush(pending, (instant(point["at"]), index, point))
        while pending and len(items) < body["count"]:
            _, index, point = heapq.heappop(pending)
            items.append({"ruleId": rules[index]["ruleId"], **point})
            following = provider.next_after(rules[index], point["at"])
            if following is not None:
                heapq.heappush(pending, (instant(following["at"]), index, following))
        return {"items": items}

    def admit_quota(self, request_key, policy, at, *, actor):
        """Quota business adapter over the existing canonical composition port."""
        for kind in ("job", "event_creator", "trigger"):
            self._permit(actor, kind, "create")
            self._permit(actor, kind, "lifecycle", execution=True)
        self._permit(actor, "job", "execute", execution=True)
        self._permit(actor, "action", "manageSystemActions", execution=True)
        with self.transaction() as db:
            def mutate(operation_id):
                from packages.automation.quota_native import ACTION
                action = self._get(db, "action", ACTION)
                key = "quota-compose:" + fc.digest({"scope": self._scope(actor, "create"), "key": request_key})
                prior = self.creators.composition_for_key(key)
                graph = prior["desiredGraph"] if prior else self.quota_native.graph(policy, at, action, key)
                composed = self.compositions.compose(request_key=key, desired_graph=graph,
                    authorization_evidence={"principal": principal(actor), "authority": copy.deepcopy(actor)}, activate=True)
                ids = composed["objectIds"]
                source = self.creators.get(ids["creators"]["clock"])
                if policy["purpose"] != "monitor95" and not source["cursor"]["rules"]["query"]["nextFireAt"]:
                    error("provider_unavailable", "Quota once time expired during admission; retry original request")
                objects = [("job", ids["job"]), ("event_creator", ids["creators"]["clock"]),
                           ("trigger", ids["triggers"]["query"])]
                for kind, oid in objects:
                    db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)", (kind, oid, fc.canonical_json(actor)))
                refs = [object_ref(kind, self._object(db, kind, oid)) for kind, oid in objects]
                return {"jobId": ids["job"], "compositionKey": key, "quotaPolicy": policy,
                        "nativeRefs": refs}, refs, False
            return self._operation(db, actor, "create", request_key, policy, "job", mutate)

    def _legacy_action_ref(self, db, legacy_id):
        row = db.execute(
            "SELECT actionId,revision,digest FROM final_action_aliases WHERE legacyId=?",
            (legacy_id,),
        ).fetchone()
        if row is not None:
            return {"id": row[0], "revision": int(row[1]), "digest": row[2]}
        try:
            return object_ref("action", self._get(db, "action", legacy_id))
        except KeyError:
            error("reference_conflict", "Legacy Action has no canonical ActionRef: " + str(legacy_id))

    def _legacy_template_definition(self, db, item):
        """Translate one old Template record without creating a second writer.

        The old record remains the caller's identity and source of truth for the
        compatibility response.  Canonical validation and references still run
        before the existing FinalService write transaction.
        """
        if not isinstance(item, dict) or set(item) - {"id", "name", "description", "config", "revision", "referenceGeneration"}:
            error("invalid_schema", "Invalid legacy Job Template fields")
        config = item.get("config")
        if not isinstance(config, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            error("invalid_schema", "Invalid legacy Job Template")
        raw_steps = config.get("steps")
        if raw_steps is None:
            raw_steps = [{"stepId": "execute", "actionId": config.get("actionId"),
                          "arguments": config.get("arguments", {})}]
        if (not isinstance(raw_steps, list) or not raw_steps or
                any(not isinstance(step, dict) or set(step) - {"stepId", "actionId", "arguments"}
                    or not isinstance(step.get("actionId"), str)
                    or not isinstance(step.get("arguments", {}), dict) for step in raw_steps)):
            error("invalid_schema", "Invalid legacy Template steps")
        steps = []
        for index, step in enumerate(raw_steps):
            ref = self._legacy_action_ref(db, step["actionId"])
            action_version = self._action_version_record(db, ref["id"], ref["revision"], digest=ref["digest"])
            arguments = {key: (copy.deepcopy(value) if isinstance(value, dict) and set(value) <= {"from", "value", "path"}
                               and "from" in value else {"from": "literal", "value": copy.deepcopy(value)})
                         for key, value in step.get("arguments", {}).items()}
            steps.append({"stepId": step.get("stepId") or ("step_" + str(index)),
                          "actionRef": ref, "arguments": arguments,
                          "completion": {"contract": action_version["definition"].get("defaultCompletion", "ordinary")}})
        trigger = config.get("trigger", {"kind": "manual"})
        if not isinstance(trigger, dict):
            error("invalid_schema", "Invalid legacy Template trigger")
        composition = {"creators": [], "triggers": []}
        if trigger.get("kind") == "event":
            selector = trigger.get("creatorSelector")
            if not isinstance(selector, dict) or set(selector) not in ({"mode", "ids"}, {"mode", "providerType"}):
                error("reference_conflict", "Event Template creator selector requires explicit canonical mapping")
            creator_keys = []
            if selector["mode"] == "ids":
                for index, creator_id in enumerate(selector.get("ids", [])):
                    creator = self.creators.get(creator_id)
                    if creator is None:
                        error("reference_conflict", "Event Template creator is not a canonical Creator: " + str(creator_id))
                    key = "creator_" + str(index)
                    creator_keys.append(key)
                    composition["creators"].append({"mode": "reference", "key": key,
                                                   "ref": self.creators.ref(creator_id)})
            event_types = trigger.get("eventTypes")
            if not isinstance(event_types, list) or not event_types:
                error("invalid_schema", "Event Template requires eventTypes")
            definition_value = {"name": item["name"] + " trigger", "eventTypes": list(event_types),
                                "targetJobId": "job",
                                "creatorSelector": {"mode": "ids", "ids": creator_keys}}
            if selector["mode"] == "registeredType":
                definition_value["creatorSelector"] = copy.deepcopy(selector)
            query = trigger.get("query")
            if query is not None:
                if not isinstance(query, dict) or any(not isinstance(value, (str, int, float, bool, type(None))) for value in query.values()):
                    error("reference_conflict", "Event Template query needs a scalar canonical eventFilter")
                definition_value["eventFilter"] = copy.deepcopy(query)
            composition["triggers"].append({"key": "event_trigger", "definition": definition_value,
                                           "targetJobKey": "job", "creatorKeys": creator_keys})
        job = {"name": item["name"], "description": item.get("description", ""), "steps": steps,
               "retention": {"policyRef": "legacy-template"}}
        return fc.validate_definition("template", {"name": item["name"],
            "description": item.get("description", ""), "jobDefinition": job, "composition": composition})

    def _legacy_template_alias(self, db, legacy_id):
        row = db.execute("SELECT document FROM final_documents WHERE kind='legacy_template_alias' AND id=?", (legacy_id,)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def legacy_template_list(self, *, actor):
        self._permit(actor, "template", "read")
        with self.transaction(readonly=True) as db:
            result = []
            aliases = [json.loads(row[0]) for row in db.execute(
                "SELECT document FROM final_documents WHERE kind='legacy_template_alias' ORDER BY id")]
            aliased_canonical = {item.get("canonicalId") for item in aliases}
            result.extend(copy.deepcopy(item["raw"]) for item in aliases if not item.get("deleted"))
            for item in self._all(db, "template"):
                if item["id"] not in aliased_canonical:
                    result.append({"id": item["id"], "name": item["name"], "description": item.get("description", ""),
                                   "config": {"name": item["name"], "enabled": False},
                                   "revision": item["revision"], "referenceGeneration": {}})
            return {"templates": result}

    def legacy_template_write(self, item, expected_revision, request_key, *, actor, generation=None):
        self._permit(actor, 'template', 'create' if expected_revision == 0 else 'updateConfig')
        if not isinstance(item, dict):
            error("invalid_schema", "Legacy Template item must be an object")
        legacy_id = item.get("id")
        if legacy_id is not None and (not isinstance(legacy_id, str) or not legacy_id.startswith("jtpl_")):
            error("invalid_schema", "Invalid legacy Template ID")
        with self.transaction() as db:
            def mutate(operation_id):
                alias = self._legacy_template_alias(db, legacy_id) if legacy_id else None
                canonical_id = alias['canonicalId'] if alias else legacy_id
                current = None
                if canonical_id:
                    row = db.execute("SELECT document FROM final_documents WHERE kind='template' AND id=?", (canonical_id,)).fetchone()
                    current = json.loads(row[0]) if row else None
                if alias:
                    if alias.get('deleted'):
                        error('reference_conflict', 'Deleted legacy Template identity cannot be recreated')
                    fc.check_revision(alias['raw']['revision'], expected_revision)
                elif current:
                    fc.check_revision(current['revision'], expected_revision)
                elif expected_revision != 0:
                    error('revision_conflict', 'New legacy Template requires revision zero')
                definition_value = self._legacy_template_definition(db, item)
                successor = current is not None and current['state'] == 'published'
                body = {'definition': definition_value, 'expectedRevision': current['revision'] if current else 0,
                        'requestKey': operation_id + ':write'}
                if current and not successor:
                    result = self.write('template', canonical_id, body, actor=actor)
                elif successor:
                    # A new immutable successor must never overwrite the old
                    # published ID. Only the legacy alias advances atomically.
                    successor_id = 'jtpl_' + fc.digest([legacy_id, expected_revision, definition_value])[:24]
                    if db.execute("SELECT 1 FROM final_documents WHERE kind='template' AND id=?", (successor_id,)).fetchone():
                        error('reference_conflict', 'Template successor identity already exists')
                    self._references(db, 'template', definition_value)
                    stored = self._envelope('template', definition_value, actor, object_id=successor_id)
                    self._save(db, 'template', successor_id, stored)
                    result = {'result': {'object': stored}}
                else:
                    body['legacyId'] = canonical_id or ('jtpl_' + fc.digest(definition_value)[:24])
                    body['expectedRevision'] = 0
                    result = self.write('template', None, body, actor=actor)
                stored = result['result']['object']
                if successor:
                    stored = self.control('template', stored['id'], {'operation': 'publish',
                        'expectedRevision': stored['revision'], 'expectedStateRevision': stored['stateRevision'],
                        'requestKey': operation_id + ':publish', 'parameters': {}}, actor=actor)['result']['object']
                raw = copy.deepcopy(item)
                raw.update(id=legacy_id or stored['id'], revision=expected_revision + 1,
                           referenceGeneration=copy.deepcopy(generation or item.get('referenceGeneration', {})))
                self._save(db, 'legacy_template_alias', raw['id'], {'raw': raw,
                    'canonicalRef': object_ref('template', stored), 'canonicalId': stored['id'], 'deleted': False})
                return {'item': raw, 'canonical': stored}, [object_ref('template', stored)], False
            response = self._operation(db, actor, 'create' if expected_revision == 0 else 'update', request_key,
                {'legacyTemplateWrite': item, 'expectedRevision': expected_revision, 'generation': generation},
                'template', mutate)
            return response['result']

    def legacy_template_delete(self, template_id, expected_revision, request_key, *, actor):
        self._permit(actor, 'template', 'delete')
        with self.transaction() as db:
            def mutate(operation_id):
                alias = self._legacy_template_alias(db, template_id)
                canonical_id = alias['canonicalId'] if alias else template_id
                item = self._object(db, 'template', canonical_id)
                fc.check_revision(alias['raw']['revision'] if alias else item['revision'], expected_revision)
                result = self.retire('template', canonical_id, {'expectedRevision': item['revision'],
                    'expectedStateRevision': item['stateRevision'], 'requestKey': operation_id + ':retire',
                    'parameters': {}}, actor=actor)
                if alias:
                    alias['deleted'] = True
                    self._save(db, 'legacy_template_alias', template_id, alias)
                return {'deleted': True, 'templateId': template_id, 'result': result}, result['objectRefs'], False
            return self._operation(db, actor, 'retire', request_key,
                {'legacyTemplateDelete': template_id, 'expectedRevision': expected_revision}, 'template', mutate)['result']

    def legacy_template_instantiate(self, template_id, expected_revision, request_key, overrides=None, *, actor, activate=False):
        canonical_id, canonical_revision = template_id, expected_revision
        with self.transaction(readonly=True) as db:
            alias = self._legacy_template_alias(db, template_id)
            if alias:
                if alias.get('deleted'):
                    error('reference_conflict', 'Legacy Template is deleted')
                fc.check_revision(alias['raw']['revision'], expected_revision)
                canonical_id = alias['canonicalId']
                canonical_revision = alias['canonicalRef']['revision']
        result = self.instantiate_template(canonical_id, {'expectedRevision': canonical_revision,
            'requestKey': request_key, 'activate': activate, 'overrides': overrides or {}}, actor=actor)
        objects = result["result"]["objectIds"] if "result" in result else result.get("objectIds", {})
        return {"job": {"jobId": objects.get("job"), "templateId": template_id,
                         "templateRevision": expected_revision, "displayOverrides": copy.deepcopy(overrides or {})},
                "result": result}

    def instantiate_time_template(self, template_id, body, *, actor):
        """Compatibility name for the existing generic Template operation."""
        return self.instantiate_template(template_id, body, actor=actor)

    def instantiate_template(self, template_id, body, *, actor, fixed_template=None):
        """Complete passive or time composition; no Run or second operation ledger.

        CreatorStore, FinalService operations and the composition callbacks join
        the same SQLite transaction. A failed call rolls all of them back;
        an existing composition checkpoint resumes under its original key.
        """
        if (not isinstance(body, dict) or set(body) - {"expectedRevision", "requestKey", "activate", "overrides"}
                or not {"expectedRevision", "requestKey"} <= set(body)
                or type(body.get("activate", False)) is not bool):
            error("invalid_schema", "Invalid Template instantiation")
        overrides = body.get("overrides", {})
        if (not isinstance(overrides, dict) or set(overrides) - {"name", "description"}
                or any(not isinstance(value, str) for value in overrides.values())
                or ("name" in overrides and not overrides["name"].strip())):
            error("invalid_schema", "Invalid display overrides")
        self._permit(actor, "template", "use")
        activate = body.get("activate", False)
        request = {**body, "activate": activate, "overrides": copy.deepcopy(overrides)}
        if fixed_template is not None:
            fixed_template = fc.validate_object('template', copy.deepcopy(fixed_template))
            if fixed_template['id'] != template_id:
                error('reference_conflict', 'Fixed Template identity differs')
            request['fixedTemplateDigest'] = fc.digest(fixed_template)
        with self.transaction() as db:
            def mutate(operation_id):
                template = fixed_template if fixed_template is not None else self._object(db, "template", template_id)
                fc.check_revision(template["revision"], body["expectedRevision"])
                if template["state"] != "published":
                    error("reference_conflict", "Only a published Template can be instantiated")
                value = definition("template", template)
                if overrides:
                    value = copy.deepcopy(value)
                    value["jobDefinition"] = {**value["jobDefinition"], **overrides}
                    value = fc.validate_definition("template", value)
                specs = value["composition"]["creators"]
                if not specs and value["composition"]["triggers"]:
                    error("capability_unsupported", "Source-free instantiation supports only a Job-only Template")
                self._permit(actor, "job", "create")
                self._references(db, "template", value, enabled=activate)
                # Use the existing Job target validation before creating the graph.
                self._references(db, "job", value["jobDefinition"], enabled=activate)
                for spec in specs:
                    if spec["mode"] == "create":
                        self._permit(actor, "event_creator", "create")
                        source = self.creators.registry.validate_definition(spec["definition"])
                    else:
                        self._permit(actor, "event_creator", "read")
                        source = self.creators.get(spec["ref"]["id"])
                    if source is None or source["providerRef"]["type"] != "time" or source["providerRef"]["id"] != "scheduler":
                        error("capability_unsupported", "Only time Scheduler compositions are supported here")
                if value["composition"]["triggers"]:
                    self._permit(actor, "trigger", "create")
                if activate:
                    self._permit(actor, "job", "lifecycle")
                    self._permit(actor, "job", "execute", execution=True)
                    if value["composition"]["triggers"]:
                        self._permit(actor, "trigger", "lifecycle", execution=True)
                    if any(spec["mode"] == "create" for spec in specs):
                        self._permit(actor, "event_creator", "lifecycle", execution=True)
                # Omitted overrides and an explicit empty object are the same
                # request across the old HTTP/MCP envelopes. Keep composition
                # identity stable while fencing non-empty display changes.
                key_payload = {"scope": self._scope(actor, "instantiate-template"),
                               "requestKey": body["requestKey"]}
                if overrides:
                    key_payload["overrides"] = overrides
                key = "template:" + fc.digest(key_payload)
                composed = self.compositions.instantiate_template(value, request_key=key, activate=activate,
                    authorization_evidence={"principal": principal(actor)})
                ids = composed["objectIds"]
                objects = [("job", ids["job"]), *[("trigger", tid) for tid in ids["triggers"].values()]]
                objects += [("event_creator", ids["creators"][spec["key"]]) for spec in specs if spec["mode"] == "create"]
                for kind, oid in objects:
                    db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)", (kind, oid, fc.canonical_json(actor)))
                refs = [object_ref(kind, self._object(db, kind, oid)) for kind, oid in objects]
                return {"objectIds": ids, "compositionId": composed["operationId"],
                        "phase": composed["phase"], "activated": activate}, refs, False
            return self._operation(db, actor, "instantiate-template", body["requestKey"],
                                   {"templateId": template_id, **request}, "template", mutate)

    def resolve_time_input(self, body, *, actor):
        self._permit(actor, "event_creator", "read")
        provider = self.creators.registry.providers.get(("time", "scheduler"))
        if provider is None:
            error("provider_unavailable", "Time provider is unavailable")
        return provider.resolve_input(body["value"], body.get("timezone"))

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
            target_id = trigger.get("targetJobId", trigger.get("target", {}).get("jobId"))
            job = self._get(db, "job", target_id)["canonical"] if target_id else {"id": None, "name": "Direct Action", "configRevision": None}
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

    def _normalize_template_form(self, db, body, *, actor):
        """Existing Template POST convenience envelope -> complete canonical definition.

        Only one fixed published product step and a passive empty composition
        are accepted. The form itself is not a document field or runtime input.
        """
        if set(body) != {"definition", "form", "expectedRevision", "requestKey"}:
            error("invalid_schema", "Unknown form write fields")
        form = body["form"]
        if not isinstance(form, dict) or set(form) != {"kind", "fields"} or form["kind"] not in {"assign", "shell"}:
            error("invalid_schema", "Invalid assign/shell form")
        value = fc.validate_definition("template", body.get("definition"))
        job = value["jobDefinition"]
        if len(job["steps"]) != 1 or value["composition"] != {"creators": [], "triggers": []}:
            error("invalid_schema", "Simple form requires one product step and no schedule")
        step = job["steps"][0]
        raw_fields = copy.deepcopy(form["fields"])
        if not isinstance(raw_fields, dict) or not isinstance(raw_fields.get('runtime', False), bool):
            error('invalid_schema', 'Form fields must be an object and runtime must be boolean')
        runtime_opt_in = raw_fields.pop("runtime", False)
        if runtime_opt_in and form["kind"] != "assign":
            error("capability_unsupported", "Runtime Template opt-in is available for assign only")
        expected = ("product_assign_runtime_v1" if runtime_opt_in else "product_assign_v2") \
            if form["kind"] == "assign" else "product_process_logs_v1"
        action = fc._check_action_ref(step["actionRef"], path="definition.jobDefinition.steps.0.actionRef",
            completion=step["completion"], completion_path="definition.jobDefinition.steps.0.completion",
            resolver=self._resolver(db), require_enabled=False)
        config = action["definition"]
        if (action["id"] != expected or config.get("system") is not True
                or config["execution"]["operation"] != ("assign" if form["kind"] == "assign" else "process")):
            error("reference_conflict", "Form requires the fixed product Action publication")
        if runtime_opt_in:
            if any(binding["from"] != "literal" for binding in step["arguments"].values()):
                error("invalid_schema", "Runtime form canonical fields must start literal")
        elif any(binding["from"] != "literal" for binding in step["arguments"].values()):
            error("invalid_schema", "Simple form arguments must be literal")
        explicit = {key: binding["value"] for key, binding in step["arguments"].items()}
        arguments = normalize_legacy_form_fields(raw_fields, kind=form["kind"], canonical=explicit)
        if form["kind"] == "assign":
            arguments = self._session_source(arguments, actor)
        if runtime_opt_in:
            self._check_literal_product_sessions({'steps': [{'arguments': {
                key: {'from': 'literal', 'value': val} for key, val in arguments.items()}}]}, [action])
            step["arguments"] = {
                "sessionId": {"from": "input", "path": "delivery.target"},
                "text": {"from": "input", "path": "delivery.text"},
                "source": {"from": "literal", "value": arguments["source"]},
                **({"sourceSessionId": {"from": "literal", "value": arguments["sourceSessionId"]}}
                   if arguments.get("sourceSessionId") else {}),
                **({"taskId": {"from": "literal", "value": arguments["taskId"]}} if 'taskId' in arguments else {}),
            }
            job["inputSchema"] = {"type": "object", "properties": {"delivery": {
                "type": "object", "properties": {"target": {"type": ["string", "null"], "minLength": 1},
                                                     "text": {"type": "string", "minLength": 1}},
                "required": ["target", "text"], "additionalProperties": False}},
                "required": ["delivery"], "additionalProperties": False}
            job["arguments"] = {"delivery": {"target": arguments["sessionId"], "text": arguments["text"]}}
            job["deliveryRuntime"] = {"owner": "delivery-manager", "targetMode": "runtime",
                "inputFields": ["delivery.target", "delivery.text"], "allowedStates": ["pending", "reserved"],
                "concurrency": "job-config-cas+event-receipt", "audit": "final_operations",
                "phase": "before-reservation", "unknownPolicy": "query-original"}
        else:
            step["arguments"] = {key: {"from": "literal", "value": val} for key, val in arguments.items()}
        self._references(db, "job", job)
        return {**{key: val for key, val in body.items() if key != "form"}, "definition": value}

    def _require_action_management(self, actor, action=None, *, capability="lifecycle"):
        """Apply the existing Action matrix plus the system Action fence."""
        self._permit(actor, "action", capability)
        if action is not None and action.get("system"):
            if actor.get("type") not in {"system", "plugin"}:
                allowed, revision = self.auth.permission_allowed("action", "manageSystemActions")
                if actor.get("type") != "agent" or not allowed or actor.get("revision") != revision:
                    error("authorization_denied", "System Actions require manageSystemActions")

    def _action_write(self, object_id, body, *, actor, draft_enabled=None, management_capability=None, legacy_projection=True):
        """Create or edit a draft without rewriting a published version."""
        self._permit(actor, "action", management_capability or ("create" if object_id is None else "updateConfig"))
        allowed = {"definition", "expectedRevision", "requestKey", "requestDigest"}
        if not isinstance(body, dict) or set(body) - allowed:
            error("invalid_schema", "Unknown Action write fields")
        value = fc.validate_write_input(
            "action", {key: body[key] for key in ("definition", "expectedRevision", "requestKey")},
            create=object_id is None,
        )
        requested = copy.deepcopy(value["definition"])
        with self.transaction() as db:
            self._ensure_action_schema(db)

            def mutate(operation_id):
                old = None
                if object_id is not None:
                    try:
                        old = self._get(db, "action", object_id)
                    except KeyError:
                        raise
                    fc.check_revision(old["revision"], value["expectedRevision"])
                    if old["state"] == "retired":
                        error("reference_conflict", "Retired Action cannot be edited")
                    if old.get("system"):
                        if requested.get("system") is not True:
                            error("authorization_denied", "System Action ownership cannot be changed")
                        self._require_action_management(actor, old, capability="updateConfig")
                if requested.get("system") and actor.get("type") not in {"system", "plugin"}:
                    self._require_action_management(actor, {"system": True}, capability="create" if old is None else "updateConfig")

                action_id = object_id or new_id("action_")
                revision = (old["revision"] + 1) if old is not None else 1
                item = self._envelope("action", requested, actor, object_id=action_id, state="draft")
                if old is not None:
                    item.update({key: copy.deepcopy(old[key]) for key in fc.SERVER_FIELDS if key in old})
                    item.update(id=action_id, revision=revision, state="draft", stateRevision=old["stateRevision"] + 1,
                                updatedAt=now(), enabled=old.get("enabled", False))
                else:
                    item.update(id=action_id, revision=revision, state="draft", enabled=False)
                if draft_enabled is not None:
                    # Trusted legacy configuration adapter: a disabled edit
                    # creates a disabled draft without touching frozen versions.
                    item['enabled'] = draft_enabled
                item = fc.validate_object("action", item)
                digest = fc.digest(requested)
                previous = db.execute(
                    "SELECT digest FROM final_action_versions WHERE actionId=? AND revision=?",
                    (action_id, revision),
                ).fetchone()
                if previous is not None and previous[0] != digest:
                    error("revision_conflict", "Action draft revision already contains different content")
                if old is not None:
                    db.execute(
                        "UPDATE final_action_versions SET state='superseded' WHERE actionId=? AND state='draft' AND revision<>?",
                        (action_id, revision),
                    )
                db.execute(
                    "INSERT OR IGNORE INTO final_action_versions(actionId,revision,digest,state,definition,createdAt,publishedAt) VALUES(?,?,?,?,?,?,NULL)",
                    (action_id, revision, digest, "draft", fc.canonical_json(requested), now()),
                )
                db.execute(
                    "INSERT OR REPLACE INTO final_action_lifecycle(actionId,revision,enabled,state,stateRevision,updatedAt) VALUES(?,?,?,?,?,?)",
                    (action_id, revision, int(item.get("enabled", False)), "draft", item["stateRevision"], now()),
                )
                self._save(db, "action", action_id, item)
                if legacy_projection:
                    from packages.actions.group_migration import advance_action_projection
                    advance_action_projection(db, action_id)
                db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)",
                           ("action", action_id, fc.canonical_json(actor)))
                return (
                    {"object": item, "version": {"revision": revision, "digest": digest, "state": "draft"}},
                    [object_ref("action", item)],
                    False,
                )

            return self._operation(db, actor, "create" if object_id is None else "update", value["requestKey"],
                                   {"kind": "action", "objectId": object_id, **value}, "action", mutate)

    def _action_control(self, object_id, body, *, actor, management_capability='lifecycle', legacy_projection=True,
                        legacy_system_lifecycle=False):
        op = body["operation"]
        with self.transaction() as db:
            self._ensure_action_schema(db)

            def mutate(operation_id):
                item = self._get(db, "action", object_id)
                if legacy_system_lifecycle:
                    # Only the retained old enable/disable capability for an
                    # exact immutable product successor. Public system CRUD
                    # continues to use its original management restrictions.
                    if op not in {'enable', 'disable'}:
                        error('authorization_denied', 'Legacy system lifecycle cannot edit or publish')
                    from packages.actions.legacy_families import system_view
                    system_view(definition('action', item), item['enabled'])
                    self._permit(actor, 'action', 'lifecycle', execution=True)
                    self._permit(actor, 'action', 'manageSystemActions', execution=True)
                else:
                    self._require_action_management(actor, item, capability=management_capability)
                fc.check_revision(item["revision"], body["expectedRevision"])
                fc.check_revision(item["stateRevision"], body["expectedStateRevision"])
                parameters = copy.deepcopy(body.get("parameters") or {})
                target_revision = parameters.pop("revision", item["revision"])
                if type(target_revision) is not int or target_revision < 1:
                    error("invalid_schema", "Action version revision must be a positive integer")
                if parameters:
                    error("invalid_schema", "Unsupported Action operation parameters")
                version = self._action_version_record(db, object_id, target_revision)
                if op == "publish":
                    if item["state"] != "draft" or target_revision != item["revision"]:
                        error("reference_conflict", "Only the current draft can be published")
                    db.execute("UPDATE final_action_versions SET state='published',publishedAt=? WHERE actionId=? AND revision=?",
                               (now(), object_id, target_revision))
                    db.execute("UPDATE final_action_lifecycle SET state='published',enabled=1,stateRevision=stateRevision+1,updatedAt=? WHERE actionId=? AND revision=?",
                               (now(), object_id, target_revision))
                    item.update(state="published", enabled=True, stateRevision=item["stateRevision"] + 1, updatedAt=now())
                elif op in {"enable", "disable"}:
                    if version["state"] != "published":
                        error("reference_conflict", "Only a published Action version can be enabled or disabled")
                    enabled = op == "enable"
                    db.execute("UPDATE final_action_lifecycle SET enabled=?,stateRevision=stateRevision+1,updatedAt=? WHERE actionId=? AND revision=?",
                               (int(enabled), now(), object_id, target_revision))
                    if target_revision == item["revision"]:
                        item.update(enabled=enabled, stateRevision=item["stateRevision"] + 1, updatedAt=now())
                elif op == "retire":
                    if target_revision == item["revision"] and item["state"] in {"draft", "published"}:
                        item.update(state="retired", enabled=False, stateRevision=item["stateRevision"] + 1, updatedAt=now())
                    db.execute("UPDATE final_action_lifecycle SET state='retired',enabled=0,stateRevision=stateRevision+1,updatedAt=? WHERE actionId=? AND revision=?",
                               (now(), object_id, target_revision))
                elif op == "transfer-owner":
                    error("unsupported_operation", "Action owner transfer needs its explicit composition contract")
                else:
                    error("unsupported_operation", "Unsupported Action operation")
                item = fc.validate_object("action", item)
                self._save(db, "action", object_id, item)
                if legacy_projection:
                    from packages.actions.group_migration import advance_action_projection
                    advance_action_projection(db, object_id)
                db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)",
                           ("action", object_id, fc.canonical_json(actor)))
                return (
                    {"object": item, "version": {"revision": target_revision,
                        "digest": version["digest"], "state": ("retired" if op == "retire" else version["state"])}},
                    [object_ref("action", item)],
                    False,
                )

            return self._operation(db, actor, op, body["requestKey"],
                                   {"kind": "action", "id": object_id, **body}, "action", mutate)

    def write(self, kind, object_id, body, *, actor):
        if kind == "action":
            return self._action_write(object_id, body, actor=actor)
        self._permit(actor, kind, "create" if object_id is None else "updateConfig")
        if "form" in body:
            if kind != "template" or object_id is not None:
                error("invalid_schema", "Form mapping is only available at Template creation")
        allowed = {"definition", "expectedRevision", "requestKey", "requestDigest"}
        if "form" in body:
            allowed.add("form")
        if kind == "template":
            allowed.add("legacyId")
        if kind == "event_creator":
            allowed.add("expectedReferences")
        if kind == "trigger":
            allowed.add("expectedCreatorRevisions")
        if set(body) - allowed:
            error("invalid_schema", "Unknown write fields")
        value = fc.validate_write_input(kind, {key: body[key] for key in ("definition", "expectedRevision", "requestKey")}, create=object_id is None)
        forced_id = body.get("legacyId")
        if forced_id is not None:
            if kind != "template" or object_id is not None or not isinstance(forced_id, str) or not forced_id.startswith("jtpl_"):
                error("invalid_schema", "Invalid legacy Template identity")
        if "form" in body:
            value["form"] = copy.deepcopy(body["form"])
        if "expectedReferences" in body:
            value["expectedReferences"] = body["expectedReferences"]
        if "expectedCreatorRevisions" in body:
            value["expectedCreatorRevisions"] = body["expectedCreatorRevisions"]
        request_value = value
        with self.transaction() as db:
            def mutate(operation_id):
                runtime_audit = None
                # _operation has already resolved a repeated requestKey before
                # this callback. Allocate only for a new creation, inside the
                # existing profile owner fence and SQLite writer transaction.
                creation_value = copy.deepcopy(request_value)
                if kind == "job" and object_id is None and "name" not in creation_value["definition"]:
                    taken = set()
                    for row in self._all(db, "job"):
                        match = re.fullmatch(r"job-(\d+)", str(row.get("canonical", {}).get("name") or "").strip())
                        if match:
                            taken.add(int(match[1]))
                    number = 1
                    while number in taken:
                        number += 1
                    creation_value["definition"]["name"] = "job-" + str(number)
                    creation_value = fc.validate_write(kind, {key: creation_value[key]
                        for key in ("definition", "expectedRevision", "requestKey")}, create=True)
                value = (fc.validate_write(kind, self._normalize_template_form(db,
                             {key: request_value[key] for key in ("definition", "form", "expectedRevision", "requestKey")}, actor=actor), create=True)
                         if "form" in request_value else creation_value)
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
                if kind == "job" and old and value["definition"]["overlap"]["maxActive"] < old["overlap"]["maxActive"]:
                    stored = self._get(db, "job", object_id)
                    occupied = sum(run["state"] in {"running", "uncertain", "settling", "cancelling"}
                        for run in aggregate(stored, self.root)["runs"].values())
                    if occupied > value["definition"]["overlap"]["maxActive"]:
                        error("reference_conflict", "Active Run count exceeds the requested concurrency limit")
                if kind == "event_creator":
                    if old is None:
                        item = self.creators.create(value["definition"], request_key=operation_id,
                                                    created_by=principal(actor), owner=principal(actor))
                    else:
                        item = self.creators.update(object_id, value["definition"], expected_revision=value["expectedRevision"], request_key=operation_id)
                    item = self._public(kind, item)
                else:
                    item = self._envelope(kind, value["definition"], actor, object_id=(forced_id if old is None else object_id)) if old is None else {
                        **value["definition"], **{key: old[key] for key in fc.SERVER_FIELDS if key in old},
                        fc.VERSION_FIELD[kind]: old[fc.VERSION_FIELD[kind]] + 1, "updatedAt": now()}
                    item = fc.validate_object(kind, item)
                    stored = item
                    if kind == "job":
                        stored = self._get(db, kind, object_id) if old else {"jobId": item["id"]}
                        if (item.get("deliveryRuntime") or item.get("notification", {}).get("runtime")
                                or (old and (old.get("deliveryRuntime") or old.get("notification", {}).get("runtime")))):
                            fields = list(dict.fromkeys(item.get("deliveryRuntime", {}).get("inputFields", [])
                                + (old.get("deliveryRuntime", {}).get("inputFields", []) if old else [])))
                            before = definition("job", old) if old else {}
                            def runtime_values(doc):
                                inputs = {}
                                for name in fields:
                                    current = doc.get("arguments", {})
                                    for piece in name.split('.'):
                                        current = current.get(piece) if isinstance(current, dict) else None
                                    inputs[name] = copy.deepcopy(current)
                                return {"inputs": inputs,
                                        "notification": copy.deepcopy(doc.get("notification", {}))}
                            runtime_audit = {"operationId": operation_id, "actor": principal(actor),
                                "fromRevision": old["configRevision"] if old else 0,
                                "toRevision": item["configRevision"], "before": runtime_values(before),
                                "after": runtime_values(value["definition"])}
                            stored["runtimeOperationId"] = operation_id
                        stored.update(canonical=item, config=copy.deepcopy(value["definition"]), configRevision=item["configRevision"])
                        aggregate(stored, self.root)["profileId"] = self.profile_id
                    self._save(db, kind, item["id"], stored)
                if kind == "trigger":
                    db.execute("DELETE FROM creator_links WHERE triggerId=?", (item["id"],))
                    if item["creatorSelector"]["mode"] == "ids":
                        for cid in item["creatorSelector"]["ids"]:
                            self.creators.bind_link(cid, item["id"], item.get("targetJobId", item.get("target", {}).get("jobId", "action:" + item.get("target", {}).get("actionRef", {}).get("id", ""))))
                db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)", (kind, item["id"], fc.canonical_json(actor)))
                return {"object": item, **({"runtimeAudit": runtime_audit} if runtime_audit else {})}, [object_ref(kind, item)], False
            return self._operation(db, actor, "create" if object_id is None else "update", value["requestKey"],
                                   {"kind": kind, "objectId": object_id, **value}, kind, mutate)

    def control(self, kind, object_id, body, *, actor, capability=None):
        if kind == "action":
            return self._action_control(object_id, body, actor=actor)
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
        from .final_runtime.ledger import admission_for_cause
        previous = admission_for_cause(state, cause or {"type": "manual"})
        if previous is not None:
            prior_run = next((r for r in [*state['runs'].values(),
                *(t['run'] for t in state.get('expiredRuns', {}).values())]
                if r['admissionId'] == previous['admissionId']), None)
            if prior_run is not None:
                return {'runId': prior_run['runId'], 'admissionId': previous['admissionId'],
                        'configRevision': prior_run['configRevision']}
        active = [run for run in state["runs"].values() if run["state"] in ACTIVE]
        overlap = cfg["overlap"]
        if overlap["mode"] == "forbid" and active:
            admission = self.final.ledger.admit(job, self.root, plan={}, cause=cause or {"type": "manual"},
                authority=actor, state="skipped", reason="overlap", operation_id=operation_id)
            self._save(db, "job", item["id"], job)
            return {"admissionId": admission["admissionId"], "configRevision": item["configRevision"], "skipped": True}
        if len(active) >= overlap["maxActive"] + overlap["maxPending"]:
            error("capacity_exceeded", "Job pending capacity reached")
        if cfg.get("runLimit") and len(state["runs"]) + len(state.get('expiredRuns', {})) >= cfg["runLimit"]["maxRuns"]:
            error("capacity_exceeded", "Job Run limit reached")
        rerun = value.get("rerunOf")
        if rerun:
            if rerun in state.get('expiredRuns', {}):
                error('history_expired', 'Original Run history expired; operation identity is retained')
            self.final.ledger.run(job, self.root, rerun)
        frozen = {"steps": copy.deepcopy(cfg["steps"]), "actions": {a["id"]: a for a in actions if a.get("definition")},
                  "stepActions": {step["stepId"]: copy.deepcopy(actions[index]) for index, step in enumerate(cfg["steps"])},
                  "input": input_value, "event": copy.deepcopy(value.get("event", {})),
                  "recovery": copy.deepcopy(cfg["recovery"]),
                  "authority": copy.deepcopy(actor), "configRevision": item["configRevision"]}
        if cfg.get("deliveryRuntime"):
            frozen["deliveryRuntime"] = copy.deepcopy(cfg["deliveryRuntime"])
        for step in cfg["steps"]:
            if any(binding["from"] == "step" for binding in step["arguments"].values()):
                continue  # Dynamic output mapping is checked at its Step boundary.
            try:
                arguments = resolve_bindings(step["arguments"], input_value, frozen["event"], {})
            except ValueError:
                error("invalid_schema", "Step input mapping cannot be resolved")
            action = frozen_action(frozen, step["stepId"], step["actionRef"]["id"])
            if not jsonschema.Draft202012Validator(action["inputSchema"]).is_valid(arguments):
                error("invalid_schema", "Step input does not match Action schema")
            if action["execution"]["operation"] in {"session-message", "session-broadcast", "assign"}:
                self._session_source(arguments, actor)
        admission = self.final.ledger.admit(job, self.root, plan=frozen, cause=cause or {"type": "manual"},
                                          authority=actor, operation_id=operation_id, rerun_of=rerun,
                                          authorization_revision=max(1, self.auth.permissions_status()["revision"]))
        run = next(r for r in state["runs"].values() if r["admissionId"] == admission["admissionId"])
        from packages.scheduler.json_handoff import freeze_dispatch
        freeze_dispatch(self, db, job, run['runId'], value.get('event'))
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
            if run_id in job['finalRuntime'].get('expiredRuns', {}):
                error('history_expired', 'Original Run history expired; operation identity is retained')
        for job in self._all(db, 'runtime_archive'):
            if run_id in job.get('finalRuntime', {}).get('expiredRuns', {}):
                error('history_expired', 'Original Run history expired; operation identity is retained')
        raise KeyError(run_id)

    def get_run(self, run_id, *, actor):
        self._permit(actor, "job", "read")
        with self.transaction(readonly=True) as db:
            job, run = self._find_run(db, run_id)
            return self._run_notification_projection(job, run)

    def _run_notification_projection(self, job, run):
        fields = ("outboxId", "transitionSeq", "recipientId", "status", "targetRef", "targetRevision",
                  "payloadDigest", "summary", "receipt", "notificationRuntime", "runtimeOperationId")
        rows = [{key: copy.deepcopy(row[key]) for key in fields if key in row}
                for row in aggregate(job, self.root)["outbox"].values()
                if row["runId"] == run["runId"] and row["channel"] == "session"]
        plan = aggregate(job, self.root)["plans"].get(run["runId"], {})
        delivery = {}
        if plan.get("frozen", {}).get("deliveryRuntime"):
            delivery = {"declaration": copy.deepcopy(plan["frozen"]["deliveryRuntime"]), "steps": {}}
            for step_id, reserved in plan.get("runtimeDelivery", {}).items():
                projected = {key: copy.deepcopy(value) for key, value in reserved.items() if key not in {"arguments", "children"}}
                projected["children"] = [{key: copy.deepcopy(value) for key, value in child.items() if key != "arguments"}
                                         for child in reserved.get("children", [])]
                from packages.jobs.session_delivery_runtime import bound_attempt
                eligible = bool(bound_attempt(aggregate(job, self.root), run['runId'])) and not plan.get('runtimeHold')
                projected['pendingFires'] = [{**copy.deepcopy(child['backlog']), 'status': child['status'],
                    'automaticDelivery': child['status'] == 'known-no-attempt' and eligible} for child in reserved.get('children', [])
                    if child.get('operation') == 'channel' and child.get('status') != 'accepted' and child.get('backlog')]
                if plan.get('runtimeHold'):
                    projected['holdReason'] = plan['runtimeHold']
                delivery["steps"][step_id] = projected
        return {**copy.deepcopy(run), **({"notifications": rows} if rows else {}),
                **({"deliveryRuntime": delivery} if delivery else {})}

    def _process_log_records(self, run_id):
        """One trusted live/archive locator for both authorized log queries."""
        with self.transaction(readonly=True) as db:
            job = next((j for kind in ("job", "runtime_archive") for j in self._all(db, kind)
                        if run_id in j.get("finalRuntime", {}).get("runs", {})
                        or run_id in j.get('finalRuntime', {}).get('expiredRuns', {})), None)
            if job is None:
                raise KeyError(run_id)
            state = job["finalRuntime"]
            tombstone = state.get('expiredRuns', {}).get(run_id)
            if tombstone:
                return job['jobId'], copy.deepcopy(tombstone['logRecords'])
            records = []
            for attempt in state["attempts"].values():
                if attempt["runId"] != run_id:
                    continue
                rows = [row for row in state["effects"] if row["attemptId"] == attempt["attemptId"]]
                rows += [row for row in state["effectAdmissions"].values() if row["attemptId"] == attempt["attemptId"]]
                records.append({"attempt": copy.deepcopy(attempt), "rows": copy.deepcopy(rows),
                    **({'logExpired': copy.deepcopy(state['expiredLogs'][attempt['attemptId']])}
                       if attempt['attemptId'] in state.get('expiredLogs', {}) else {})})
            return job["jobId"], sorted(records, key=lambda r: (r["attempt"]["stepId"], r["attempt"]["generation"], r["attempt"]["attemptId"]))

    async def process_log_references(self, run_id, *, actor, cursor=None, limit=100):
        """Discover ORIGINAL references, including pending and archived output."""
        from packages.jobs.process_logs import reference
        self._permit(actor, "job", "read")
        if type(limit) is not int or not 1 <= limit <= 100:
            error("invalid_schema", "Log reference limit must be 1..100")
        job_id, records = self._process_log_records(run_id)
        page = self._page(records, "process-logs:" + run_id, cursor, limit, actor)
        items = []
        adapter = self._external_adapter(job_id)
        for record in page["items"]:
            attempt, rows = record["attempt"], record["rows"]
            item = {"attemptId": attempt["attemptId"], "status": "unavailable", "logRef": None, "href": None}
            if len(rows) == 1:
                row = rows[0]
                key = row["effectKey"]
                item["effectKey"] = copy.deepcopy(key)
                ref = row.get("receipt", {}).get("receipt", {}).get("logRef")
                pending = attempt["state"] in {"running", "uncertain"}
                # An admitted execution may have a persisted reference before
                # its pending receipt returns to the canonical aggregate.
                if ref is None and "receipt" not in row:
                    lookup = getattr(adapter, "lookup_execution", None)
                    if lookup is not None:
                        try:
                            doc = await lookup(attempt=attempt, effect_key=key)
                            ref = doc.get("logRef")
                            pending = doc.get("result") is None
                        except (OSError, ValueError, RuntimeError):
                            item["reason"] = "original_execution_unavailable"
                if ref is not None:
                    from packages.jobs.independent_executor import effect_id
                    expected = reference({"jobId": job_id, "effectId": effect_id(key), "effectKey": key, "attempt": attempt})
                    if ref == expected:
                        item.update(status="pending" if pending else "available", logRef=copy.deepcopy(ref),
                                    href=f"/api/job-runs/{run_id}/attempts/{attempt['attemptId']}/log")
                        if record.get('logExpired'):
                            item.update(status='expired', reason='log_expired')
                    else:
                        item["reason"] = "original_reference_inconsistent"
            if item["status"] == "unavailable":
                item.setdefault("reason", "original_log_reference_unavailable")
            items.append(item)
        self._permit(actor, "job", "read")
        return {"items": items, "nextCursor": page["nextCursor"]}

    async def read_process_log(self, run_id, attempt_id, *, offset=0, limit=65536, actor):
        """Read only an original live/archived Attempt under existing grants."""
        from packages.jobs.process_logs import bounds
        bounds(offset, limit)
        self._permit(actor, "job", "read")
        job_id, records = self._process_log_records(run_id)
        target = next((r for r in records if r["attempt"]["attemptId"] == attempt_id), None)
        if target is None:
            raise KeyError(attempt_id)
        attempt, rows = target["attempt"], target["rows"]
        if len(rows) != 1:
            error("reference_conflict", "Original process effect reference unavailable")
        key = rows[0]["effectKey"]
        adapter = self._external_adapter(job_id)
        reader = getattr(adapter, "read_log", None)
        if reader is None:
            error("provider_unavailable", "Original executor log reader unavailable")
        try:
            result = await reader(attempt=attempt, effect_key=key, offset=offset, limit=limit)
        except fc.ContractError:
            raise
        except (OSError, ValueError, RuntimeError):
            error("provider_unavailable", "Original process log cannot be read; execution is not retried")
        # Permission may have been revoked while the external read awaited.
        self._permit(actor, "job", "read")
        return result

    def _admission_order(self, db, job, rows):
        """Read original receipt insertion order; random IDs never imply age.

        Operation rows and the Run plan are committed by one owner transaction.
        Updates preserve their rowids, and retention keeps these same receipts.
        No new sequence, metadata writer or ledger is introduced here.
        """
        state = aggregate(job, self.root)
        operation_ids = {rid: state.get('plans', {}).get(rid, {}).get('operationId')
                         for rid in state['runs']}
        ids = list({identity for identity in operation_ids.values() if identity})
        receipts = {}
        if ids:
            for ordinal, identity, raw in db.execute(
                    'SELECT rowid,id,document FROM final_operations WHERE id IN '
                    '(SELECT value FROM json_each(?))', (fc.canonical_json(ids),)):
                record = json.loads(raw)
                if record.get('kind') == 'job' and record.get('operation') in {'create', 'run-again', 'clone-and-run'}:
                    receipts[identity] = (ordinal, record.get('response', {}).get('runId'))
        ordinals = {}
        for rid, identity in operation_ids.items():
            original = receipts.get(identity)
            if original is not None and original[1] == rid:
                ordinals[rid] = original[0]
        if all(row['runId'] in ordinals for row in rows):
            return sorted(rows, key=lambda row: ordinals[row['runId']]), True
        # Historical missing receipts cannot prove which equal-time admission
        # was newest. Stable listing is still possible; callers must preserve
        # unknown when choosing a latest state from an ambiguous timestamp.
        return sorted(rows, key=lambda row: (row['createdAt'], row['runId'])), False

    def runs(self, job_id, *, actor, cursor=None, limit=100):
        self._permit(actor, "job", "read")
        with self.transaction(readonly=True) as db:
            job = next((item for kind in ("job", "runtime_archive") for item in self._all(db, kind)
                        if item["jobId"] == job_id), None)
            if job is None:
                raise KeyError(job_id)
            rows = [self._run_notification_projection(job, row) for row in self.final.runs(job_id)]
            rows, _ = self._admission_order(db, job, rows)
        return self._page(rows, "runs:" + job_id, cursor, limit, actor)

    @staticmethod
    def _qq_manual_retry_proven(state, frozen, step, prior):
        """Only an original trusted QQ pre-send receipt replaces cleanup proof."""
        from packages.actions.qq_channel_provider import is_pre_send_phase
        try:
            action = frozen_action(frozen, step["stepId"], step["actionRef"]["id"])
            config = action["definition"]
            if (action["id"] != "product_channel" or config.get("system") is not True
                    or config.get("execution") != {"operation": "channel", "config": {}}
                    or config.get("providerRef") != {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.v2"), "generation": 1}
                    or step["actionRef"] != {"id": action["id"], "revision": action["revision"], "digest": fc.digest(config)}
                    or action["digest"] != fc.digest(config)
                    or any(binding.get("from") != "literal" for binding in step["arguments"].values())):
                return False
            arguments = {name: binding["value"] for name, binding in step["arguments"].items()}
            wire = {"target_type": "private" if arguments["targetType"] == "user" else "group",
                    "target_id": arguments["targetId"], "text": arguments["text"]}
            if "botUin" in arguments:
                wire["bot_uin"] = arguments["botUin"]
            effect = prior[-1]
            receipt = effect["receipt"]
            proof = receipt["qqNoSend"]
            attempt = state["attempts"][effect["attemptId"]]
            key = {"runId": attempt["runId"], "stepId": step["stepId"], "targetId": action["id"], "operationOrdinal": 0}
            return (effect["outcome"] == receipt["outcome"] == "rejected" and attempt["state"] == "failed"
                    and attempt["stepId"] == step["stepId"]
                    and state["steps"][attempt["runId"]][step["stepId"]]["attemptCount"] == attempt["generation"]
                    and proof["attempt"] == {field: attempt[field] for field in (
                        "attemptId", "runId", "stepId", "ownerEpoch", "generation", "leaseId")}
                    and effect["effectKey"] == receipt["effectKey"] == proof["effectKey"] == key
                    and proof["arguments"] == arguments
                    and receipt["receipt"].get("ok") is False
                    and proof["phase"] == receipt["receipt"].get("qqSendPhase")
                    and is_pre_send_phase(proof["phase"], wire))
        except (KeyError, TypeError, ValueError, IndexError):
            return False

    def _retry_boundary(self, job, run_id, step_id=None, *, manual=False):
        """Shared manual safety checks; automatic policy adds stricter gates."""
        state = aggregate(job, self.root)
        failed = [step for step in state["steps"][run_id].values() if step["state"] == "failed"]
        if step_id and (not failed or step_id != failed[0]["stepId"]):
            error("not_retryable", "Retry must resume the failed boundary")
        if not failed or run_id not in state["plans"]:
            error("not_retryable", "Frozen snapshot unavailable")
        if any(e["outcome"] in {"unknown", "partial"} for e in state["effects"] if e["effectKey"]["runId"] == run_id):
            error("reconciliation_required", "Unknown Effect cannot be retried")
        frozen = state["plans"][run_id]["frozen"]
        failed_id = failed[0]["stepId"]
        step = next(s for s in frozen["steps"] if s["stepId"] == failed_id)
        policy = frozen_action(frozen, step["stepId"], step["actionRef"]["id"])["retry"]
        prior = [e for e in state["effects"] if e["effectKey"]["runId"] == run_id and e["effectKey"]["stepId"] == failed_id]
        if any(e["outcome"] == "succeeded" for e in prior):
            error("not_retryable", "Successful effect cannot be repeated")
        qq_proven = manual and prior and self._qq_manual_retry_proven(state, frozen, step, prior)
        if prior and (not (policy["idempotent"] or policy["sameKeySupported"]) or
                      (policy["requiresProof"] and not prior[-1]["receipt"].get("cleanupProof", {}).get("confirmed") and not qq_proven)):
            error("not_retryable", "Effect lacks retry/cleanup proof")
        return step, policy, prior

    def control_run(self, run_id, body, *, actor):
        return self._control_run(run_id, body, actor=actor, capability='execute')

    def cancel_legacy_run(self, run_id, body, *, actor):
        """Preserve the retained cancel entry's original lifecycle grant.

        The trusted compatibility adapter supplies an already reconciled Run.
        This port cannot resume/retry/admit work or borrow an execute grant.
        """
        if set(body) != {'expectedStateRevision', 'requestKey'}:
            error('invalid_schema', 'Legacy cancellation only accepts its original CAS/key')
        return self._control_run(run_id, {**body, 'operation': 'cancel'}, actor=actor, capability='lifecycle')

    def _control_run(self, run_id, body, *, actor, capability):
        self._permit(actor, "job", capability)
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
                    self._retry_boundary(job, run_id, body.get("stepId"), manual=True)
                    state["plans"][run_id].pop("autoRecovery", None)
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

    def _command_target(self, job, target):
        from packages.actions.command_execution import OPERATIONS
        state = aggregate(job, self.root)
        frozen = state["plans"][target["attempt"]["runId"]]["frozen"]
        return frozen_action(frozen, target["effectKey"]["stepId"], target["effectKey"]["targetId"])["execution"]["operation"] in OPERATIONS

    async def control_run_postcommit(self, run_id, body, *, actor):
        self._permit(actor, "job", "execute")
        if body["operation"] == "cancel":
            # Observe a previous asynchronous unknown before admitting an
            # explicit new recheck. This query cannot perform cleanup itself.
            await self.recover_external_receipts()
        response = self.control_run(run_id, body, actor=actor)
        if body["operation"] != "cancel" or response.get("replayed"):
            return response
        return await self._cancel_postcommit(run_id, response, actor=actor)

    async def _cancel_postcommit(self, run_id, response, *, actor, capability='execute'):
        """Finish the original cancel intent after a compatibility transaction.

        The caller already owns the persisted Run cancellation receipt. No new
        admission, resource identity, retry or ledger is allocated here.
        """
        if capability not in {'execute', 'lifecycle'}:
            error('authorization_denied', 'Invalid cancellation capability')
        self._permit(actor, 'job', capability, execution=True)
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
            if self._command_target(job, target):
                # The original local owner observes cancelling at its next
                # boundary and supplies proof after terminating its own tree.
                # Do not send this identity to the independent process store.
                failure = "command_cleanup_pending"
                continue
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

    async def invoke_action(self, action_id, body, *, actor, source=None):
        from packages.actions.invocation import invoke
        return await invoke(self, action_id, body, actor=actor, source=source)

    def action_job_references(self, action_id, *, actor, cursor=None, limit=100):
        from packages.jobs.action_references import references
        return references(self, action_id, actor=actor, cursor=cursor, limit=limit)

    def preview_action_references(self, body, *, actor):
        from packages.jobs.action_references import preview
        return preview(self, body, actor=actor)

    def apply_action_references(self, body, *, actor):
        from packages.jobs.action_references import apply
        return apply(self, body, actor=actor)

    def job_provenance(self, job_id, *, actor):
        self._permit(actor, "job", "read")
        with self.transaction(readonly=True) as db:
            return copy.deepcopy(self._get(db, "job", job_id).get("provenance"))

    def legacy_background_view(self, job_id, *, actor):
        """Project a canonical process Job through the retained old reader.

        This is a compatibility read/control adapter only. The canonical Job,
        Run, Attempt, effect and process owner remain authoritative; no legacy
        registry row or second receipt is created.
        """
        self._permit(actor, "job", "read")
        item = self.get("job", job_id, actor=actor)
        cfg = definition("job", item)
        if len(cfg.get("steps", [])) != 1:
            error("capability_unsupported", "Complex process Job has no lossless legacy projection")
        step = cfg["steps"][0]
        action = self.get("action", step["actionRef"]["id"], actor=actor)
        if action.get("execution", {}).get("operation") != "process":
            error("capability_unsupported", "Job is not a canonical background process")
        args = {key: copy.deepcopy(value.get("value")) for key, value in step.get("arguments", {}).items()
                if isinstance(value, dict) and value.get("from") == "literal"}
        runs = self.runs(job_id, actor=actor).get("items", [])
        # A page is bounded and stored maps sort random IDs. Select the latest
        # admission from the original aggregate, including beyond page one.
        with self.transaction(readonly=True) as db:
            stored = self._get(db, 'job', job_id)
            ordered, proven = self._admission_order(db, stored, list(aggregate(stored, self.root)['runs'].values()))
        latest = ordered[-1] if ordered else None
        status = item.get("state", "draft")
        if latest:
            ambiguous = not proven and sum(row['createdAt'] == latest['createdAt'] for row in ordered) > 1
            status = 'uncertain' if ambiguous else latest.get('state', status)
        return {"jobId": job_id, "kind": "background-process", "executionOwner": "final-service",
                "status": status, "argv": args.get("argv", []), "cwd": args.get("cwd"),
                "hideWindow": args.get("hideWindow", True), "name": item.get("name"),
                "description": item.get("description", ""), "targetSessionId": args.get("sessionId"),
                "creatorSessionId": None, "createdAt": item.get("createdAt"), "updatedAt": item.get("updatedAt"),
                "runs": runs, "history": copy.deepcopy(runs), "notificationState": "canonical-outbox"}

    def legacy_background_cancel(self, job_id, *, actor, request_key):
        self._permit(actor, "job", "execute")
        view = self.legacy_background_view(job_id, actor=actor)
        active = next((run for run in reversed(view.get("runs", []))
                       if run.get("state") in {"queued", "running", "retry_wait", "cancelling", "uncertain"}), None)
        if active is None:
            return view
        result = self.control_run(active["runId"], {"operation": "cancel",
            "expectedStateRevision": active["stateRevision"], "requestKey": request_key}, actor=actor)
        return {**self.legacy_background_view(job_id, actor=actor), "cancelResult": result}

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

    def publish_action(self, action_id, value, *, enabled=True):
        """Trusted product/provider publication into the canonical directory.

        Product registration is intentionally separate from the user draft
        route.  It may register protected system Actions, but never rewrites a
        published definition or an existing digest.
        """
        value = fc.validate_definition("action", value)
        with self.transaction() as db:
            self._ensure_action_schema(db)
            try:
                old = self._get(db, "action", action_id)
            except KeyError:
                old = None
            if old is not None:
                if definition("action", old) != value:
                    error("reference_conflict", "Published Action content is immutable")
                digest = fc.digest(value)
                if db.execute("SELECT 1 FROM final_action_versions WHERE actionId=? AND revision=?",
                              (action_id, old["revision"])).fetchone() is None:
                    db.execute("INSERT INTO final_action_versions(actionId,revision,digest,state,definition,createdAt,publishedAt) VALUES(?,?,?,?,?,?,?)",
                               (action_id, old["revision"], digest, "published", fc.canonical_json(value),
                                old["createdAt"], old.get("updatedAt", now())))
                    db.execute("INSERT OR REPLACE INTO final_action_lifecycle(actionId,revision,enabled,state,stateRevision,updatedAt) VALUES(?,?,?,?,?,?)",
                               (action_id, old["revision"], int(old.get("enabled", True)), old["state"], old["stateRevision"], now()))
                return old
            item = self._envelope("action", value, {"type": "system", "id": "product"}, object_id=action_id, state="published")
            item["enabled"] = enabled
            item = fc.validate_object("action", item)
            digest = fc.digest(value)
            db.execute("INSERT INTO final_action_versions(actionId,revision,digest,state,definition,createdAt,publishedAt) VALUES(?,?,?,?,?,?,?)",
                       (action_id, item["revision"], digest, "published", fc.canonical_json(value), item["createdAt"], item["updatedAt"]))
            db.execute("INSERT INTO final_action_lifecycle(actionId,revision,enabled,state,stateRevision,updatedAt) VALUES(?,?,?,?,?,?)",
                       (action_id, item["revision"], int(enabled), "published", item["stateRevision"], now()))
            self._save(db, "action", action_id, item)
            db.execute("INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)",
                       ("action", action_id, fc.canonical_json({"type": "system", "id": "product"})))
            return item

    def _install_product_actions(self):
        from packages.actions.command_execution import OPERATIONS, product_definition
        for operation in sorted(OPERATIONS):
            self.publish_action(operation, product_definition(operation), enabled=False)
        from packages.actions.job_creation import product_definition as job_creation_definition
        self.publish_action("job.create", job_creation_definition(), enabled=False)
        from packages.automation.quota_native import ACTION, action_definition
        self.publish_action(ACTION, action_definition())
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
                # Publication only. The original owned runner supplies logRef;
                # custom provider receipts are not decorated by this schema.
                logged = copy.deepcopy(value)
                logged["providerRef"] = {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.process-logs.v1"), "generation": 3}
                logged["outputSchema"].setdefault("properties", {})["logRef"] = {"type": "object"}
                self.publish_action("product_process_logs_v1", logged)

        # Additive publications: old releases and frozen Runs retain their
        # original schemas/digests. These remain ordinary typed Actions.
        from packages.core.worker import SOURCE_TYPES
        for op in ("session-message", "session-broadcast", "assign"):
            fields = copy.deepcopy(schemas["session-message" if op == "assign" else op])
            if op == "session-broadcast":
                fields["sessionIds"].pop("uniqueItems", None)  # worker fanout keeps first occurrence
            fields.update(source={"enum": sorted(SOURCE_TYPES)}, sourceSessionId=string)
            if op == "assign":
                fields["taskId"] = string
            self.publish_action("product_" + op.replace("-", "_") + "_v2", {
                "name": op, "inputSchema": {"type": "object", "properties": fields,
                    "required": ["sessionIds" if op == "session-broadcast" else "sessionId", "text"], "additionalProperties": False},
                "outputSchema": {"type": "object"},
                "providerRef": {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.session.v2"), "generation": 2},
                "execution": {"operation": op, "config": {}}, "effectClass": "write_local",
                "requiredCapabilities": [], "completionContracts": [{"id": "durable-queue-accepted", "schema": {"type": "object"}, "terminalProofSchema": {"type": "object"}}],
                "defaultCompletion": "durable-queue-accepted", "cancel": {"supported": False}, "reconcile": {"supported": False},
                "retry": {"idempotent": False, "sameKeySupported": True, "requiresProof": True}, "system": True})

        # New opt-in releases only. Never alter source-aware v2 publications
        # or Actions already embedded in a frozen Run.
        for op in ("assign", "session-message", "session-broadcast"):
            with self.transaction(readonly=True) as db:
                runtime_action = definition("action", self._get(db, "action", "product_" + op.replace("-", "_") + "_v2"))
            runtime_action["runtimeBeforeAcceptance"] = {
                "owner": "delivery-manager", "fields": ["sessionIds" if op == "session-broadcast" else "sessionId", "text"],
                "phase": "before-queue-acceptance", "fence": "per-target-attempt-effect", "unknownPolicy": "query-original"}
            runtime_action["providerRef"] = {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.session-runtime.v1"), "generation": 3}
            self.publish_action("product_" + op.replace("-", "_") + "_runtime_v1", runtime_action)

        # QQ runtime is additive and opt-in. The old product_channel release
        # remains frozen; this release only permits the existing trusted QQ
        # producer proof to hold one unfinished Attempt before acceptance.
        if getattr(self.executor, "channel_provider", None) is not None:
            with self.transaction(readonly=True) as db:
                channel_runtime = definition("action", self._get(db, "action", "product_channel"))
            channel_runtime["runtimeBeforeAcceptance"] = {
                "owner": "delivery-manager", "fields": ["targetType", "targetId", "botUin", "text"],
                "phase": "before-provider-acceptance", "fence": "per-target-attempt-effect",
                "unknownPolicy": "query-original"}
            channel_runtime['inputSchema']['properties']['botUin']['type'] = ['string', 'null']
            channel_runtime["providerRef"] = {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.qq-runtime.v1"), "generation": 2}
            self.publish_action("product_channel_runtime_v1", channel_runtime)

        self.publish_action("product_resume_legal_running_v1", {
            "name": "Resume Illegal / pending recovery Sessions",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {"type": "object"},
            "providerRef": {"id": "pan.typed", "releaseDigest": fc.digest("pan.typed.resume-legal-running.v1"), "generation": 1},
            "execution": {"operation": "resume-legal-running.v1", "config": {}}, "effectClass": "write_local",
            "requiredCapabilities": [],
            "completionContracts": [{"id": "durable-queue-accepted", "schema": {"type": "object"}, "terminalProofSchema": {"type": "object"}}],
            "defaultCompletion": "durable-queue-accepted", "cancel": {"supported": False}, "reconcile": {"supported": False},
            "retry": {"idempotent": False, "sameKeySupported": True, "requiresProof": True}, "system": True})

    @staticmethod
    def _session_source(arguments, authority):
        """Source is message context, never a grant or a transport identity.

        The current delegated transport authenticates an Agent capability, not
        a particular Session. It cannot assert a Session or a user identity.
        A Bearer-authenticated user may select existing agent message context;
        worker.assign/send_session still validate that context and queue gates.
        """
        source = arguments.get("source", "automation")
        sid = arguments.get("sourceSessionId")
        if authority.get("type") != "user" and (source != "automation" or sid is not None):
            error("authorization_denied", "Transport has no authenticated Session source identity")
        return {**arguments, "source": source}

    def actions(self, *, actor):
        self._permit(actor, "action", "read")
        with self.transaction(readonly=True) as db:
            items = self._all(db, "action")
            try:
                self._ensure_action_schema(db)
            except sqlite3.OperationalError:
                # An old profile can still expose the logical heads while it
                # waits for its next owner-fenced write to create side tables.
                pass
            versions = {}
            references = {}
            draft_references = {}
            for item in items:
                try:
                    rows = db.execute("SELECT revision,digest,state FROM final_action_versions WHERE actionId=? ORDER BY revision",
                                      (item["id"],)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                versions[item["id"]] = {
                    "count": len(rows),
                    "latest": next(({"id": item["id"], "revision": int(row[0]), "digest": row[1]}
                                     for row in reversed(rows) if row[2] == "published"), None),
                }
                draft_references[item["id"]] = object_ref("action", item)
                references[item["id"]] = versions[item["id"]]["latest"] or draft_references[item["id"]]
            return {"items": items, "nextCursor": None,
                    "references": references,
                    "draftReferences": draft_references,
                    "versions": versions}

    def legacy_action_definition(self, body, *, actor):
        """Map a legacy quick-create draft to a canonical Action definition.

        Read-only boundary adapter: it reuses the approved adoption mapping
        (``group_migration.command_definition``) so the designed form and the
        migration cannot drift. Nothing is persisted, published or executed.
        """
        self._permit(actor, "action", "read")
        if not isinstance(body, dict) or "draft" not in body or set(body) - {"draft", "templateRef"}:
            error("invalid_schema", "Definition preview requires a draft")
        draft = body["draft"]
        if not isinstance(draft, dict):
            error("invalid_schema", "Draft must be an object")
        template_ref = body.get("templateRef")
        execution = draft.get("execution")
        if (template_ref is None and isinstance(execution, dict)
                and execution.get("kind") == "job_template"):
            template_ref = self._fixed_template_ref(execution)
        candidate = dict(draft)
        # The legacy validator insists on a stable id; the id never enters the
        # definition, so a fixed placeholder keeps the preview side-effect free.
        candidate.setdefault("id", "definition_preview")
        from packages.actions.group_migration import command_definition
        try:
            return {"definition": command_definition(candidate, template_ref=template_ref)}
        except fc.ContractError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise fc.ContractError("invalid_schema", str(exc)) from exc

    def _fixed_template_ref(self, execution):
        template_id = execution.get("templateId")
        revision = execution.get("templateRevision")
        if not isinstance(template_id, str) or not template_id or type(revision) is not int or revision < 1:
            error("invalid_schema", "Template ID and revision are required")
        with self.transaction(readonly=True) as db:
            item = self._get(db, "template", template_id)
        if item["revision"] != revision or item["state"] != "published":
            error("reference_conflict", "Fixed Template must be a published revision")
        return object_ref("template", item)

    def action_versions(self, action_id, *, actor):
        self._permit(actor, "action", "read")
        with self.transaction(readonly=True) as db:
            try:
                rows = db.execute("SELECT revision,digest,state,definition,createdAt,publishedAt FROM final_action_versions WHERE actionId=? ORDER BY revision",
                                  (action_id,)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                # Product/legacy rows created before P1 still have a stable
                # digest and are represented as one published version.
                item = self._get(db, "action", action_id)
                digest = fc.digest(definition("action", item))
                return {"actionId": action_id, "items": [{"ref": {"id": action_id, "revision": item["revision"], "digest": digest},
                    "revision": item["revision"], "digest": digest, "state": item["state"],
                    "enabled": item.get("enabled", False), "definition": definition("action", item)}]}
            items = []
            for row in rows:
                version = self._action_version_record(db, action_id, int(row[0]), digest=row[1])
                items.append({"ref": {"id": action_id, "revision": int(row[0]), "digest": row[1]},
                              "revision": int(row[0]), "digest": row[1], "state": version["state"],
                              "enabled": version["enabled"], "definition": version["definition"],
                              "createdAt": row[4], "publishedAt": row[5]})
            return {"actionId": action_id, "items": items}

    def action_ref(self, action_id, *, actor, revision=None, digest=None, latest=False):
        self._permit(actor, "action", "read")
        with self.transaction(readonly=True) as db:
            head = self._get(db, "action", action_id)
            resolving_latest = latest or revision is None
            if resolving_latest:
                if head.get("state") == "retired":
                    error("reference_conflict", "Retired Action has no invokable latest version")
                try:
                    row = db.execute("SELECT revision,digest FROM final_action_versions WHERE actionId=? AND state='published' ORDER BY revision DESC LIMIT 1",
                                     (action_id,)).fetchone()
                except sqlite3.OperationalError:
                    row = None
                    if head.get("state") == "published":
                        row = (head["revision"], fc.digest(definition("action", head)))
                if row is None:
                    error("reference_conflict", "No published Action version is available")
                revision, digest = int(row[0]), row[1]
            version = self._action_version_record(db, action_id, revision, digest=digest)
            if resolving_latest and version["state"] != "published":
                error("reference_conflict", "Action version is not published")
            return {"id": action_id, "revision": int(revision), "digest": version["digest"]}

    def register_action_alias(self, legacy_id, action_id, revision, digest, *, source="legacy-file",
                              actor=None, request_key=None):
        actor = actor or {"type": "system", "id": "migration"}
        self._permit(actor, "action", "updateConfig")
        if not isinstance(legacy_id, str) or not legacy_id.strip() or not isinstance(source, str) or not source.strip():
            error("invalid_schema", "legacyId and source are required")
        payload = {"legacyId": legacy_id, "actionId": action_id, "revision": revision, "digest": digest, "source": source}
        with self.transaction() as db:
            self._ensure_action_schema(db)
            def mutate(operation_id):
                version = self._action_version_record(db, action_id, revision, digest=digest)
                if version["state"] == "retired":
                    error("reference_conflict", "Cannot map a legacy ID to a retired Action version")
                owner = principal(actor)
                existing = db.execute("SELECT actionId,revision,digest,source FROM final_action_aliases WHERE legacyId=?",
                                      (legacy_id,)).fetchone()
                if existing is not None:
                    if tuple(existing) != (action_id, revision, digest, source):
                        error("idempotency_conflict", "Legacy Action ID already maps to different content")
                else:
                    db.execute("INSERT INTO final_action_aliases VALUES(?,?,?,?,?,?,?)",
                               (legacy_id, action_id, revision, digest, source, fc.canonical_json(owner), now()))
                result = {"legacyId": legacy_id, "actionRef": {"id": action_id, "revision": revision, "digest": digest},
                          "source": source, "owner": owner}
                return result, [], False
            if request_key is None:
                return mutate(new_id("op_"))[0]
            return self._operation(db, actor, "update", request_key, payload, "action", mutate)

    def action_alias(self, legacy_id, *, actor):
        self._permit(actor, "action", "read")
        with self.transaction(readonly=True) as db:
            row = db.execute("SELECT legacyId,actionId,revision,digest,source,owner,createdAt FROM final_action_aliases WHERE legacyId=?",
                             (legacy_id,)).fetchone()
            if row is None:
                raise KeyError(legacy_id)
            return {"legacyId": row[0], "actionRef": {"id": row[1], "revision": int(row[2]), "digest": row[3]},
                    "source": row[4], "owner": json.loads(row[5]), "createdAt": row[6]}

    def _guard_effect(self, db, job, run_id, frozen, action):
        self._permit(frozen["authority"], "job", "execute", execution=True)
        from packages.scheduler.json_handoff import guard_effect
        guard_effect(self, db, job, run_id)
        if action.get("system"):
            self._permit(frozen["authority"], "action", "manageSystemActions", execution=True)
        # Definition retirement/disable blocks new admission. Already frozen
        # Runs remain valid; pause is checked at each boundary by the driver.
        current = self._action_version_record(db, action["id"], action["revision"], digest=action["digest"], for_ref=True)
        head = self._get(db, "action", action["id"])
        if (not current["enabled"] or current["state"] != "published"
                or head["revision"] == action["revision"] and not head["enabled"]):
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

    @staticmethod
    def _recovery_time(timestamp=None):
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if timestamp else datetime.now(timezone.utc)
        delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000

    @staticmethod
    def _automatic_policy(plan):
        # Old frozen Runs never acquire policy from a later definition edit.
        policy = plan["frozen"].get("recovery", {})
        return policy if policy.get("automatic") and policy.get("retryIf") else None

    def _automatic_due(self, plan):
        if "autoRecovery" not in plan:
            return True
        try:
            deadline = Decimal(fc.check_duration(plan["autoRecovery"]["eligibleAt"], "eligibleAt"))
            return self._recovery_time() >= deadline
        except (fc.ContractError, KeyError, TypeError):
            return False  # Corrupt/missing metadata cannot mean immediately due.

    def _validate_process_document(self, job, target, doc):
        """Only a fenced original host lookup supplies automatic retry proof."""
        from packages.jobs.independent_executor import fence, effect_id
        state = aggregate(job, self.root)
        expected, key = target["attempt"], target["effectKey"]
        current = state["attempts"][expected["attemptId"]]
        plan = state["plans"][current["runId"]]
        frozen = plan["frozen"]
        step = next(s for s in frozen["steps"] if s["stepId"] == current["stepId"])
        action = frozen_action(frozen, step["stepId"], step["actionRef"]["id"])
        executable = {**copy.deepcopy(action["execution"]["config"]), "operation": action["execution"]["operation"],
                      "effect": action["effectClass"], "arguments": resolve_bindings(
                          step["arguments"], frozen["input"], frozen["event"], plan.get("outputs", {}))}
        if step["completion"].get("contract") == "monitoring":
            executable["completion"] = copy.deepcopy(step["completion"])
        if (action["execution"]["operation"] != "process" or action["effectClass"] == "high_risk"
                or target["jobId"] != job["jobId"] or doc.get("jobId") != job["jobId"]
                or fence(current) != fence(expected) or fence(doc.get("attempt", {})) != fence(expected)
                or (key["runId"], key["stepId"]) != (current["runId"], current["stepId"])
                or state["steps"][current["runId"]][current["stepId"]]["attemptCount"] != current["generation"]
                or doc.get("effectKey") != key or doc.get("effectId") != effect_id(key)
                or doc.get("planDigest") != fc.digest(executable) or doc.get("frozenPlan") != executable
                or (doc.get("result") and doc["result"].get("effectKey") != key)):
            error("revision_conflict", "Original process proof differs from frozen Attempt/effect/plan")

    @staticmethod
    def _process_retry_reason(doc):
        result = doc.get("result") or {}
        receipt = result.get("receipt", {})
        if result.get("outcome") != "rejected":
            return None
        if receipt.get("code") == "timeout":
            return "timeout"
        if receipt.get("code") == "monitoring_completion_invalid":
            return "failed"
        if receipt.get('code') in {'orphaned', 'interrupted'}:
            from packages.core.platform.process import ProcessIdentity, IDENTITY_SCHEMA
            identity = doc.get('supervisorIdentity')
            proof = doc.get('supervisorExitProof', {})
            if (type(receipt.get('exitCode')) is not int or receipt['exitCode'] == 0
                    or not FinalService._cleanup_complete(doc)
                    or not isinstance(identity, dict) or identity.get('schema') != IDENTITY_SCHEMA
                    or type(identity.get('pid')) is not int
                    or not isinstance(identity.get('birth'), str) or not identity['birth']
                    or proof.get('identity') != identity):
                return None
            try:
                ProcessIdentity.from_dict(identity)
            except (ValueError, TypeError):
                return None
            return receipt['code']
        if ("code" not in receipt and type(receipt.get("exitCode")) is int
                and receipt["exitCode"] != 0):
            return "failed"
        return None

    @staticmethod
    def _stable_execution_seconds(doc):
        """Elapsed original execution, never coordinator observation time."""
        timing = (doc.get("result") or {}).get("receipt", {}).get("executionTiming")
        if not isinstance(timing, dict) or set(timing) != {
                "startedAt", "endedAt", "startedMonotonicNs", "endedMonotonicNs"}:
            return None
        try:
            for key in ("startedAt", "endedAt"):
                fc.check_timestamp(timing[key], key)
            start, end = (timing[key] for key in ("startedMonotonicNs", "endedMonotonicNs"))
            if type(start) is not int or type(end) is not int or start < 0 or end < start:
                return None
            # Both ticks belong to one original executor invocation. Using its
            # clock interval also prevents UTC adjustments or offline query lag
            # from minting a stable recovery window.
            with localcontext() as context:
                context.prec = len(str(end)) + 10
                return Decimal(end - start) / 1000000000
        except (fc.ContractError, TypeError, ValueError, OverflowError):
            return None

    def _automatic_budget(self, job, rid, step_id):
        """Derive consecutive-window usage from monotonic canonical generation.

        Only a stable rejected Attempt installs a baseline. The small per-Step
        plan marker is a fenced window boundary, never an independent counter.
        """
        state = aggregate(job, self.root)
        plan = state["plans"][rid]
        count = state["steps"][rid][step_id]["attemptCount"]
        windows = plan.get("recoveryWindows", {})
        if not isinstance(windows, dict):
            error("reconciliation_required", "Invalid recovery windows")
        window = windows.get(step_id)
        if window is None:
            return count
        if (not isinstance(window, dict) or set(window) != {"afterAttemptId", "baseGeneration"}
                or not isinstance(window["afterAttemptId"], str)):
            error("reconciliation_required", "Invalid recovery window")
        base = window["baseGeneration"]
        attempt = state["attempts"].get(window["afterAttemptId"])
        if (type(base) is not int or base < 0 or base >= count or attempt is None
                or attempt["runId"] != rid or attempt["stepId"] != step_id
                or attempt["generation"] != base + 1 or attempt["state"] != "failed"):
            error("reconciliation_required", "Recovery window does not match original failed Attempt")
        rows = [e for e in state["effects"] if e["attemptId"] == window["afterAttemptId"]]
        threshold = Decimal(plan["frozen"].get("recovery", {}).get("stableResetSeconds", "0"))
        elapsed = self._stable_execution_seconds({"result": rows[0]["receipt"]}) if len(rows) == 1 else None
        if (threshold <= 0 or elapsed is None or elapsed < threshold
                or rows[0]["outcome"] != "rejected"):
            error("reconciliation_required", "Recovery window lacks original stable execution evidence")
        return count - base

    def _schedule_automatic(self, db, job, rid, doc):
        state = aggregate(job, self.root)
        plan = state["plans"][rid]
        policy = self._automatic_policy(plan)
        if not policy or state["runs"][rid]["state"] != "failed" or rid in state.get("cancellations", {}):
            return False
        step, _, prior = self._retry_boundary(job, rid)
        count = state["steps"][rid][step["stepId"]]["attemptCount"]
        used = self._automatic_budget(job, rid, step["stepId"])
        reason = self._process_retry_reason(doc)
        if (reason not in policy["retryIf"] or not self._cleanup_complete(doc)
                or not prior or prior[-1]["attemptId"] != doc["attempt"]["attemptId"]):
            return False
        reset = Decimal(policy["stableResetSeconds"])
        elapsed = self._stable_execution_seconds(doc) if reset > 0 else None
        if reset > 0 and elapsed is None:
            return False  # Old/malformed receipts cannot assert a stable interval.
        action = frozen_action(plan["frozen"], step["stepId"], step["actionRef"]["id"])
        if job["canonical"]["state"] not in {"enabled", "paused"}:
            return False
        try:
            self._guard_effect(db, job, rid, plan["frozen"], action)
        except fc.ContractError:
            return False
        if reset > 0 and elapsed >= reset:
            plan.setdefault("recoveryWindows", {})[step["stepId"]] = {
                "afterAttemptId": prior[-1]["attemptId"], "baseGeneration": count - 1}
            used = 1  # The just-ended stable failure is first in its new window.
        if used >= policy["maxAttempts"]:
            return False
        delay = Decimal(policy["backoffSeconds"][min(used - 1, len(policy["backoffSeconds"]) - 1)])
        ended = state["attempts"][prior[-1]["attemptId"]]["endedAt"]
        base = self._recovery_time(ended)
        with localcontext() as context:
            context.prec = len(str(base)) + len(str(delay)) + 8
            deadline = format(base + delay, "f")
        plan["autoRecovery"] = {"stepId": step["stepId"], "afterAttemptId": prior[-1]["attemptId"],
                                "eligibleAt": deadline, "reason": reason}
        self.final.ledger.transition_run(job, self.root, rid, "retry_wait", operation="retry")
        self._save(db, "job", job["jobId"], job)
        return True

    async def _automatic_original(self, job_id, rid):
        with self.transaction(readonly=True) as db:
            job = self._get(db, "job", job_id)
            state = aggregate(job, self.root)
            marker = copy.deepcopy(state["plans"][rid].get("autoRecovery"))
            if marker is None:
                return None, None
            if not isinstance(marker, dict):
                return marker, None
            aid = marker.get("afterAttemptId")
            prior = [e for e in state["effects"] if e["attemptId"] == aid]
            if len(prior) != 1 or aid not in state["attempts"]:
                return marker, None
            target = {"jobId": job_id, "attempt": copy.deepcopy(state["attempts"][aid]),
                      "effectKey": copy.deepcopy(prior[0]["effectKey"])}
        try:
            doc = await self._external_adapter(job_id).lookup_execution(
                attempt=target["attempt"], effect_key=target["effectKey"])
            return marker, (target, doc)
        except Exception:
            return marker, None  # No resend or cleanup to obtain evidence.

    def _claim_automatic(self, db, job, rid, marker, original):
        state = aggregate(job, self.root)
        plan = state["plans"][rid]
        if plan.get("autoRecovery") != marker or not self._automatic_due(plan) or original is None:
            return False
        target, doc = original
        self._validate_process_document(job, target, doc)
        step, _, prior = self._retry_boundary(job, rid, marker["stepId"])
        policy = self._automatic_policy(plan)
        used = self._automatic_budget(job, rid, step["stepId"])
        if (not policy or not prior or prior[-1]["attemptId"] != marker["afterAttemptId"]
                or not self._cleanup_complete(doc) or doc["result"] != prior[-1]["receipt"]
                or self._process_retry_reason(doc) != marker["reason"] or marker["reason"] not in policy["retryIf"]):
            return False
        action = frozen_action(plan["frozen"], step["stepId"], step["actionRef"]["id"])
        try:
            if job["canonical"]["state"] != "enabled" or used >= policy["maxAttempts"]:
                error("authorization_denied", "Automatic retry unavailable")
            self._guard_effect(db, job, rid, plan["frozen"], action)
        except fc.ContractError:
            self.final.ledger.start(job, self.root, rid)
            self.final.ledger.transition_run(job, self.root, rid, "failed")
            self._settle(db, job, rid, failure="authorization_denied")
            return False
        return True

    async def dispatch_once(self):
        """Drive WP02 records through WP05, one serial boundary per Run."""
        await self.recover_session_receipts()
        await self.recover_external_receipts()
        async with self._dispatch_lock:
            candidates = await asyncio.to_thread(self._dispatch_candidates)
            await asyncio.gather(*(self._drive_run(job_id, run_id) for job_id, run_id in candidates))

    def _dispatch_candidates(self):
        # This is only a detached candidate snapshot. _drive_run rechecks the
        # original Run under the canonical owner/writer fence before mutation.
        with self.owner.writer(canonical=True), self.transaction(readonly=True) as db:
            candidates = []
            for job in self._all(db, "job"):
                runs = list(aggregate(job, self.root)["runs"].values())
                # Unknown effects still occupy concurrency capacity.
                # Queued cancellation, however, needs no effect slot.
                candidates.extend((job["jobId"], run["runId"]) for run in runs if run["state"] == "cancelling")
                occupied = sum(run["state"] in {"running", "uncertain", "settling"} for run in runs)
                ready = [r for r in runs if r["state"] in {"queued", "retry_wait", "paused_at_boundary"}
                         and self._automatic_due(aggregate(job, self.root)["plans"][r["runId"]])]
                # Canonical JSON sorts map keys, so dictionary iteration
                # after reload is not admission order.
                ready.sort(key=lambda run: (run["createdAt"], run["runId"]))
                slots = max(0, job["config"]["overlap"]["maxActive"] - occupied)
                candidates.extend((job["jobId"], run["runId"]) for run in ready[:slots])
        return candidates

    async def _drive_run(self, job_id, run_id):
        marker, original = await self._automatic_original(job_id, run_id)
        with self.transaction() as db:
            job = self._get(db, "job", job_id)
            state = aggregate(job, self.root)
            run = state["runs"][run_id]
            plan = state["plans"][run_id]
            if run["state"] not in {"queued", "retry_wait", "paused_at_boundary", "cancelling"}:
                return
            from packages.jobs.session_delivery_runtime import unproven_channel_attempt
            if unproven_channel_attempt(state, run_id):
                plan['runtimeHold'] = 'runtime_reservation_unproven'
                self._save(db, 'job', job_id, job)
                return
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
            # Runtime delivery can wait without starting an Attempt or changing
            # the frozen input. Reuse queued/paused state across a cold reopen.
            frozen = plan["frozen"]
            pending_step = next((s for s in frozen["steps"] if state["steps"][run_id][s["stepId"]]["state"] != "completed"), None)
            if pending_step and frozen.get("deliveryRuntime", {}).get("phase") == "before-reservation":
                from packages.jobs.session_delivery_runtime import effective
                try:
                    action = frozen_action(frozen, pending_step["stepId"], pending_step["actionRef"]["id"])
                    self._guard_effect(db, job, run_id, frozen, action)
                    runtime = effective(job, state, run_id, pending_step, action)
                except (fc.ContractError, ValueError):
                    from packages.jobs.session_delivery_runtime import bound_attempt
                    if bound_attempt(state, run_id):
                        plan["runtimeHold"] = "authorization_or_reference_unavailable"
                        self._save(db, "job", job_id, job)
                        return
                    runtime = None  # Existing failed-boundary handling below.
                from packages.scheduler.json_handoff import manual_intent
                if runtime and (runtime["phase"] == "pending" or
                                job["canonical"]["state"] != "enabled" and not manual_intent(self, db, job, run_id)):
                    plan.setdefault("runtimeDelivery", {})[pending_step["stepId"]] = runtime
                    self._save(db, "job", job_id, job)
                    return
            if not plan.get("autoRecovery"):
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
                if plan.get("autoRecovery"):
                    if not self._claim_automatic(db, job, run_id, marker, original):
                        return
                step = next((s for s in frozen["steps"] if state["steps"][run_id][s["stepId"]]["state"] != "completed"), None)
                if step is None:
                    self.final.ledger.transition_run(job, self.root, run_id, "completed")
                    self._settle(db, job, run_id)
                    return
                action = frozen_action(frozen, step["stepId"], step["actionRef"]["id"])
                try:
                    self._guard_effect(db, job, run_id, frozen, action)
                    args = resolve_bindings(step["arguments"], frozen["input"], frozen["event"], plan.get("outputs", {}))
                    from packages.jobs.session_delivery_runtime import effective
                    runtime = effective(job, state, run_id, step, action)
                    if runtime:
                        plan.setdefault("runtimeDelivery", {})[step["stepId"]] = runtime
                        from packages.scheduler.json_handoff import manual_intent
                        if runtime["phase"] == "pending" or job["canonical"]["state"] != "enabled" and not manual_intent(self, db, job, run_id):
                            self.final.ledger.transition_run(job, self.root, run_id, "paused_at_boundary", operation="pause")
                            self._save(db, "job", job_id, job)
                            return
                        args = copy.deepcopy(runtime["arguments"])
                    if not jsonschema.Draft202012Validator(action["inputSchema"]).is_valid(args):
                        error("invalid_schema", "Action input schema rejected mapping")
                    from packages.actions.command_execution import OPERATIONS as COMMAND_OPERATIONS, prepare as prepare_command, argument_defaults
                    if action["execution"]["operation"] in COMMAND_OPERATIONS:
                        from packages.actions.invocation import check_capabilities
                        check_capabilities(action)
                        args = argument_defaults(action["inputSchema"], args)
                        prepare_command(action["execution"]["operation"], action["execution"]["config"], args)
                    if action["execution"]["operation"] in {"session-message", "session-broadcast", "assign"}:
                        args = self._session_source(args, frozen["authority"])
                    if action["execution"]["operation"] == "resume-legal-running.v1":
                        from packages.actions.session_resume import select
                        selections = plan.setdefault("resumeSelections", {})
                        if step["stepId"] not in selections:
                            selections[step["stepId"]] = select()
                        args = {"sessionIds": copy.deepcopy(selections[step["stepId"]])}
                except (fc.ContractError, ValueError) as exc:
                    from packages.jobs.session_delivery_runtime import bound_attempt
                    if bound_attempt(state, run_id):
                        plan["runtimeHold"] = getattr(exc, "code", "invalid_schema")
                        self.final.ledger.transition_run(job, self.root, run_id, "paused_at_boundary", operation="pause")
                        self._save(db, "job", job_id, job)
                        return
                    self.final.ledger.fail_step_without_attempt(job, self.root, run_id, step["stepId"])
                    self.final.ledger.transition_run(job, self.root, run_id, "failed")
                    self._settle(db, job, run_id, failure=getattr(exc, "code", "invalid_schema"))
                    return
                self.final.ledger.start(job, self.root, run_id)
                from packages.jobs.session_delivery_runtime import bound_attempt
                continuation = bound_attempt(state, run_id) if runtime else None
                attempt = continuation or self.final.ledger.begin_attempt(
                    job, self.root, run_id, step["stepId"],
                    owner_epoch=self.owner.token[0] if self.owner.token else 1,
                )
                for row in db.execute("SELECT id,document FROM final_operations").fetchall():
                    op = json.loads(row[1])
                    if op["operation"] == "retry" and op["response"].get("runId") == run_id and op["response"]["status"] == "accepted":
                        op["response"]["result"]["attemptId"] = attempt["attemptId"]
                        db.execute("UPDATE final_operations SET document=? WHERE id=?", (fc.canonical_json(op), row[0]))
                effect_key = runtime["effectKey"] if continuation else self.final.ledger.admit_effect(job, self.root, attempt["attemptId"], action["id"])
                if runtime:
                    runtime.update(effectKey=copy.deepcopy(effect_key), attemptId=attempt["attemptId"],
                        ownerEpoch=attempt["ownerEpoch"], generation=attempt["generation"], leaseId=attempt["leaseId"])
                plan.pop("autoRecovery", None)
                executable = {**copy.deepcopy(action["execution"]["config"]),
                              "operation": action["execution"]["operation"], "effect": action["effectClass"],
                              "arguments": args}
                if step["completion"].get("contract") == "monitoring":
                    executable["completion"] = copy.deepcopy(step["completion"])
                if executable["operation"] == "channel":
                    executable["action"] = {"api": "send_qq", "args": {key: args[key] for key in ("targetType", "targetId", "botUin") if key in args}}
                self._save(db, "job", job_id, job)
            delivery = None
            if runtime:
                from packages.jobs.session_delivery_runtime import SessionDelivery
                delivery = SessionDelivery(self, job_id, run_id, attempt, effect_key)
                executable["_runtimeDelivery"] = delivery
            try:
                executor = self._external_adapter(job_id) if executable["operation"] == "process" and self.executor is self._product_executor else self.executor
                from packages.actions.command_execution import OPERATIONS as COMMAND_OPERATIONS
                from packages.actions.family_execution import OPERATIONS_FAMILY
                if executable["operation"] == "job.create":
                    from packages.actions.job_creation import job_execute
                    receipt = await job_execute(self, job_id, run_id, attempt, effect_key, executable)
                elif executable['operation'] == 'template.instantiate':
                    from packages.actions.invocation import template_job_execute
                    receipt = await template_job_execute(self, job_id, run_id, attempt, effect_key, executable)
                elif executable["operation"] in COMMAND_OPERATIONS:
                    from packages.actions.invocation import job_execute
                    receipt = await job_execute(self, job_id, run_id, attempt, effect_key, executable)
                elif executable['operation'] in OPERATIONS_FAMILY:
                    from packages.actions.invocation import family_job_execute
                    receipt = await family_job_execute(self, job_id, run_id, attempt, effect_key, executable)
                else:
                    receipt = await executor.execute(attempt=attempt, effect_key=effect_key, frozen_plan=executable,
                                                          retry=attempt["generation"] > 1)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                receipt = {"outcome": "unknown", "receipt": {"code": "executor_interrupted"}}
            outcome = receipt.get("outcome", "unknown")
            if (step["completion"].get("contract") == "monitoring" and outcome == "succeeded"
                    and not self._monitoring_proof_valid(receipt, attempt, step["completion"])):
                receipt = copy.deepcopy(receipt)
                receipt["outcome"] = outcome = "unknown"
                receipt.setdefault("receipt", {})["code"] = "monitoring_completion_unproven"
            if delivery:
                with self.transaction() as db:
                    job = self._get(db, "job", job_id)
                    state = aggregate(job, self.root)
                    runtime = state["plans"][run_id]["runtimeDelivery"][step["stepId"]]
                    if delivery.pending:
                        if state["runs"][run_id]["state"] != "cancelling":
                            self.final.ledger.transition_run(job, self.root, run_id, "paused_at_boundary", operation="pause")
                        self._save(db, "job", job_id, job)
                        return  # Same live Attempt, no retry or terminal effect.
                    state["plans"][run_id].pop("pendingSessionAttempt", None)
                    self._save(db, "job", job_id, job)
            if runtime:
                from packages.jobs.session_delivery_runtime import receipt_projection
                receipt["deliveryRuntime"] = receipt_projection(runtime, receipt)
            mapped = {"succeeded": "completed", "rejected": "failed", "unknown": "uncertain", "partial": "uncertain"}.get(outcome, "uncertain")
            with self.transaction() as db:
                job = self._get(db, "job", job_id)
                state = aggregate(job, self.root)
                # A fenced lookup may have already committed this very receipt
                # while the original callback was delayed in transport.
                if state["attempts"][attempt["attemptId"]]["state"] != "running":
                    return
                if (self.executor is self._product_executor and executable["operation"] == "process"
                        and receipt.get("effectKey") == effect_key
                        and ((outcome == "unknown" and receipt.get("receipt", {}).get("code") == "execution_pending")
                             or (outcome in {"succeeded", "rejected"}
                                 and (self._automatic_policy(state["plans"][run_id])
                                      or step["completion"].get("contract") == "monitoring")))):
                    # Known default-host pending is a query reference, not a lost
                    # effect. Keep its original running admission until lookup.
                    state["plans"][run_id]["pendingProcessAttempt"] = attempt["attemptId"]
                    self._save(db, "job", job_id, job)
                    return
                self.final.ledger.finish_attempt(job, self.root, attempt["attemptId"], mapped,
                    expected_epoch=attempt["ownerEpoch"], expected_generation=attempt["generation"])
                self.final.ledger.record_effect(job, self.root, attempt["attemptId"], "partial" if outcome == "partial" else mapped, receipt,
                    expected_attempt=attempt if mapped in {"completed", "failed"} else None)
                state = aggregate(job, self.root)
                if run_id in state.get("cancellations", {}):
                    rows = receipt.get("receipt", {}).get("commands", [])
                    if (executable["operation"] in COMMAND_OPERATIONS
                            or executable['operation'] in OPERATIONS_FAMILY and rows):
                        intent = state["cancellations"][run_id]
                        if (rows and all(row.get("cleanupProof", {}).get("confirmed") is True for row in rows)
                                and all(t["attempt"]["attemptId"] == attempt["attemptId"] for t in intent["targets"])):
                            intent["status"] = "confirmed"
                            self.final.ledger.transition_run(job, self.root, run_id, "cancelled", operation="cancel")
                            self._cancel_status(db, job, run_id, "completed")
                            self._settle(db, job, run_id)
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
        summary = {"runId": run_id, "status": run["state"]}
        state = aggregate(job, self.root)
        frozen = state["plans"][run_id]["frozen"]
        runtime = frozen.get("deliveryRuntime")
        if isinstance(runtime, dict):
            summary["deliveryRuntime"] = copy.deepcopy(runtime)
        summary["creator"] = copy.deepcopy(frozen["authority"])
        if frozen["authority"].get("type") == "agent":
            summary["creatorSessionId"] = frozen["authority"]["id"]
        # Notification summaries are projections of already recorded receipts.
        # They never alter the frozen effect identity or authorize a resend.
        effects = [effect for effect in aggregate(job, self.root)["effects"]
                   if effect.get("attemptId") and
                   aggregate(job, self.root)["attempts"].get(effect["attemptId"], {}).get("runId") == run_id]
        if effects:
            latest = effects[-1].get("receipt") or {}
            receipt = latest.get("receipt") if isinstance(latest, dict) else None
            if isinstance(receipt, dict):
                for field in ("exitCode", "logRef", "logPath"):
                    if field in receipt:
                        summary[field] = copy.deepcopy(receipt[field])
                targets = receipt.get("targets")
                if isinstance(targets, list):
                    summary["targetResults"] = copy.deepcopy(targets)
                    summary["failedTargets"] = [copy.deepcopy(item.get("effectKey", {}).get("targetId"))
                                                 for item in targets
                                                 if item.get("outcome") in {"rejected", "failed", "error"}]
                    summary["failedTargets"] = [item for item in summary["failedTargets"] if item]
        created = self.final.ledger.ensure_outbox(job, self.root, run_id, "canonical:" + run_id, summary)
        from packages.jobs.notification_delivery import NOTIFICATION_RUNTIME
        policy = state["plans"][run_id].get("notification", {})
        for row in created:
            if row["channel"] == "session":
                row["summary"]["targetSessionId"] = row.get("targetRef")
                row["payloadDigest"] = fc.digest(row["summary"])
                row["targetRevision"] = frozen["configRevision"]
                if policy.get("runtime") == NOTIFICATION_RUNTIME:
                    row["notificationRuntime"] = copy.deepcopy(NOTIFICATION_RUNTIME)
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

    def _completed_chain_boundary(self, state, run_id):
        """Prove a committed prefix without authorizing any previous effect again."""
        plan = state["plans"][run_id]
        steps = plan["frozen"]["steps"]
        rows = state["steps"][run_id]
        if set(rows) != {step["stepId"] for step in steps}:
            return False
        completed, pending = [], False
        for step in steps:
            row = rows[step["stepId"]]
            if row["state"] == "pending" and row["attemptCount"] == 0:
                pending = True
            elif row["state"] == "completed" and not pending:
                completed.append(step)
            else:
                return False
        if not completed:
            return False
        attempts = [a for a in state["attempts"].values() if a["runId"] == run_id]
        if (len(attempts) != len(completed) or any(a["state"] != "succeeded" for a in attempts)
                or len([e for e in state["effects"] if e["effectKey"]["runId"] == run_id]) != len(attempts)
                or any(a["effectKey"]["runId"] == run_id for a in state["effectAdmissions"].values())):
            return False
        for step in completed:
            matches = [a for a in attempts if a["stepId"] == step["stepId"]]
            if len(matches) != 1:
                return False
            effects = [e for e in state["effects"] if e["attemptId"] == matches[0]["attemptId"]]
            if len(effects) != 1:
                return False
            effect = effects[0]
            receipt = effect["receipt"]
            from packages.jobs.independent_executor import fence
            try:
                if fence(effect.get("attempt")) != fence(matches[0]):
                    return False
            except (ValueError, TypeError):
                return False
            if (effect["outcome"] != "succeeded"
                    or effect["effectKey"] != self.final.ledger.effect_key(run_id, step["stepId"], step["actionRef"]["id"], 0)
                    or receipt.get("effectKey") != effect["effectKey"]
                    or receipt.get("outcome") != "succeeded"
                    or step["stepId"] not in plan.get("outputs", {})
                    or plan["outputs"][step["stepId"]] != receipt.get("receipt", {})):
                return False
        return True

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
                    from packages.jobs.session_delivery_runtime import unproven_channel_attempt
                    if unproven_channel_attempt(state, run['runId']):
                        state['plans'][run['runId']]['runtimeHold'] = 'runtime_reservation_unproven'
                        self._save(db, 'job', job['jobId'], job)
                        continue
                    from packages.jobs.session_delivery_runtime import bound_attempt
                    if bound_attempt(state, run["runId"]):
                        # Only explicit child facts retain the same unfinished
                        # Attempt. Sending facts are queried, never replayed.
                        if run["state"] == "running":
                            self.final.ledger.transition_run(job, self.root, run["runId"], "paused_at_boundary", operation="pause")
                        self._save(db, "job", job["jobId"], job)
                        continue
                    active_attempts = [attempt for attempt in state["attempts"].values()
                                       if attempt["runId"] == run["runId"] and attempt["state"] == "running"]
                    pending = state["plans"][run["runId"]].get("pendingProcessAttempt")
                    if (run["state"] == "running" and len(active_attempts) == 1
                            and active_attempts[0]["attemptId"] == pending):
                        continue  # Original detached host is queried, never resubmitted.
                    if (run["state"] == "running" and not active_attempts and not pending
                            and run["runId"] not in state.get("cancellations", {})
                            and self._completed_chain_boundary(state, run["runId"])):
                        original = db.execute("SELECT document FROM final_operations WHERE id=?",
                            (state["plans"][run["runId"]].get("operationId"),)).fetchone()
                        original = json.loads(original[0]) if original else {}
                        response = original.get("response", {})
                        if (original.get("kind") == "job"
                                and original.get("operation") in {"create", "run-again", "clone-and-run"}
                                and response.get("runId") == run["runId"]
                                and response.get("admissionId") == run["admissionId"]
                                and response.get("result", {}).get("runId") == run["runId"]
                                and response.get("result", {}).get("admissionId") == run["admissionId"]
                                and run["jobId"] == job["jobId"]
                                and any(ref.get("id") == job["jobId"] for ref in response.get("objectRefs", []))):
                            # Continue at a committed boundary, never retry or
                            # reconcile an unfinished Attempt or infer identity.
                            self.final.ledger.transition_run(job, self.root, run["runId"], "retry_wait")
                            self._save(db, "job", job["jobId"], job)
                            continue
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

    @staticmethod
    def _monitoring_proof_valid(receipt, attempt, completion):
        """Validate the runner's bounded proof against the frozen Attempt."""
        from packages.jobs.independent_executor import monitoring_proof_valid
        return monitoring_proof_valid(receipt or {}, attempt, completion)

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
            self.final.ledger.record_effect(job, self.root, attempt["attemptId"], outcome, receipt,
                expected_attempt=expected)
        elif attempt["state"] == "uncertain":
            self.final.ledger.reconcile_attempt(job, self.root, expected_attempt=expected, effect_key=key, receipt=receipt)
        else:
            prior = [r for r in state["effects"] if r["attemptId"] == attempt["attemptId"]]
            if len(prior) != 1 or prior[0]["effectKey"] != key or prior[0]["receipt"] != receipt:
                error("revision_conflict", "Late receipt differs from committed original")
        if outcome == "completed":
            state["plans"][expected["runId"]].setdefault("outputs", {})[expected["stepId"]] = copy.deepcopy(receipt.get("receipt", {}))
        return outcome

    async def recover_external_receipts(self, *, job_id=None):
        # Query only. Never submit execution or repeat a cleanup request here.
        with self.transaction(readonly=True) as db:
            pending = []
            for job in self._all(db, "job"):
                if job_id is not None and job['jobId'] != job_id:
                    continue
                state = aggregate(job, self.root)
                cancelled = state.get("cancellations", {})
                for rid, intent in cancelled.items():
                    if intent["status"] != "confirmed" and intent["targets"]:
                        external = [t for t in intent["targets"] if not self._command_target(job, t)]
                        if external:
                            pending.append((job["jobId"], rid, copy.deepcopy(intent), copy.deepcopy(external)))
                for target in self._external_targets(job):
                    if self._command_target(job, target):
                        continue  # Original aggregate receipt only; never resubmit or query another host.
                    rid = target["attempt"]["runId"]
                    if self.executor is self._product_executor:
                        frozen = state["plans"][rid]["frozen"]
                        step = next(s for s in frozen["steps"] if s["stepId"] == target["attempt"]["stepId"])
                        if frozen_action(frozen, step["stepId"], step["actionRef"]["id"])["execution"]["operation"] in {"session-message", "session-broadcast", "assign"}:
                            continue  # Original local queue lookup owns Session reconciliation.
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
                        plan = state['plans'][rid]
                        if plan.get('pendingProcessAttempt') == target['attempt']['attemptId']:
                            # The cancellation owner has just proven and
                            # consumed this exact original process receipt.
                            plan.pop('pendingProcessAttempt')
                    current["status"] = "confirmed"
                    self.final.ledger.transition_run(job, self.root, rid, "cancelled",
                        operation="reconcile" if state["runs"][rid]["state"] == "uncertain" else "cancel")
                    self._cancel_status(db, job, rid, "completed")
                    self._settle(db, job, rid)
                else:
                    receipt = documents[0].get("result")
                    plan = state["plans"][rid]
                    if rid in state.get("cancellations", {}):
                        continue  # The cancellation owner alone settles its cleanup.
                    original_step = next(s for s in plan["frozen"]["steps"]
                                         if s["stepId"] == targets[0]["attempt"]["stepId"])
                    pending_aid = plan.get("pendingProcessAttempt")
                    is_pending = pending_aid == targets[0]["attempt"]["attemptId"]
                    # A lookup can race a newer Attempt/cancellation or another
                    # pulse. The original pending reference is consumed once.
                    if is_pending:
                        self._validate_process_document(job, targets[0], documents[0])
                        if state["attempts"][pending_aid]["state"] != "running":
                            continue
                        if (receipt and receipt.get('outcome') == 'rejected'
                                and receipt.get('receipt', {}).get('code') in {'orphaned', 'interrupted'}
                                and self._process_retry_reason(documents[0]) is None):
                            # An abnormal label is not proof of a known failed
                            # original effect. Preserve the observation as unknown.
                            receipt = {**copy.deepcopy(receipt), 'outcome': 'unknown',
                                'receipt': {'code': 'process_exit_unproven',
                                    'originalReceipt': copy.deepcopy(receipt.get('receipt', {}))},
                                'cleanupProof': {'confirmed': False}}
                        if (receipt is None and documents[0].get('supervisorIdentity')
                                and documents[0].get('supervisorExitProof', {}).get('confirmed') is True):
                            from packages.core.platform.process import ProcessIdentity, IDENTITY_SCHEMA
                            identity = documents[0]['supervisorIdentity']
                            proof_identity = documents[0]['supervisorExitProof'].get('identity')
                            if (not isinstance(identity, dict) or identity.get('schema') != IDENTITY_SCHEMA
                                    or type(identity.get('pid')) is not int
                                    or not isinstance(identity.get('birth'), str) or not identity['birth']
                                    or proof_identity != identity):
                                error('revision_conflict', 'Original supervisor exit proof identity differs or is incomplete')
                            try:
                                ProcessIdentity.from_dict(identity)
                            except (ValueError, TypeError):
                                error('revision_conflict', 'Original supervisor exit proof identity is invalid')
                            # The original supervisor exited without a terminal
                            # receipt. This is an unknown observation, never an
                            # invented failed result or permission to execute.
                            from packages.actions.typed_executor import EffectReceipt
                            receipt = EffectReceipt(targets[0]['effectKey'], 'unknown', {
                                'code': 'supervisor_exited_without_receipt',
                                'supervisorIdentity': documents[0]['supervisorIdentity'],
                                'supervisorExitProof': documents[0]['supervisorExitProof'],
                            }, {'confirmed': False}).as_dict()
                        if receipt and receipt.get("outcome") in {"unknown", "partial"}:
                            self.final.ledger.finish_attempt(job, self.root, pending_aid, "uncertain")
                            self.final.ledger.record_effect(job, self.root, pending_aid,
                                "partial" if receipt["outcome"] == "partial" else "unknown", receipt)
                            plan.pop("pendingProcessAttempt", None)
                            self.final.ledger.transition_run(job, self.root, rid, "uncertain")
                            self._settle(db, job, rid)
                            continue
                        if receipt and receipt.get("outcome") == "rejected" and not self._cleanup_complete(documents[0]):
                            continue  # Includes a supervisor that has not exited yet.
                    elif self._automatic_policy(plan) and state["attempts"][targets[0]["attempt"]["attemptId"]]["state"] not in {"running", "uncertain"}:
                        continue
                    if not receipt or receipt.get("outcome") not in {"succeeded", "rejected"}:
                        continue
                    action = plan['frozen']['actions'][original_step['actionRef']['id']]
                    if self.executor is self._product_executor and action['execution']['operation'] == 'process':
                        # A later lookup must carry this Attempt's full fence,
                        # including after an unknown outcome lost pending status.
                        try:
                            self._validate_process_document(job, targets[0], documents[0])
                        except fc.ContractError as exc:
                            if exc.code != 'revision_conflict':
                                raise
                            self.diagnostics.append({'at': now(), 'code': 'external_receipt_unavailable',
                                'errorCode': exc.code, 'runId': rid,
                                'attemptId': targets[0]['attempt']['attemptId']})
                            del self.diagnostics[:-100]
                            continue  # Refuse this stale observation, recover other Runs.
                        if (receipt.get('receipt', {}).get('code') in {'orphaned', 'interrupted'}
                                and self._process_retry_reason(documents[0]) is None):
                            self.diagnostics.append({'at': now(), 'code': 'external_receipt_unavailable',
                                'errorCode': 'reconciliation_required', 'runId': rid,
                                'attemptId': targets[0]['attempt']['attemptId']})
                            del self.diagnostics[:-100]
                            continue  # An existing unknown cannot become a claimed failure without proof.
                    if original_step["completion"]["contract"] == "monitoring":
                        if state["attempts"][targets[0]["attempt"]["attemptId"]]["state"] not in {"running", "uncertain"}:
                            continue
                        self._validate_process_document(job, targets[0], documents[0])
                        if not self._cleanup_complete(documents[0]):
                            continue
                        if receipt.get("outcome") == "succeeded" and not self._monitoring_proof_valid(
                                receipt, targets[0]["attempt"], original_step["completion"]):
                            # A successful external effect without the declared
                            # proof is not safe to reinterpret as failed or to
                            # replay. Preserve the original identity as unknown.
                            unknown = copy.deepcopy(receipt)
                            unknown["outcome"] = "unknown"
                            unknown["receipt"] = {
                                "code": "monitoring_completion_invalid",
                                "originalReceipt": copy.deepcopy(receipt.get("receipt", {})),
                            }
                            aid = targets[0]["attempt"]["attemptId"]
                            if state["attempts"][aid]["state"] == "running":
                                self.final.ledger.finish_attempt(job, self.root, aid, "uncertain")
                                self.final.ledger.record_effect(job, self.root, aid, "unknown", unknown)
                            else:
                                continue  # Repeated lookup cannot create another effect row.
                            if is_pending:
                                plan.pop("pendingProcessAttempt", None)
                            self.final.ledger.transition_run(job, self.root, rid, "uncertain")
                            self._settle(db, job, rid)
                            continue
                    # A control may have arrived while lookup was in flight.
                    if rid in state.get("cancellations", {}):
                        continue
                    outcome = self._apply_original_receipt(job, targets[0], receipt)
                    if is_pending:
                        plan.pop("pendingProcessAttempt", None)
                    target_state = ("completed" if all(s["state"] == "completed" for s in state["steps"][rid].values())
                                    else "retry_wait") if outcome == "completed" else "failed"
                    run = state["runs"][rid]
                    self.final.ledger.transition_run(job, self.root, rid, target_state,
                        operation="reconcile" if run["state"] == "uncertain" else None)
                    if is_pending and outcome == "failed" and self._schedule_automatic(db, job, rid, documents[0]):
                        continue
                    self._save(db, "job", job_id, job)
                    if target_state != "retry_wait":
                        self._settle(db, job, rid)

    async def recover_session_receipts(self):
        """Query the original local worker queue/task identity; never send.

        Session providers have no remote reconcile protocol. Only the default
        local host can prove durable enqueue from its existing worker store.
        Missing/expired receipts remain unknown. Fanout stays one parent effect.
        """
        if self.executor is not self._product_executor:
            return
        from packages.core import worker
        from packages.actions.typed_executor import EffectReceipt
        with self.transaction(readonly=True) as db:
            pending = []
            for job in self._all(db, "job"):
                state = aggregate(job, self.root)
                for target in self._external_targets(job):
                    attempt = target["attempt"]
                    if attempt["state"] != "uncertain":
                        continue
                    plan = state["plans"][attempt["runId"]]
                    frozen = plan["frozen"]
                    step = next(s for s in frozen["steps"] if s["stepId"] == attempt["stepId"])
                    action = frozen_action(frozen, step["stepId"], step["actionRef"]["id"])
                    op = action["execution"]["operation"]
                    if op not in {"session-message", "session-broadcast", "assign", "resume-legal-running.v1"}:
                        continue
                    args = resolve_bindings(step["arguments"], frozen["input"], frozen["event"], plan.get("outputs", {}))
                    if op == "resume-legal-running.v1":
                        selections = plan.get("resumeSelections")
                        ids = selections.get(step["stepId"]) if isinstance(selections, dict) else None
                        if not isinstance(ids, list) or any(not isinstance(sid, str) or not sid for sid in ids):
                            continue  # Missing selection is not an empty successful scan.
                        args = {"sessionIds": copy.deepcopy(ids)}
                    prior = next(e["receipt"] for e in state["effects"] if e["attemptId"] == attempt["attemptId"])
                    from packages.jobs.session_delivery_runtime import original_arguments
                    try:
                        args = original_arguments(plan, target, args)
                    except fc.ContractError:
                        continue  # Wrong original identity never falls back to a new target.
                    pending.append((target, op, args, copy.deepcopy(prior)))
        for target, op, args, prior in pending:
            key = target["effectKey"]
            fanout = op in {"session-broadcast", "resume-legal-running.v1"}
            resume = op == "resume-legal-running.v1"
            ids = list(dict.fromkeys(args["sessionIds"])) if fanout else [args["sessionId"]]
            rows = []
            runtime_children = args.get("_runtimeChildren")
            if runtime_children:
                from packages.jobs.session_delivery_runtime import query_original_child
                rows = [copy.deepcopy(c['receipt']) if c['status'] == 'accepted' else query_original_child(c)
                        for c in runtime_children if c.get('arguments')]
                if len(rows) != len(runtime_children):
                    continue  # A missing child fact never proves no send.
                ids = [c['targetId'] for c in runtime_children]
            for sid in ([] if runtime_children else ids):
                child = {**key, "targetId": sid} if fanout else key
                previous = next((r for r in prior.get("receipt", {}).get("results" if resume else "targets", []) if r.get("effectKey") == child), None) if fanout else prior
                if previous and previous.get("outcome") in {"succeeded", "rejected"}:
                    rows.append(previous)
                    continue
                def lookup():
                    session = worker._get_session_shallow(sid)
                    if session is None:
                        return None
                    if op == "assign":
                        task_id = args.get("taskId") or "effect_" + fc.digest(child)[:40]
                        item = worker._find_queue_item_by_idempotency(session, task_id=task_id)
                    elif resume:
                        from packages.actions.session_resume import client_message_id
                        item = worker._find_queue_item_by_idempotency(session, client_message_id=client_message_id(child))
                        if item is not None and item.get("source") != "automation":
                            return None
                    else:
                        item = worker._find_queue_item_by_idempotency(session, client_message_id="effect_" + fc.digest(child)[:40])
                    # A key collision is not proof for a different body.
                    if item is None or (not resume and "text" in item and item["text"] != args["text"]):
                        return None
                    return {"status": "accepted", "sessionId": sid, "queueItemId": worker._queue_item_id(item),
                            "taskId": item.get("taskId"), "consumption": "unconfirmed"}
                try:
                    async with worker.queue_lock(sid):
                        observed = await asyncio.to_thread(lookup)
                except Exception:
                    observed = None  # Unreadable original evidence stays unknown.
                rows.append(EffectReceipt(child, "succeeded" if observed else "unknown", observed or {"code": "original_receipt_unavailable"},
                                          target_selection={"sessionIds": [sid]}).as_dict())
            outcomes = [r["outcome"] for r in rows]
            if "unknown" in outcomes:
                continue  # Absence cannot prove rejection, even after restart.
            outcome = "succeeded" if all(s == "succeeded" for s in outcomes) else "rejected" if all(s == "rejected" for s in outcomes) else "partial"
            if outcome == "partial":
                continue  # Existing canonical uncertain aggregate stays queryable.
            receipt = rows[0] if not fanout else EffectReceipt(key, outcome,
                ({"matchedCount": len(ids), "results": rows} if resume else
                 {"targets": rows, "matched": len(rows), "succeeded": outcomes.count("succeeded"),
                  "rejected": outcomes.count("rejected"), "unknown": 0}), target_selection={"sessionIds": ids}).as_dict()
            with self.transaction() as db:
                job = self._get(db, "job", target["jobId"])
                state = aggregate(job, self.root)
                aid, rid = target["attempt"]["attemptId"], target["attempt"]["runId"]
                if state["attempts"][aid]["state"] != "uncertain" or rid in state.get("cancellations", {}):
                    continue
                reservation = state["plans"][rid].get("runtimeDelivery", {}).get(target["attempt"]["stepId"])
                if reservation:
                    from packages.jobs.session_delivery_runtime import receipt_projection
                    if runtime_children:
                        for child, observed in zip(reservation['children'], rows):
                            child.update(status='accepted', receipt=copy.deepcopy(observed))
                    receipt["deliveryRuntime"] = receipt_projection(reservation, receipt)
                resolved = self._apply_original_receipt(job, target, receipt)
                result = ("completed" if all(s["state"] == "completed" for s in state["steps"][rid].values()) else "retry_wait") if resolved == "completed" else "failed"
                self.final.ledger.transition_run(job, self.root, rid, result, operation="reconcile")
                self._save(db, "job", job["jobId"], job)
                if result != "retry_wait":
                    self._settle(db, job, rid)

    def targets(self, event):
        with self.transaction(readonly=True) as db:
            return [{**item, "_final": True, "enabled": item["state"] == "enabled"}
                    for item in self._all(db, "trigger") if event["type"] in item["eventTypes"]]

    def admit_event(self, event, target, *, condition_result=None):
        """WP03 admission only: no Action or provider effect is invoked here."""
        # A repeated fact is a query, including on a cold/non-owner host.
        with self.transaction(readonly=True) as db:
            prior = db.execute("SELECT operationId,factDigest FROM final_event_operations WHERE eventId=? AND triggerId=?",
                (event.get("eventId", event.get("id")), target["id"])).fetchone()
            if prior:
                if prior["factDigest"] != fc.digest(event):
                    error("idempotency_conflict", "Event fact changed")
                record = json.loads(db.execute("SELECT document FROM final_operations WHERE id=?", (prior["operationId"],)).fetchone()[0])
                result = {**copy.deepcopy(record["response"]), "replayed": True}
                if self.automation_ledger is not None and self.owner.token is None and self.owner.status() is None:
                    self.automation_ledger.record_final_admission(event.get("eventId", event.get("id")), target["id"], result)
                return result
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
                return {"status": "deferred", "reason": "trigger_revision_changed"}
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
            if trigger["state"] in {"paused", "disabled"}:
                return {"status": "deferred", "reason": "trigger_temporarily_inactive"}
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
            self._permit(actor, "trigger", "lifecycle", execution=True)
            if trigger.get("target", {}).get("type") == "action":
                from packages.jobs.action_trigger import admit
                result = admit(self, db, trigger, legacy_event, actor, key, fact_digest)
                if result["status"] != "deferred":
                    db.execute("INSERT INTO final_event_operations VALUES(?,?,?,?)",
                        (eid, trigger["id"], result["operationId"], fact_digest))
                return result
            target_job = trigger.get("target", {})
            job = self._get(db, "job", trigger.get("targetJobId", target_job.get("jobId")))
            if target_job and target_job["configRevision"] != job["configRevision"]:
                error("reference_conflict", "Trigger fixed Job revision changed")
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

    def _subscription_snapshot(self):
        with self.owner.writer(canonical=True), self.transaction(readonly=True) as db:
            return {item["id"]: {**item, "subscriptionId": item["id"],
                "creatorIds": item["creatorSelector"].get("ids", [])} for item in self._all(db, "trigger")}

    def refresh_subscriptions(self):
        self.bus.subscriptions = self._subscription_snapshot()

    def scheduler_migration(self, legacy_events):
        """Explicit reviewed handoff port; never instantiated by startup scans."""
        from packages.scheduler.source_migration import SourceMigration
        return SourceMigration(self, legacy_events)

    async def tick(self):
        # Publish on the owning loop only after the offloaded immutable read.
        self.bus.subscriptions = await asyncio.to_thread(self._subscription_snapshot)
        await self.creator_runtime.tick()
        for event in await asyncio.to_thread(self.events.page):
            snapshot = await asyncio.to_thread(self.events.get, event["eventId"])
            for target in snapshot["targets"]:
                with self.transaction() as db:
                    if db.execute("SELECT 1 FROM final_event_receipts WHERE eventId=? AND triggerId=?", (event["eventId"], target["id"])).fetchone():
                        continue
                try:
                    # Preserve the published target IDs; re-read only that
                    # Trigger so pause/resume revisions and State evaluation
                    # use its current policy. A concurrent edit defers below.
                    with self.transaction(readonly=True) as db:
                        current_target = self._get(db, "trigger", target["id"])
                    result = await self.admit_event_async(event, current_target)
                except (fc.ContractError, KeyError):
                    self.diagnostics.append({"at": now(), "code": "event_admission_blocked"})
                    continue
                if result["status"] == "deferred":
                    continue
                with self.transaction() as db:
                    db.execute("INSERT OR IGNORE INTO final_event_receipts VALUES(?,?,?)", (event["eventId"], target["id"], result["status"]))
        from packages.jobs.action_trigger import deliver
        await deliver(self)
        await self.dispatch_once()
        await self.deliver_outbox()
        from packages.jobs.retention import pulse
        await asyncio.to_thread(pulse, self)

    def _pending_outbox_snapshot(self):
        # Reservation below re-reads the original row under the writer fence.
        with self.owner.writer(canonical=True), self.transaction(readonly=True) as db:
            return [(job["jobId"], copy.deepcopy(row)) for job in self._all(db, "job")
                    for row in aggregate(job, self.root)["outbox"].values() if row["status"] == "pending"]

    async def deliver_outbox(self):
        from packages.jobs.notification_delivery import NOTIFICATION_RUNTIME, is_local_no_attempt
        if self.outbox_sender is None:
            return
        async with self._outbox_lock:
            pending = await asyncio.to_thread(self._pending_outbox_snapshot)
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
                    # Only opted-in, not-yet-accepted notification rows may
                    # select current config at this existing reservation fence.
                    if current.get("notificationRuntime") == NOTIFICATION_RUNTIME:
                        policy = job["canonical"].get("notification", {})
                        recipient = next((r for r in policy.get("recipients", [])
                            if r["recipientId"] == current["recipientId"] and r["channelId"] == current["channel"]), None)
                        if recipient is None:
                            current.update(status="suppressed", receipt={"outcome": "suppressed", "reason": "recipient_removed"})
                            self._save(db, "job", job_id, job)
                            continue
                        current["targetRef"] = recipient["targetRef"]
                        current["targetRevision"] = job["configRevision"]
                        current["runtimeOperationId"] = job.get("runtimeOperationId")
                        current["summary"]["targetSessionId"] = current["targetRef"]
                        current["payloadDigest"] = fc.digest(current["summary"])
                    current["status"] = "sending"
                    row = copy.deepcopy(current)
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
                    outcome = receipt.get("outcome")
                    trusted_no_attempt = is_local_no_attempt(receipt, row)
                    current.update(status=("acked" if outcome == "succeeded" else
                                           "pending" if trusted_no_attempt else "uncertain"),
                                   receipt=dict(receipt))
                    self._save(db, "job", job_id, job)

    def _guard_creator(self, item):
        from packages.scheduler.source_migration import validate_successors
        validate_successors(self, item)
        with self.transaction(readonly=True) as db:
            row = db.execute("SELECT document FROM final_authorities WHERE kind='event_creator' AND id=?", (item["id"],)).fetchone()
            if row is None:
                error("authorization_denied", "Creator authority unavailable")
            self._permit(json.loads(row[0]), "event_creator", "lifecycle", execution=True)
        from packages.scheduler.json_handoff import guard_clock
        guard_clock(self, item)

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
    service.session_pause = automation.pause
    automation.events.owner_fence = service.owner
    automation.final_jobs = service
    from packages.automation.quota_native import NativeQuota
    service.quota_native = NativeQuota(automation.quota_policy, service)
    automation.quota_policy.native = service.quota_native
    if service._product_executor is not None:
        service._product_executor.quota_provider = service.quota_native.query
    app.state.job_final_service = service
    return service
