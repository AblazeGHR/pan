"""Canonical HTTP boundary (§10.4).

This module owns transport validation and authorization, not an execution loop.
The host supplies ``app.state.job_final_service`` implementing the service port
below. Missing host bindings fail before admission; they never fall back to an
old payload, dispatcher, or an in-memory imitation of a durable operation.
"""
from __future__ import annotations

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
               "template": "jobTemplate"}


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
    def runs(self, job_id: str, *, actor: Mapping[str, Any], cursor: str | None, limit: int): ...
    def create_run(self, job_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def get_run(self, run_id: str, *, actor: Mapping[str, Any]): ...
    def control_run(self, run_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    async def control_run_postcommit(self, run_id: str, body: dict, *, actor: Mapping[str, Any]): ...
    def operation(self, operation_id: str, *, actor: Mapping[str, Any]): ...
    def operation_for_key(self, operation: str, request_key: str, *, actor: Mapping[str, Any]): ...


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


def _is_retired_legacy_route(suffix: str, methods: set[str]) -> bool:
    """Whether an old facade route was an effectful write.

    Historical reads and the explicit cancel control stay on the legacy facade
    until P6 removes the legacy UI: a legacy Job that still owns a process must
    remain inspectable and cancellable.  Every other old mutation is refused.
    """
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
                                "requestKey", "operation", "definition.steps", "definition.retention"}:
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
        value = contracts.validate_write(kind, raw, create=create)
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
        value = write_body(await body(request), create=True)
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

    for suffix, method, function in [("", "GET", listing), ("", "POST", create),
                                     ("/{object_id}", "GET", detail), ("/{object_id}", "PUT", update),
                                     ("/{object_id}", "DELETE", delete),
                                     ("/{object_id}/operations", "POST", control)]:
        router.add_api_route(base + suffix, function, methods=[method], name=f"canonical_{kind}_{function.__name__}")


@router.get("/jobs/actions")
async def action_catalog(request: Request):
    who = await actor(request)
    return await invoke(request, "actions", actor=who)


@router.get("/event-creators/providers")
async def creator_providers(request: Request):
    who = await actor(request, "event_creator", "read")
    return await invoke(request, "creator_providers", actor=who)


@router.get("/event-creators/{creator_id}/references")
async def creator_references(creator_id: str, request: Request):
    who = await actor(request, "event_creator", "read")
    return await invoke(request, "creator_references", creator_id, actor=who)


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


def _copy_legacy_route(target, route, path):
    """Add a facade copy of a pre-canonical route: reads keep, writes refuse."""
    if route.methods <= {"GET", "HEAD", "OPTIONS"}:
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
    * a canonical path always wins; an old write address that canonical does not
      serve answers 410 instead of a bare 404, so a stale client learns it is
      retired rather than that the path never existed.
    """
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
            if retired:
                if suffix in {"", "/"} or is_canonical(route.path) or canonical_serves(route.path):
                    continue
                _retired_route(app, route.path, set(route.methods))
            elif not canonical_serves(route.path):
                _copy_legacy_route(retained, route, route.path)
    app.include_router(retained)
    app.include_router(router, prefix=API_PREFIX)
    # The catalog cannot replace the existing /api/actions legacy registry.
    # Keep its original URL while sharing the new /api/jobs/actions handler.
    aliases = APIRouter(route_class=ContractRoute)
    aliases.add_api_route("/actions", action_catalog, methods=["GET"])
    for route in router.routes:
        if route.path != "/jobs/actions":
            aliases.routes.append(route)
    app.include_router(aliases, prefix=DEPRECATED_PREFIX, deprecated=True)
