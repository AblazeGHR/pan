"""Canonical HTTP boundary (§10.4).

This module owns transport validation and authorization, not an execution loop.
The host supplies ``app.state.job_final_service`` implementing the service port
below. Missing host bindings fail before admission; they never fall back to an
old payload, dispatcher, or an in-memory imitation of a durable operation.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import re
import sqlite3
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from packages.automation.auth import UserAuth
from packages.automation.authorization import delegated
from packages.plugins.lifecycle import io
from . import final_contracts as contracts


API_PREFIX = "/api"
DEPRECATED_PREFIX = "/api/final"
COOKIE_NAME = "pan_user"
KINDS = {"jobs": "job", "triggers": "trigger", "event-creators": "event_creator",
         "job-templates": "template"}
GRANT_KINDS = {"job": "job", "trigger": "trigger", "event_creator": "eventCreator",
               "template": "jobTemplate", "action": "action"}


class CanonicalUserAuth(UserAuth):
    """Add the proposed Creator row without rewriting existing grants/revision.

    A legacy four-row document is read as-is with the new row denied. A user
    permission write can explicitly grant the new row. Unknown fields and
    partial/malformed existing rows continue to fail closed.
    """

    PERMISSION_SCHEMA = {**UserAuth.PERMISSION_SCHEMA,
                         "eventCreator": ("read", "create", "updateConfig", "lifecycle", "delete")}
    DEFAULT_PERMISSIONS = {**copy.deepcopy(UserAuth.DEFAULT_PERMISSIONS),
                           "eventCreator": {key: False for key in PERMISSION_SCHEMA["eventCreator"]}}

    @classmethod
    def _validate_permissions(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Invalid authorization permissions")
        old = {key: item for key, item in value.items() if key != "eventCreator"}
        result = UserAuth._validate_permissions(old)
        row = value.get("eventCreator", cls.DEFAULT_PERMISSIONS["eventCreator"])
        names = set(cls.PERMISSION_SCHEMA["eventCreator"])
        if not isinstance(row, dict) or set(row) != names or any(type(row[key]) is not bool for key in names):
            raise ValueError("Invalid authorization permission fields for eventCreator")
        result["eventCreator"] = copy.deepcopy(row)
        return result


class CanonicalService(Protocol):
    """Host port. Every write must be a durable §10.4 operation.

    ``actor`` is host-derived, never supplied by the request body. The service
    must scope requestKey by principal/profile/operation, check CAS and object
    references atomically, and recheck grants at every effect boundary. A
    response with accepted/pending/unknown must be queryable after restart.
    ``list`` returns a page after applying the existing visibility policy.
    Operation lookups enforce the referenced object's read grant and the same
    principal/profile scope; knowing an operation ID grants no visibility.
    """

    def list(self, kind: str, *, actor: Mapping[str, Any], cursor: str | None, limit: int): ...
    def get(self, kind: str, object_id: str, *, actor: Mapping[str, Any]): ...
    def write(self, kind: str, object_id: str | None, body: dict, *, actor: Mapping[str, Any]): ...
    def control(self, kind: str, object_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def retire(self, kind: str, object_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def instantiate_time_template(self, template_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def instantiate_template(self, template_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def resolve_time_input(self, body: dict, *, actor: Mapping[str, Any]): ...
    def preview_creator(self, body: dict, *, actor: Mapping[str, Any]): ...
    def runs(self, job_id: str, *, actor: Mapping[str, Any], cursor: str | None, limit: int): ...
    def create_run(self, job_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def get_run(self, run_id: str, *, actor: Mapping[str, Any]): ...
    async def read_process_log(self, run_id: str, attempt_id: str, *, offset: int, limit: int, actor: Mapping[str, Any]): ...
    async def process_log_references(self, run_id: str, *, actor: Mapping[str, Any], cursor: str | None, limit: int): ...
    def control_run(self, run_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    async def control_run_postcommit(self, run_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def operation(self, operation_id: str, *, actor: Mapping[str, Any]): ...
    def operation_for_key(self, operation: str, request_key: str, *, actor: Mapping[str, Any]): ...
    def actions(self, *, actor: Mapping[str, Any]): ...
    async def invoke_action(self, action_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def action_job_references(self, action_id: str, *, actor, cursor=None, limit=100): ...
    def preview_action_references(self, body: dict, *, actor): ...
    def apply_action_references(self, body: dict, *, actor): ...
    def job_provenance(self, job_id: str, *, actor): ...
    def action_versions(self, action_id: str, *, actor: Mapping[str, Any]): ...
    def action_ref(self, action_id: str, *, actor: Mapping[str, Any], revision: int | None = None,
                   digest: str | None = None, latest: bool = False): ...
    def action_alias(self, legacy_id: str, *, actor: Mapping[str, Any]): ...


def _service(request):
    """The single FinalService instance of this app (never a legacy runtime)."""
    return getattr(getattr(request.app, "state", None), "job_final_service", None)


def reject(code, message, *, path=None):
    raise contracts.ContractError(code, message, path=path)


RETIRED_MESSAGE = "Legacy write entrypoint retired; use the canonical /api API"


async def retired_legacy_write(request: Request):
    """Authenticate a compatibility caller, then close the old write boundary.

    Authentication runs first so an unknown/revoked caller keeps its original
    401/403 instead of learning which legacy paths remain.  Nothing below this
    point reaches an old handler, store, or process.  The contract error is
    translated here because this boundary is not a canonical ``ContractRoute``
    and an escaping exception must never surface as a 500.
    """
    try:
        await actor(request)
    except contracts.ContractError as exc:
        raise HTTPException(status_code=exc.status,
                            detail={"code": exc.code, "message": exc.code.replace("_", " ")}) from exc
    raise HTTPException(status_code=410, detail={"code": "retired", "message": RETIRED_MESSAGE})


retired_legacy_write.retirement_boundary = True


async def legacy_template_list(request: Request):
    who = await actor(request, "template", "read")
    return await invoke(request, "legacy_template_list", actor=who)


async def _legacy_template_write(request, template_id=None):
    who = await actor(request, "template", "create" if template_id is None else "updateConfig")
    # Resolve the canonical port before validating old payloads. Never fall
    # back to ActionRuntime/Templates when activation is missing or failed.
    if not callable(getattr(getattr(request.app.state, "job_final_service", None), "legacy_template_write", None)):
        reject("provider_unavailable", "Canonical Template service unavailable")
    value = await body(request)
    fields(value, {"item", "expectedRevision"}, {"item", "expectedRevision"})
    if type(value["expectedRevision"]) is not int or value["expectedRevision"] < (0 if template_id is None else 1):
        reject("invalid_schema", "Invalid expectedRevision")
    if not isinstance(value["item"], dict):
        reject("invalid_schema", "Template item must be an object")
    item = dict(value["item"])
    if template_id is not None:
        if item.get("id", template_id) != template_id:
            reject("reference_conflict", "Template path and payload identity differ")
        item["id"] = template_id
    key = "legacy-template-" + ("create:" if template_id is None else "update:")
    key += str(item.get("id", "new")) + ":" + str(value["expectedRevision"])
    return await invoke(request, "legacy_template_write", item, value["expectedRevision"], key, actor=who)


async def legacy_template_create(request: Request):
    return await _legacy_template_write(request)


async def legacy_template_update(template_id: str, request: Request):
    return await _legacy_template_write(request, template_id)


async def legacy_template_delete(template_id: str, request: Request):
    who = await actor(request, "template", "delete")
    raw = request.query_params.get("expectedRevision", "")
    if (set(request.query_params) != {"expectedRevision"}
            or len(request.query_params.getlist("expectedRevision")) != 1
            or not re.fullmatch(r"0|[1-9][0-9]{0,18}", raw)):
        reject("invalid_schema", "One expectedRevision is required")
    expected = int(raw)
    return await invoke(request, "legacy_template_delete", template_id, expected,
                        "legacy-template-delete:" + template_id + ":" + raw, actor=who)


async def legacy_template_instantiate(template_id: str, request: Request):
    who = await actor(request, "template", "use")
    await actor(request, "job", "create")
    if not callable(getattr(getattr(request.app.state, "job_final_service", None), "legacy_template_instantiate", None)):
        reject("provider_unavailable", "Canonical Template service unavailable")
    value = await body(request)
    fields(value, {"expectedRevision", "idempotencyKey", "overrides"}, {"expectedRevision", "idempotencyKey"})
    revision(value["expectedRevision"])
    contracts.check_request_key(value["idempotencyKey"])
    return await invoke(request, "legacy_template_instantiate", template_id, value["expectedRevision"],
                        value["idempotencyKey"], value.get("overrides"), actor=who, activate=False)


def _is_retired_legacy_route(suffix: str, methods: set[str]) -> bool:
    """Whether an old facade route was an effectful write.

    Historical reads and the explicit cancel control stay on the legacy facade
    until P6 removes the legacy UI: a legacy Job that still owns a process must
    remain inspectable and cancellable.  Every other old mutation is refused.
    """
    # P6 compatibility handoff: the old Template CRUD/instantiate shape is
    # admitted only through this canonical boundary, which calls the same
    # FinalService owner fence and operation receipt as /api/job-templates.
    if suffix.startswith("/job-templates"):
        return False
    if methods <= {"GET", "HEAD", "OPTIONS"}:
        return False
    if suffix.endswith("/cancel"):
        return False
    return True


def _retired_route(target, path, methods):
    target.router.add_api_route(
        path, retired_legacy_write, methods=sorted(methods),
        name="retired_legacy_" + path.strip("/").replace("/", "_").replace("{", "").replace("}", ""),
        tags=["retired"], deprecated=True,
    )


def same_origin(request):
    """Cookie writes require an explicit, exact Origin; forwarded headers lie."""
    origin = request.headers.get("origin")
    try:
        parsed = urlsplit(origin or "")
        target = urlsplit(str(request.url))
        def identity(value):
            return (value.scheme.lower(), value.hostname,
                    value.port or (443 if value.scheme == "https" else 80))
        allowed = (parsed.scheme in {"http", "https"} and parsed.hostname is not None
                   and not parsed.username and not parsed.password
                   and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                   and identity(parsed) == identity(target))
    except ValueError:
        allowed = False
    if not allowed or request.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        reject("authorization_denied", "Cookie writes require the same Origin")


async def actor(request, kind=None, capability=None):
    svc = getattr(request.app.state, "automation", None)
    if svc is None:
        reject("provider_unavailable", "Authorization unavailable")
    if delegated(request):
        state = await io(svc.auth.permissions_status)
        revision = state["revision"]
        if capability is not None:
            allowed, revision = await io(svc.auth.permission_allowed, GRANT_KINDS[kind], capability)
            if not allowed:
                reject("authorization_denied", "Object capability denied")
        return {"type": "agent", "id": "agent", "revision": revision}
    header = request.headers.get("authorization")
    # An explicit invalid Bearer never falls back to a cookie.
    if header is None:
        cookie = request.cookies.get(COOKIE_NAME)
        header = "Bearer " + cookie if cookie else ""
        if cookie and request.method not in {"GET", "HEAD", "OPTIONS"}:
            same_origin(request)
    if not await io(svc.auth.authorized, header):
        reject("unauthenticated", "User authentication or trusted transport required")
    return {"type": "user", "id": "local"}


async def body(request):
    if request.headers.get("content-type", "").split(";", 1)[0].lower().strip() != "application/json":
        reject("invalid_schema", "JSON content type required")
    raw = await request.body()
    if len(raw) > contracts.MAX_DEFINITION_BYTES:
        reject("invalid_schema", "Request exceeds contract byte limit")
    try:
        value = contracts.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        reject("invalid_schema", "Invalid UTF-8 JSON")
    if not isinstance(value, dict):
        reject("invalid_schema", "Request body must be an object")
    return value


def fields(value, allowed, required):
    if set(value) - set(allowed) or set(required) - set(value):
        reject("invalid_schema", "Invalid request fields")


def revision(value, field="expectedRevision"):
    minimum = 0 if field == "expectedStateRevision" else 1
    if type(value) is not int or value < minimum:
        reject("invalid_schema", "Invalid revision", path=field)
    return value


def page(request):
    if set(request.query_params) - {"cursor", "limit"}:
        reject("invalid_schema", "Unknown pagination query")
    try:
        limit = int(request.query_params.get("limit", "100"))
    except ValueError:
        reject("invalid_schema", "Invalid limit")
    if not 1 <= limit <= 100:
        reject("invalid_schema", "Limit must be 1..100")
    return {"cursor": request.query_params.get("cursor"), "limit": limit}


def page_result(kind, value, *, runtime=False):
    if not isinstance(value, dict) or set(value) != {"items", "nextCursor"} or not isinstance(value["items"], list):
        reject("invalid_schema", "Invalid service page")
    if value["nextCursor"] is not None and not isinstance(value["nextCursor"], str):
        reject("invalid_schema", "Invalid service cursor")
    validate = contracts.validate_record if runtime else contracts.validate_object
    return {"items": [validate(kind, item) for item in value["items"]], "nextCursor": value["nextCursor"]}


async def invoke(request, method, *args, **kwargs):
    service = getattr(request.app.state, "job_final_service", None)
    function = getattr(service, method, None)
    if function is None:
        reject("provider_unavailable", "Canonical service binding unavailable")
    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    value = await io(function, *args, **kwargs)
    return await value if inspect.isawaitable(value) else value


def write_result(value, *, created=False):
    contracts.validate_operation_result(value)
    status = value["status"]
    # A service explicitly reports replay so repeated create returns 200.
    code = 202 if status in {"accepted", "pending", "unknown"} else 200
    if created and status == "completed" and not value.get("replayed", False):
        code = 201
    if status == "rejected":
        error = value.get("error", {})
        code = contracts.ERROR_HTTP_STATUS.get(error.get("code"), 400)
    return JSONResponse(value, status_code=code)


class ContractRoute(APIRoute):
    def get_route_handler(self):
        endpoint = super().get_route_handler()
        async def handle(request):
            try:
                return await endpoint(request)
            except contracts.ContractError as exc:
                # Schema validators can include untrusted property names in
                # their messages. Return paths/categories, never input values.
                safe = {"code": exc.code, "message": exc.code.replace("_", " ")}
                # Do not echo an attacker-chosen unknown property name.
                if exc.path in {"body", "definition", "expectedRevision", "expectedStateRevision",
                                "requestKey", "operation", "name", "definition.name",
                                "definition.steps", "definition.retention"}:
                    safe["path"] = exc.path
                if "currentRevision" in exc.details:
                    safe["currentRevision"] = exc.details["currentRevision"]
                return JSONResponse({"error": safe}, status_code=exc.status)
            except KeyError:
                return JSONResponse({"error": {"code": "not_found", "message": "Object not found"}}, status_code=404)
            except (OSError, sqlite3.Error):
                return JSONResponse({"error": {"code": "storage_unavailable", "message": "Storage unavailable"}}, status_code=503)
            except HTTPException as exc:
                codes = {401: "unauthenticated", 403: "authorization_denied", 404: "not_found"}
                if exc.status_code not in codes:
                    raise
                return JSONResponse({"error": {"code": codes[exc.status_code], "message": codes[exc.status_code]}}, status_code=exc.status_code)
        return handle


router = APIRouter(route_class=ContractRoute, tags=["canonical-jobs"])


def definition_routes(base, kind):
    def write_body(raw, *, create=False):
        extra = "expectedReferences" if kind == "event_creator" else "expectedCreatorRevisions" if kind == "trigger" else None
        extras = {extra: raw.pop(extra)} if extra and extra in raw else {}
        value = contracts.validate_write_input(kind, raw, create=create)
        if "expectedReferences" in extras:
            if not isinstance(extras["expectedReferences"], str):
                reject("invalid_schema", "expectedReferences must be a digest")
        if "expectedCreatorRevisions" in extras and not isinstance(extras["expectedCreatorRevisions"], dict):
            reject("invalid_schema", "expectedCreatorRevisions must be an object")
        value.update(extras)
        return value

    async def listing(request: Request):
        who = await actor(request, kind, "read")
        return page_result(kind, await invoke(request, "list", kind, actor=who, **page(request)))

    async def detail(object_id: str, request: Request):
        who = await actor(request, kind, "read")
        return contracts.validate_object(kind, await invoke(request, "get", kind, object_id, actor=who))

    async def create(request: Request):
        who = await actor(request, kind, "create")
        raw = await body(request)
        # Form is a convenience envelope, never a new canonical document schema.
        # The service resolves the fixed product publication, normalizes its
        # arguments and runs the existing write contract before persistence.
        if kind == "template" and "form" in raw:
            fields(raw, {"definition", "form", "expectedRevision", "requestKey"},
                   {"definition", "form", "expectedRevision", "requestKey"})
            contracts.check_request_key(raw["requestKey"])
            value = raw
        else:
            value = write_body(raw, create=True)
        return write_result(await invoke(request, "write", kind, None, value, actor=who), created=True)

    async def update(object_id: str, request: Request):
        who = await actor(request, kind, "updateConfig")
        value = write_body(await body(request))
        return write_result(await invoke(request, "write", kind, object_id, value, actor=who))

    async def control(object_id: str, request: Request):
        # Authenticate before parsing, then check the exact operation's grant.
        who = await actor(request)
        value = await body(request)
        fields(value, {"operation", "expectedRevision", "expectedStateRevision", "requestKey", "parameters"},
               {"operation", "expectedRevision", "expectedStateRevision", "requestKey", "parameters"})
        contracts.check_operation(kind, value["operation"])
        revision(value["expectedRevision"])
        revision(value["expectedStateRevision"], "expectedStateRevision")
        contracts.check_request_key(value["requestKey"])
        if not isinstance(value["parameters"], dict):
            reject("invalid_schema", "parameters must be an object")
        capability = "updateConfig" if kind == "template" else "lifecycle"
        who = await actor(request, kind, capability)
        return write_result(await invoke(request, "control", kind, object_id, value, actor=who))

    async def delete(object_id: str, request: Request):
        who = await actor(request, kind, "delete")
        value = await body(request)
        fields(value, {"expectedRevision", "expectedStateRevision", "requestKey"} | ({"expectedReferences"} if kind == "event_creator" else set()),
               {"expectedRevision", "expectedStateRevision", "requestKey"})
        revision(value["expectedRevision"])
        revision(value["expectedStateRevision"], "expectedStateRevision")
        contracts.check_request_key(value["requestKey"])
        # DELETE means retirement. Physical resource purge is never implicit.
        value.update(operation="retire", parameters={"expectedReferences": value.pop("expectedReferences")} if "expectedReferences" in value else {})
        return write_result(await invoke(request, "retire", kind, object_id, value, actor=who))

    routes = [("", "GET", listing), ("", "POST", create),
              ("/{object_id}", "GET", detail), ("/{object_id}", "PUT", update),
              ("/{object_id}", "DELETE", delete),
              ("/{object_id}/operations", "POST", control)]
    # Jobs retain the existing canonical-shaped compatibility DELETE handler
    # below; registering the generic definition DELETE as well creates two
    # identical production routes and makes dispatch order ambiguous.
    if kind == "job":
        routes = [row for row in routes if not (row[0] == "/{object_id}" and row[1] == "DELETE")]
    for suffix, method, function in routes:
        router.add_api_route(base + suffix, function, methods=[method], name=f"canonical_{kind}_{function.__name__}")


@router.get("/jobs/actions")
async def action_catalog(request: Request):
    who = await actor(request, "action", "read")
    return await invoke(request, "actions", actor=who)


async def _action_write_body(request, *, create=False):
    value = await body(request)
    fields(value, {"definition", "expectedRevision", "requestKey", "requestDigest"},
           {"definition", "expectedRevision", "requestKey"})
    revision(value["expectedRevision"], "expectedRevision") if not create else (
        None if type(value["expectedRevision"]) is int and value["expectedRevision"] == 0
        else reject("invalid_schema", "Create expectedRevision must be zero", path="expectedRevision")
    )
    contracts.check_request_key(value["requestKey"])
    return value


@router.post("/jobs/actions/definition-preview")
async def action_definition_preview(request: Request):
    """Definition-only adapter for the reused legacy quick-create form."""
    who = await actor(request, "action", "read")
    return await invoke(request, "legacy_action_definition", await body(request), actor=who)


@router.post("/jobs/actions")
async def action_create(request: Request):
    who = await actor(request, "action", "create")
    value = await _action_write_body(request, create=True)
    return write_result(await invoke(request, "write", "action", None, value, actor=who), created=True)


@router.get("/jobs/actions/aliases/{legacy_id}")
async def action_alias(legacy_id: str, request: Request):
    who = await actor(request, "action", "read")
    return await invoke(request, "action_alias", legacy_id, actor=who)


@router.get("/jobs/actions/{action_id}/job-references")
async def action_job_references(action_id: str, request: Request):
    who = await actor(request, "action", "read")
    return await invoke(request, "action_job_references", action_id, actor=who, **page(request))


@router.post("/jobs/action-references/preview")
async def preview_action_references(request: Request):
    who = await actor(request, "job", "updateConfig")
    return await invoke(request, "preview_action_references", await body(request), actor=who)


@router.post("/jobs/action-references/apply")
async def apply_action_references(request: Request):
    who = await actor(request, "job", "updateConfig")
    return await invoke(request, "apply_action_references", await body(request), actor=who)


@router.get("/jobs/{job_id}/provenance")
async def job_provenance(job_id: str, request: Request):
    who = await actor(request, "job", "read")
    return await invoke(request, "job_provenance", job_id, actor=who)


@router.post("/jobs/actions/{action_id}/invocations")
async def action_invoke(action_id: str, request: Request):
    who = await actor(request, "action", "invoke")
    value = await body(request)
    fields(value, {"actionRef", "arguments", "requestKey"}, {"actionRef", "arguments", "requestKey"})
    contracts.check_request_key(value["requestKey"])
    if not isinstance(value["arguments"], dict):
        reject("invalid_schema", "arguments must be an object")
    return await invoke(request, "invoke_action", action_id, value, actor=who)


@router.get("/jobs/actions/{action_id}/versions")
async def action_versions(action_id: str, request: Request):
    who = await actor(request, "action", "read")
    return await invoke(request, "action_versions", action_id, actor=who)


@router.get("/jobs/actions/{action_id}/versions/{revision_value}")
async def action_version(action_id: str, revision_value: str, request: Request):
    who = await actor(request, "action", "read")
    try:
        revision_value = int(revision_value)
    except ValueError:
        reject("invalid_schema", "Invalid Action revision")
    if revision_value < 1:
        reject("invalid_schema", "Invalid Action revision")
    digest = request.query_params.get("digest")
    value = await invoke(request, "action_ref", action_id, actor=who, revision=revision_value, digest=digest)
    versions = await invoke(request, "action_versions", action_id, actor=who)
    item = next((item for item in versions["items"] if item["revision"] == revision_value), None)
    if item is None:
        reject("not_found", "Action version not found")
    if digest is not None and digest != item["digest"]:
        reject("reference_conflict", "Action digest does not match the fixed reference")
    return {**item, "ref": value}


@router.get("/jobs/actions/{action_id}")
async def action_detail(action_id: str, request: Request):
    who = await actor(request, "action", "read")
    return contracts.validate_object("action", await invoke(request, "get", "action", action_id, actor=who))


@router.put("/jobs/actions/{action_id}")
async def action_update(action_id: str, request: Request):
    who = await actor(request, "action", "updateConfig")
    value = await _action_write_body(request)
    return write_result(await invoke(request, "write", "action", action_id, value, actor=who))


@router.post("/jobs/actions/{action_id}/operations")
async def action_control(action_id: str, request: Request):
    who = await actor(request)
    value = await body(request)
    fields(value, {"operation", "expectedRevision", "expectedStateRevision", "requestKey", "parameters"},
           {"operation", "expectedRevision", "expectedStateRevision", "requestKey", "parameters"})
    contracts.check_operation("action", value["operation"])
    revision(value["expectedRevision"])
    revision(value["expectedStateRevision"], "expectedStateRevision")
    contracts.check_request_key(value["requestKey"])
    if not isinstance(value["parameters"], dict):
        reject("invalid_schema", "parameters must be an object")
    who = await actor(request, "action", "lifecycle")
    return write_result(await invoke(request, "control", "action", action_id, value, actor=who))


@router.delete("/jobs/actions/{action_id}")
async def action_retire(action_id: str, request: Request):
    who = await actor(request, "action", "lifecycle")
    value = await body(request)
    fields(value, {"expectedRevision", "expectedStateRevision", "requestKey", "parameters"},
           {"expectedRevision", "expectedStateRevision", "requestKey"})
    revision(value["expectedRevision"])
    revision(value["expectedStateRevision"], "expectedStateRevision")
    contracts.check_request_key(value["requestKey"])
    parameters = value.get("parameters", {})
    if not isinstance(parameters, dict):
        reject("invalid_schema", "parameters must be an object")
    control = {**value, "operation": "retire", "parameters": parameters}
    return write_result(await invoke(request, "control", "action", action_id, control, actor=who))


@router.post("/jobs/actions/{action_id}/aliases")
async def action_alias_create(action_id: str, request: Request):
    who = await actor(request, "action", "updateConfig")
    value = await body(request)
    fields(value, {"legacyId", "revision", "digest", "source", "requestKey"},
           {"legacyId", "revision", "digest", "source", "requestKey"})
    revision(value["revision"])
    contracts.check_request_key(value["requestKey"])
    return write_result(await invoke(request, "register_action_alias", value["legacyId"], action_id,
                                     value["revision"], value["digest"], source=value["source"],
                                     actor=who, request_key=value["requestKey"]))


@router.post("/job-templates/{template_id}/instantiate")
async def instantiate_time_template(template_id: str, request: Request):
    who = await actor(request, "template", "use")
    value = await body(request)
    fields(value, {"expectedRevision", "requestKey", "activate"}, {"expectedRevision", "requestKey"})
    revision(value["expectedRevision"])
    contracts.check_request_key(value["requestKey"])
    if type(value.get("activate", False)) is not bool:
        reject("invalid_schema", "activate must be boolean")
    return write_result(await invoke(request, "instantiate_template", template_id, value, actor=who), created=True)


@router.get("/event-creators/providers")
async def creator_providers(request: Request):
    who = await actor(request, "event_creator", "read")
    return await invoke(request, "creator_providers", actor=who)


@router.post("/event-creators/preview")
async def creator_preview(request: Request):
    who = await actor(request, "event_creator", "read")
    value = await body(request)
    fields(value, {"definition", "at", "count"}, {"definition", "at", "count"})
    return await invoke(request, "preview_creator", value, actor=who)


@router.post("/event-creators/time-input/resolve")
async def resolve_time_input(request: Request):
    who = await actor(request, "event_creator", "read")
    value = await body(request)
    fields(value, {"value", "timezone"}, {"value"})
    if (not isinstance(value["value"], str) or not 1 <= len(value["value"]) <= 256
            or "timezone" in value and (not isinstance(value["timezone"], str) or not 1 <= len(value["timezone"]) <= 64)):
        reject("invalid_schema", "Invalid time input")
    return await invoke(request, "resolve_time_input", value, actor=who)


@router.get("/event-creators/{creator_id}/references")
async def creator_references(creator_id: str, request: Request):
    who = await actor(request, "event_creator", "read")
    return await invoke(request, "creator_references", creator_id, actor=who)


# -- Legacy-shaped Job facade over the canonical authority ------------------
# The designed Jobs page speaks the old shape. These routes accept that shape,
# build an ordinary canonical Job definition from the existing product Actions
# and run it through create_run/admission. They never write the retired JSON
# writer and never create a second executor.


@router.post("/jobs/legacy-create")
async def legacy_job_create(request: Request):
    who = await actor(request, "job", "create")
    data = await body(request)
    from packages.jobs import legacy_job_compat
    service = _service(request)
    key = legacy_job_compat._request_key(request, data)
    if data.get("kind") == "session-broadcast" and data.get("schedule"):
        composed = await asyncio.to_thread(legacy_job_compat.compose_broadcast,
                                           service, who, data, key)
        job = await asyncio.to_thread(service.get, "job", composed["result"]["jobId"], actor=who)
        binding = legacy_job_compat.load_binding(service, job["id"]) or {}
        return {"ok": True, "job": legacy_job_compat.job_view(service, job,
                                                              legacy=binding.get("fields"), actor=who)}
    if data.get("kind") in {"scheduled-task", "session-message"} and data.get("schedule"):
        # Scheduled kinds use the accepted scheduler adapter: it builds the
        # Job + time Creator + Trigger graph and keeps the delivery runtime.
        from packages.scheduler import canonical_tasks
        payload = legacy_job_compat.scheduler_payload(data)
        operation = await asyncio.to_thread(canonical_tasks.create, service, payload,
                                            actor=who, request_key=key)
        job = await asyncio.to_thread(service.get, "job", operation["result"]["jobId"], actor=who)
        await asyncio.to_thread(legacy_job_compat.save_binding, service, job["id"],
                                data.get("kind"), {"kind": data.get("kind"),
                                                   "target": {"sessionId": payload["target_session_id"]},
                                                   "text": payload["text"],
                                                   "schedule": payload["schedule"]})
        return {"ok": True, "job": legacy_job_compat.job_view(
            service, job, legacy=legacy_job_compat.load_binding(service, job["id"])["fields"], actor=who)}
    definition, legacy = legacy_job_compat.definition_for(service, who, data)
    created = await invoke(request, "write", "job", None,
                           {"definition": definition, "expectedRevision": 0,
                            "requestKey": key}, actor=who)
    job = created["result"]["object"]
    await asyncio.to_thread(legacy_job_compat.save_binding, service, job["id"],
                            data.get("kind"), legacy)
    if data.get("enabled", True) and job["state"] == "draft":
        job = (await invoke(request, "control", "job", job["id"], {
            "operation": "enable", "expectedRevision": job["configRevision"],
            "expectedStateRevision": job["stateRevision"], "parameters": {},
            "requestKey": legacy_job_compat.stage_key(key, "enable")},
            actor=who))["result"]["object"]
    if data.get("kind") == "background-process" and job["state"] == "enabled":
        # A legacy background process starts immediately. The Run carries its own
        # deterministic key, so a lost response is resolved by re-issuing the same
        # creation request (the Job and Run operations replay) and a cold reader
        # can query the original Run instead of starting a second one.
        try:
            run = await invoke(request, "create_run", job["id"], {
                "expectedRevision": job["configRevision"],
                "requestKey": legacy_job_compat.stage_key(key, "start"),
                "input": {},
                "cause": {"type": "manual"},
            }, actor=who)
        except Exception as exc:
            # Draft/enable already committed. Report the exact phase and keep the
            # receipt queryable; never claim the process was started.
            reason = getattr(exc, "code", None) or type(exc).__name__
            view = legacy_job_compat.job_view(service, job, legacy=legacy, actor=who)
            view["status"] = "scheduled"
            return {"ok": True, "job": view, "phase": "enabled",
                    "start": {"status": "rejected", "code": reason}}
        view = legacy_job_compat.job_view(service, job, legacy=legacy, actor=who)
        view["runCount"] = 1
        view["status"] = "starting"
        return {"ok": True, "phase": "started", "job": view,
                "run": {"status": run.get("status"), "result": run.get("result")}}
    return {"ok": True, "job": legacy_job_compat.job_view(service, job, legacy=legacy, actor=who)}


@router.get("/jobs/legacy-list")
async def legacy_job_list(request: Request):
    who = await actor(request, "job", "read")
    from packages.jobs import legacy_job_compat
    from packages.jobs import api as legacy_api
    service = _service(request)
    try:
        # The designed page must keep showing Jobs that still live in the old
        # registry; canonical rows are merged by explicit jobId only.
        legacy_page = await legacy_api.list_jobs(request=request)
        items = [{**row, "enabled": False, "retirementStatus": "Retired",
                  "retirementReason": "Legacy execution retired; create a new Job",
                  "unified": {**(row.get("unified") or {}), "compatibility": True}}
                 for row in legacy_page.get("jobs") or []]
    except Exception:
        items = []
    seen = {row.get("jobId") for row in items}
    for item in legacy_job_compat.canonical_jobs(service, who):
        if item["id"] in seen:
            continue
        binding = legacy_job_compat.load_binding(service, item["id"]) or {}
        view = legacy_job_compat.job_view(service, item, legacy=binding.get("fields"), actor=who)
        view["pinned"] = legacy_job_compat.get_pin(service, item["id"])
        items.append(view)
    from packages.jobs.maintenance import dispositions
    inventory = dispositions(service.root.parent, service=service)
    seen = {row.get("jobId") for row in items}
    for preserved in inventory["items"]:
        identity = preserved["originalId"] or preserved["id"]
        if identity in seen or preserved.get("deleted"):
            continue
        items.append({"jobId": identity, "name": identity, "kind": "invalid",
                      "status": "failed", "enabled": False, "pinned": False,
                      "source": {"type": "system"}, "target": {"sessionId": None},
                      "schedule": [], "runCount": 0, "createdAt": None, "updatedAt": None,
                      "retirementStatus": "Invalid", "retirementReason": ", ".join(preserved["reasonCodes"]),
                      "preservedSource": preserved,
                      "unified": {"compatibility": True}})
        seen.add(identity)
    return {"ok": True, "jobs": items, "pinMetadataStatus": "available"}


@router.get("/jobs/legacy/{job_id}")
async def legacy_job_detail(job_id: str, request: Request):
    who = await actor(request, "job", "read")
    from packages.jobs import legacy_job_compat
    from packages.jobs import api as legacy_api
    service = _service(request)
    legacy_row = legacy_api._find_job(job_id)
    if legacy_row is not None:
        return {"ok": True, "job": legacy_api._view(legacy_row)}
    job = legacy_job_compat.get_canonical(service, job_id, who)
    if job is None:
        raise KeyError(job_id)
    binding = legacy_job_compat.load_binding(service, job_id) or {}
    return {"ok": True, "job": legacy_job_compat.job_view(service, job,
                                                          legacy=binding.get("fields"), actor=who)}


@router.patch("/jobs/legacy/{job_id}")
async def legacy_job_patch(job_id: str, request: Request):
    who = await actor(request, "job", "updateConfig")
    data = await body(request)
    from packages.jobs import legacy_job_compat
    service = _service(request)
    job = legacy_job_compat.get_canonical(service, job_id, who)
    if job is None:
        raise KeyError(job_id)
    binding = legacy_job_compat.load_binding(service, job_id) or {}
    kind = binding.get("kind") or "background-process"
    definition = legacy_job_compat.patch_definition(service, job, data, kind)
    key = legacy_job_compat._request_key(request, data)
    if set(data) - {"paused", "enabled"}:
        job = (await invoke(request, "write", "job", job_id, {
            "definition": definition, "expectedRevision": job["configRevision"],
            "requestKey": key}, actor=who))["result"]["object"]
    operation = None
    if data.get("paused") is True and job["state"] == "enabled":
        operation = "pause"
    elif data.get("paused") is False and job["state"] == "paused":
        operation = "resume"
    elif data.get("enabled") is False and job["state"] == "enabled":
        operation = "disable"
    elif data.get("enabled") is True and job["state"] == "draft":
        operation = "enable"
    if operation:
        job = (await invoke(request, "control", "job", job_id, {
            "operation": operation, "expectedRevision": job["configRevision"],
            "expectedStateRevision": job["stateRevision"], "parameters": {},
            "requestKey": key + ":lifecycle"}, actor=who))["result"]["object"]
    return {"ok": True, "job": legacy_job_compat.job_view(service, job,
                                                          legacy=binding.get("fields"), actor=who)}


@router.post("/jobs/{job_id}/run-now")
async def legacy_job_run_now(job_id: str, request: Request):
    who = await actor(request, "job", "execute")
    from packages.jobs import legacy_job_compat
    service = _service(request)
    job = legacy_job_compat.get_canonical(service, job_id, who)
    if job is None:
        raise KeyError(job_id)
    key = legacy_job_compat._request_key(request)
    receipt = await invoke(request, "create_run", job_id, {
        "expectedRevision": job["configRevision"], "requestKey": key,
        "input": {}, "cause": {"type": "manual"}}, actor=who)
    binding = legacy_job_compat.load_binding(service, job_id) or {}
    return {"ok": True,
            "job": legacy_job_compat.job_view(service, job, legacy=binding.get("fields"), actor=who),
            "run": {"status": receipt.get("status"), "result": receipt.get("result")}}


@router.put("/jobs/legacy/{job_id}/pin")
async def legacy_job_pin(job_id: str, request: Request):
    who = await actor(request, "job", "updateConfig")
    value = await body(request)
    if set(value) != {"pinned"} or type(value.get("pinned")) is not bool:
        reject("invalid_schema", "pinned must be the only boolean field")
    from packages.jobs import legacy_job_compat
    service = _service(request)
    job = legacy_job_compat.get_canonical(service, job_id, who)
    if job is None:
        raise KeyError(job_id)
    # Canonical presentation pin: the legacy writer fence is closed after
    # cutover, so the pin lives beside the canonical document instead.
    await asyncio.to_thread(legacy_job_compat.set_pin, service, job_id, value["pinned"])
    binding = legacy_job_compat.load_binding(service, job_id) or {}
    view = legacy_job_compat.job_view(service, job, legacy=binding.get("fields"), actor=who)
    view["pinned"] = value["pinned"]
    return {"ok": True, "job": view}


@router.delete("/jobs/{job_id}")
async def legacy_job_delete(job_id: str, request: Request):
    """Canonical retirement, never a fake record removal.

    History stays readable and an active Run blocks retirement, exactly like the
    canonical lifecycle; the response never claims the record disappeared.
    """
    who = await actor(request, "job", "lifecycle")
    from packages.jobs import legacy_job_compat
    service = _service(request)
    job = legacy_job_compat.get_canonical(service, job_id, who)
    if job is None:
        raise KeyError(job_id)
    runs = await invoke(request, "runs", job_id, actor=who, cursor=None, limit=100)
    active = [row for row in runs.get("items", [])
              if row.get("state") in {"queued", "running", "retry_wait", "paused_at_boundary",
                                      "cancelling", "settling", "uncertain"}]
    if active:
        reject("reference_conflict", "Active Runs must finish or be cancelled before retirement")
    retired = (await invoke(request, "control", "job", job_id, {
        "operation": "retire", "expectedRevision": job["configRevision"],
        "expectedStateRevision": job["stateRevision"], "parameters": {},
        "requestKey": legacy_job_compat._request_key(request) + ":retire"}, actor=who))["result"]["object"]
    return {"ok": True, "retired": True, "jobId": job_id,
            "job": legacy_job_compat.job_view(service, retired,
                                              legacy=(legacy_job_compat.load_binding(service, job_id) or {}).get("fields"), actor=who)}


@router.post("/jobs/{job_id}/cancel")
async def legacy_job_cancel(job_id: str, request: Request):
    """Cancel the active Runs of one canonical Job with their own CAS."""
    who = await actor(request, "job", "execute")
    from packages.jobs import legacy_job_compat
    service = _service(request)
    job = legacy_job_compat.get_canonical(service, job_id, who)
    if job is None:
        raise KeyError(job_id)
    runs = await invoke(request, "runs", job_id, actor=who, cursor=None, limit=100)
    cancelled, failed = [], []
    for row in runs.get("items", []):
        if row.get("state") not in {"queued", "running", "retry_wait", "paused_at_boundary"}:
            continue
        try:
            # Re-read the Run: the list projection can be older than the
            # admission transition, and cancel must use the observed revision.
            current = await invoke(request, "get_run", row["runId"], actor=who)
            # The post-commit path performs the owned-tree cleanup; plain
            # control_run only persists the cancelling intent and would leave
            # the Run waiting forever.
            method = ("control_run_postcommit"
                      if callable(getattr(service, "control_run_postcommit", None))
                      else "control_run")
            await invoke(request, method, row["runId"], {
                "operation": "cancel",
                "expectedStateRevision": current.get("stateRevision", 1),
                "requestKey": legacy_job_compat._request_key(request) + ":cancel:" + row["runId"],
            }, actor=who)
            cancelled.append(row["runId"])
        except Exception as exc:
            # A partial batch must never be reported as a full success.
            failed.append({"runId": row["runId"],
                           "code": getattr(exc, "code", "cancel_failed")})
    if failed:
        return JSONResponse(status_code=409, content={
            "ok": False,
            "error": {"code": "reference_conflict",
                      "message": "Some Runs could not be cancelled"},
            "cancelled": cancelled, "failed": failed,
        })
    return {"ok": True, "cancelled": cancelled, "failed": failed}


for _path, _kind in KINDS.items():
    definition_routes("/" + _path, _kind)


@router.get("/jobs/{job_id}/runs")
async def runs(job_id: str, request: Request):
    who = await actor(request, "job", "read")
    return page_result("run", await invoke(request, "runs", job_id, actor=who, **page(request)), runtime=True)


def run_body(value):
    fields(value, {"expectedRevision", "requestKey", "input", "cause", "rerunOf"},
           {"expectedRevision", "requestKey", "input", "cause"})
    revision(value["expectedRevision"])
    contracts.check_request_key(value["requestKey"])
    if value["cause"] != {"type": "manual"} or not isinstance(value["input"], dict):
        reject("invalid_schema", "Manual cause and object input required")
    if "rerunOf" in value and (not isinstance(value["rerunOf"], str) or not 1 <= len(value["rerunOf"]) <= 128):
        reject("invalid_schema", "Invalid rerunOf")
    return value


@router.post("/jobs/{job_id}/runs")
async def create_run(job_id: str, request: Request):
    who = await actor(request, "job", "execute")
    value = run_body(await body(request))
    return write_result(await invoke(request, "create_run", job_id, value, actor=who))


@router.get("/job-runs/{run_id}")
async def run_detail(run_id: str, request: Request):
    who = await actor(request, "job", "read")
    return contracts.validate_record("run", await invoke(request, "get_run", run_id, actor=who))


@router.get("/job-runs/{run_id}/attempts/{attempt_id}/log")
async def process_log(run_id: str, attempt_id: str, request: Request):
    who = await actor(request, "job", "read")
    if set(request.query_params) - {"offset", "limit"} or any(
            len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        reject("invalid_schema", "Log read accepts only one offset and limit")
    values = {}
    for key, default in (("offset", "0"), ("limit", "65536")):
        raw = request.query_params.get(key, default)
        if not re.fullmatch(r"0|[1-9][0-9]{0,18}", raw):
            reject("invalid_schema", "Log cursor and limit must be decimal integers")
        values[key] = int(raw)
    from packages.jobs.process_logs import bounds
    bounds(**values)
    return await invoke(request, "read_process_log", run_id, attempt_id, actor=who, **values)


@router.get("/job-runs/{run_id}/logs")
async def process_log_references(run_id: str, request: Request):
    who = await actor(request, "job", "read")
    if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        reject("invalid_schema", "Log references accept one cursor and limit")
    return await invoke(request, "process_log_references", run_id, actor=who, **page(request))


@router.post("/job-runs/{run_id}/operations")
async def run_control(run_id: str, request: Request):
    who = await actor(request, "job", "execute")
    value = await body(request)
    fields(value, {"operation", "stepId", "expectedStateRevision", "expectedRevision", "requestKey", "parameters"},
           {"operation", "expectedStateRevision", "requestKey"})
    operation = value["operation"]
    if not isinstance(operation, str) or operation not in {*contracts.RUN_OPERATIONS, "run-again", "clone-and-run"}:
        reject("unsupported_operation", "Unsupported Run operation")
    revision(value["expectedStateRevision"], "expectedStateRevision")
    contracts.check_request_key(value["requestKey"])
    if "stepId" in value and (not isinstance(value["stepId"], str) or not 1 <= len(value["stepId"]) <= 128):
        reject("invalid_schema", "Invalid stepId")
    if operation in {"run-again", "clone-and-run"}:
        if "expectedRevision" not in value:
            reject("invalid_schema", "expectedRevision required")
        revision(value["expectedRevision"])
        if "stepId" in value:
            reject("invalid_schema", "stepId is only supported by retry")
        if operation == "clone-and-run":
            await actor(request, "job", "create")
    elif set(value) & {"expectedRevision", "parameters"}:
        reject("invalid_schema", "Run controls cannot override the frozen plan")
    if "stepId" in value and operation != "retry":
        reject("invalid_schema", "stepId is only supported by retry")
    if "parameters" in value and not isinstance(value["parameters"], dict):
        reject("invalid_schema", "Invalid parameters")
    service = getattr(request.app.state, "job_final_service", None)
    method = "control_run_postcommit" if callable(getattr(service, "control_run_postcommit", None)) else "control_run"
    return write_result(await invoke(request, method, run_id, value, actor=who))


@router.get("/operations")
async def operation_lookup(request: Request):
    who = await actor(request)
    query = dict(request.query_params)
    fields(query, {"operation", "requestKey"}, {"operation", "requestKey"})
    contracts.check_request_key(query["requestKey"])
    if query["operation"] not in {*contracts.OPERATIONS, "run-again"}:
        reject("unsupported_operation", "Unknown operation")
    value = await invoke(request, "operation_for_key", query["operation"], query["requestKey"], actor=who)
    contracts.validate_operation_result(value)
    return value


@router.get("/operations/{operation_id}")
async def operation_detail(operation_id: str, request: Request):
    who = await actor(request)
    value = await invoke(request, "operation", operation_id, actor=who)
    contracts.validate_operation_result(value)
    return value


def _remount_legacy(facade, legacy_routers, facade_prefix):
    """Relocate legacy writes under the facade; legacy reads stay at both.

    ``/api/jobs`` now belongs to the canonical API, so a pre-canonical effectful
    handler must not stay there: it is replaced by a 410 boundary on the facade
    address.  Reads remain available at their original address as well, because
    the old Job history/listing queries are retained until R6 and moving them
    would break the legacy UI and audit reads for no security gain.
    """
    for old in legacy_routers:
        for route in old.routes:
            path = facade_prefix + route.path.removeprefix("/api/jobs")
            _copy_legacy_route(facade, route, path)


def _is_scheduled_compat_patch(route):
    # Only this adapter can transfer an already-adopted scheduled-task. It
    # authenticates and refuses unbound records before any old writer is used.
    from packages.jobs.api import patch_job
    return route.endpoint is patch_job and set(route.methods) == {'PATCH'}


def _copy_legacy_route(target, route, path):
    """Retain reads and canonical Template adapters; refuse other old writes."""
    template_compat = "/job-templates" in route.path
    if route.methods <= {"GET", "HEAD", "OPTIONS"} or template_compat or _is_scheduled_compat_patch(route):
        target.add_api_route(
            path, route.endpoint, methods=sorted(route.methods),
            name="legacy_" + route.name,
            response_model=route.response_model, status_code=route.status_code,
            tags=["legacy-jobs"], dependencies=route.dependencies,
            responses=route.responses, summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            response_class=route.response_class,
            response_model_include=route.response_model_include,
            response_model_exclude=route.response_model_exclude,
            response_model_by_alias=route.response_model_by_alias,
            response_model_exclude_unset=route.response_model_exclude_unset,
            response_model_exclude_defaults=route.response_model_exclude_defaults,
            response_model_exclude_none=route.response_model_exclude_none,
            callbacks=route.callbacks, openapi_extra=route.openapi_extra,
            include_in_schema=route.include_in_schema,
            deprecated=True, route_class_override=type(route),
        )
    else:
        target.add_api_route(
            path, retired_legacy_write, methods=sorted(route.methods),
            name="retired_legacy_" + route.name,
            tags=["retired"], deprecated=True,
        )


def install(app, *legacy_routers):
    """Serve legacy reads, retire legacy writes, keep canonical authoritative.

    The pre-canonical routers used to be mounted twice, at ``/api/jobs`` and at
    ``/api/legacy/jobs``.  After the R5 retirement:

    * ``/api/legacy/jobs`` is the only facade address a legacy writer may use,
      and its effectful routes answer 410 after authenticating the caller.
    * old reads keep both addresses, because Job history/listing queries are
      retained until R6 and moving them would break audit reads for no gain.
    * P6 adopted scheduled-task PATCH transfers through the canonical owner at
      both addresses; it cannot restore an unbound JSON writer.
    * P6 Template CRUD/instantiate keeps its original shape at both addresses,
      owned by this module with no legacy runtime fallback.
    * a canonical path always wins; an old write address that canonical does not
      serve answers 410 instead of a bare 404, so a stale client learns it is
      retired rather than that the path never existed.
    """
    from packages.jobs.retention import install as install_retention
    install_retention(app)
    from packages.automation.authorization import canonical_path as is_canonical
    canonical = [API_PREFIX + route.path for route in router.routes
                 if getattr(route, "methods", None)]

    def canonical_serves(path):
        """Whether a canonical route covers this path first.

        Static legacy names such as ``action-jobs`` must never be read as a
        canonical object ID, so a static legacy path is never suppressed.  A
        dynamic legacy path (``/api/jobs/{job_id}``) does defer to the canonical
        object route, whose namespace owns job IDs.
        """
        if "{" not in path:
            return any(item == path for item in canonical)
        return any(re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", item), path)
                   for item in canonical)

    facade = APIRouter(tags=["legacy-jobs"])
    _remount_legacy(facade, legacy_routers, "/api/legacy/jobs")
    app.include_router(facade)

    retained = APIRouter(tags=["legacy-jobs"])
    for old in legacy_routers:
        for route in old.routes:
            suffix = route.path.removeprefix("/api/jobs")
            retired = _is_retired_legacy_route(suffix, set(route.methods))
            if _is_scheduled_compat_patch(route):
                _copy_legacy_route(retained, route, route.path)
                continue
            if retired:
                if suffix in {"", "/"} or is_canonical(route.path) or canonical_serves(route.path):
                    continue
                _retired_route(app, route.path, set(route.methods))
            elif not canonical_serves(route.path):
                _copy_legacy_route(retained, route, route.path)
    app.include_router(retained)
    app.include_router(router, prefix=API_PREFIX)
    from packages.actions.group_api import router as group_router
    app.include_router(group_router, prefix=API_PREFIX)
    # The catalog cannot replace the existing /api/actions legacy registry.
    # Keep its original URL while sharing the new /api/jobs/actions handler.
    aliases = APIRouter(route_class=ContractRoute)
    aliases.add_api_route("/actions", action_catalog, methods=["GET"])
    for route in router.routes:
        if route.path != "/jobs/actions":
            aliases.routes.append(route)
    app.include_router(aliases, prefix=DEPRECATED_PREFIX, deprecated=True)
