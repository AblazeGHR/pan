"""Canonical HTTP client shared by MCP and CLI; no execution or recovery loop."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from . import final_contracts as contracts

RESOURCES = {"jobs": "job", "triggers": "trigger", "event-creators": "event_creator",
             "job-templates": "template"}
TOOL_PREFIXES = {"jobs": "job", "triggers": "trigger", "event-creators": "event_creator",
                 "job-templates": "job_template"}
RUN_OPERATIONS = (*contracts.RUN_OPERATIONS, "run-again", "clone-and-run")
BASE = "/api/final"


def failure(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HTTPTransport:
    """One request only. Never follow redirects or replay an uncertain write."""
    def __init__(self, url, token=None):
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("API URL must be an HTTP(S) origin")
        parsed.port
        self.url = url.rstrip("/")
        self.token = token

    def __call__(self, method, path, body=None):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(self.url + path, method=method, headers=headers,
                          data=None if body is None else json.dumps(body, allow_nan=False).encode("utf-8"))
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
                value = json.load(response)
                if not isinstance(value, dict):
                    return failure("transport_unconfirmed", "Invalid response; query original requestKey; do not replay")
                return value
        except HTTPError as exc:
            try:
                value = json.loads(exc.read())
                return value if isinstance(value, dict) else failure("http_error", f"HTTP {exc.code}")
            except (ValueError, UnicodeError):
                return failure("http_error", f"HTTP {exc.code}")
        except (URLError, TimeoutError, OSError, ValueError):
            return failure("transport_unconfirmed", "Outcome unconfirmed; query the original requestKey; do not replay")


def identifier(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or value in {".", ".."} or any(
            char in value for char in "/\\?#%") or any(ord(char) < 32 for char in value):
        raise contracts.ContractError("invalid_schema", "Invalid object identity")
    return quote(value, safe="")


def revision(value, *, state=False, create=False):
    if type(value) is not int or value < (0 if state or create else 1):
        raise contracts.ContractError("invalid_schema", "Invalid revision")


def fields(body, allowed, required):
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
        raise contracts.ContractError("invalid_schema", "Invalid request fields")


class CanonicalClient:
    def __init__(self, transport):
        self.transport = transport

    def call(self, resource, operation, *, object_id=None, body=None, cursor=None, limit=100):
        """Validate WP01 writes, then forward the original operation envelope."""
        try:
            if body is not None and contracts.definition_bytes(body) > contracts.MAX_DEFINITION_BYTES:
                raise contracts.ContractError("invalid_schema", "Request exceeds contract byte limit")
            return self._call(resource, operation, object_id, body, cursor, limit)
        except contracts.ContractError as exc:
            return {"ok": False, "error": exc.as_dict()}

    def _call(self, resource, operation, object_id, body, cursor, limit):
        if resource not in {*RESOURCES, "job-runs", "operations", "actions"}:
            raise contracts.ContractError("unsupported_operation", "Unknown resource")
        path = BASE + "/" + resource
        if object_id is not None:
            path += "/" + identifier(object_id)
        if operation in {"list", "runs"}:
            if type(limit) is not int or not 1 <= limit <= 100 or (cursor is not None and not isinstance(cursor, str)):
                raise contracts.ContractError("invalid_schema", "Invalid pagination")
            if operation == "runs":
                if resource != "jobs" or object_id is None:
                    raise contracts.ContractError("invalid_schema", "Job identity required")
                path += "/runs"
            elif object_id is not None or resource in {"job-runs", "operations"}:
                raise contracts.ContractError("unsupported_operation", "Unsupported list")
            query = {"limit": limit, **({"cursor": cursor} if cursor is not None else {})}
            return self.transport("GET", path + ("?" + urlencode(query) if resource != "actions" else ""))
        if operation == "get":
            if object_id is None:
                raise contracts.ContractError("invalid_schema", "Identity required")
            return self.transport("GET", path)
        if operation == "find" and resource == "operations":
            fields(body, {"operation", "requestKey"}, {"operation", "requestKey"})
            contracts.check_request_key(body["requestKey"])
            if body["operation"] not in {*contracts.OPERATIONS, "run-again"}:
                raise contracts.ContractError("unsupported_operation", "Unknown operation")
            return self.transport("GET", path + "?" + urlencode(body))
        if resource in RESOURCES and operation in {"create", "update"}:
            if (operation == "create") != (object_id is None):
                raise contracts.ContractError("invalid_schema", "Invalid write identity")
            value = contracts.validate_write(RESOURCES[resource], body, create=operation == "create")
            value.pop("requestDigest")  # server computes the receipt digest, never accepts it as input
            return self.transport("POST" if operation == "create" else "PUT", path, value)
        if object_id is None:
            raise contracts.ContractError("invalid_schema", "Identity required")
        if resource in RESOURCES and operation in {"retire", "control"}:
            required = {"expectedRevision", "expectedStateRevision", "requestKey"}
            if operation == "control":
                required |= {"operation", "parameters"}
            fields(body, required, required)
            revision(body["expectedRevision"])
            revision(body["expectedStateRevision"], state=True)
            contracts.check_request_key(body["requestKey"])
            if operation == "control":
                contracts.check_operation(RESOURCES[resource], body["operation"])
                if not isinstance(body["parameters"], dict):
                    raise contracts.ContractError("invalid_schema", "parameters must be an object")
            return self.transport("DELETE" if operation == "retire" else "POST",
                                  path + ("/operations" if operation == "control" else ""), body)
        if resource == "jobs" and operation == "run":
            fields(body, {"expectedRevision", "requestKey", "input", "cause", "rerunOf"},
                   {"expectedRevision", "requestKey", "input", "cause"})
            revision(body["expectedRevision"])
            contracts.check_request_key(body["requestKey"])
            if body["cause"] != {"type": "manual"} or not isinstance(body["input"], dict):
                raise contracts.ContractError("invalid_schema", "Manual cause and object input required")
            if "rerunOf" in body:
                identifier(body["rerunOf"])
            return self.transport("POST", path + "/runs", body)
        if resource == "job-runs" and operation in RUN_OPERATIONS:
            allowed = {"expectedStateRevision", "requestKey"}
            required = set(allowed)
            if operation == "retry":
                allowed.add("stepId")
            if operation in {"run-again", "clone-and-run"}:
                allowed |= {"expectedRevision", "parameters"}
                required.add("expectedRevision")
            fields(body, allowed, required)
            revision(body["expectedStateRevision"], state=True)
            contracts.check_request_key(body["requestKey"])
            if "expectedRevision" in body:
                revision(body["expectedRevision"])
            if "stepId" in body:
                identifier(body["stepId"])
            if "parameters" in body and not isinstance(body["parameters"], dict):
                raise contracts.ContractError("invalid_schema", "parameters must be an object")
            return self.transport("POST", path + "/operations", {**body, "operation": operation})
        raise contracts.ContractError("unsupported_operation", "Unsupported operation")
