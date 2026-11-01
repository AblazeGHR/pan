"""Canonical tool catalog. Legacy tools remain registered by server.py until R5."""
from pydantic import StrictInt, StrictStr

from packages.jobs.final_client import CanonicalClient, RESOURCES, TOOL_PREFIXES, RUN_OPERATIONS


def register_final_tools(mcp, transport):
    client = CanonicalClient(transport)
    names = []

    def register(name, function, description):
        mcp.tool(name=name, description=description)(function)
        names.append(name)

    def resource_tools(resource):
        prefix = TOOL_PREFIXES[resource]

        def listing(cursor: StrictStr | None = None, limit: StrictInt = 100) -> dict:
            return client.call(resource, "list", cursor=cursor, limit=limit)

        def get(object_id: StrictStr) -> dict:
            return client.call(resource, "get", object_id=object_id)

        def create(definition: dict, request_key: StrictStr, expected_revision: StrictInt = 0) -> dict:
            return client.call(resource, "create", body={"definition": definition,
                "requestKey": request_key, "expectedRevision": expected_revision})

        def update(object_id: StrictStr, definition: dict, expected_revision: StrictInt, request_key: StrictStr) -> dict:
            return client.call(resource, "update", object_id=object_id, body={"definition": definition,
                "expectedRevision": expected_revision, "requestKey": request_key})

        def retire(object_id: StrictStr, expected_revision: StrictInt, expected_state_revision: StrictInt,
                   request_key: StrictStr) -> dict:
            return client.call(resource, "retire", object_id=object_id, body={"expectedRevision": expected_revision,
                "expectedStateRevision": expected_state_revision, "requestKey": request_key})

        def control(object_id: StrictStr, operation: StrictStr, expected_revision: StrictInt,
                    expected_state_revision: StrictInt, request_key: StrictStr, parameters: dict | None = None) -> dict:
            return client.call(resource, "control", object_id=object_id, body={"operation": operation,
                "expectedRevision": expected_revision, "expectedStateRevision": expected_state_revision,
                "requestKey": request_key, "parameters": {} if parameters is None else parameters})

        for operation, fn in (("list", listing), ("get", get), ("create", create), ("update", update),
                              ("retire", retire), ("control", control)):
            register(prefix + "_" + operation, fn,
                     f"Canonical {resource}: {operation} via /api. Writes return operation receipts; "
                     "accepted is not completed. Keep requestKey and query unknown outcomes; never auto-replay.")

    for resource in RESOURCES:
        resource_tools(resource)

    def runs(job_id: StrictStr, cursor: StrictStr | None = None, limit: StrictInt = 100) -> dict:
        return client.call("jobs", "runs", object_id=job_id, cursor=cursor, limit=limit)

    def run(job_id: StrictStr, expected_revision: StrictInt, request_key: StrictStr,
            input: dict | None = None, rerun_of: StrictStr | None = None) -> dict:
        body = {"expectedRevision": expected_revision, "requestKey": request_key,
                "input": {} if input is None else input, "cause": {"type": "manual"}}
        if rerun_of is not None:
            body["rerunOf"] = rerun_of
        return client.call("jobs", "run", object_id=job_id, body=body)

    def get_run(run_id: StrictStr) -> dict:
        return client.call("job-runs", "get", object_id=run_id)

    register("job_runs", runs, "Canonical Job Run page; cursor/limit, no admission.")
    register("job_run", run, "Canonical manual admission. accepted is not effect success; query the original receipt.")
    register("job_run_get", get_run, "Read canonical Run state by original Run identity.")

    def run_tool(operation):
        if operation in {"run-again", "clone-and-run"}:
            def invoke(run_id: StrictStr, expected_state_revision: StrictInt, expected_revision: StrictInt,
                       request_key: StrictStr, parameters: dict | None = None) -> dict:
                body = {"expectedStateRevision": expected_state_revision, "expectedRevision": expected_revision,
                        "requestKey": request_key}
                if parameters is not None:
                    body["parameters"] = parameters
                return client.call("job-runs", operation, object_id=run_id, body=body)
        elif operation == "retry":
            def invoke(run_id: StrictStr, expected_state_revision: StrictInt, request_key: StrictStr,
                       step_id: StrictStr | None = None) -> dict:
                body = {"expectedStateRevision": expected_state_revision, "requestKey": request_key}
                if step_id is not None:
                    body["stepId"] = step_id
                return client.call("job-runs", operation, object_id=run_id, body=body)
        else:
            def invoke(run_id: StrictStr, expected_state_revision: StrictInt, request_key: StrictStr) -> dict:
                return client.call("job-runs", operation, object_id=run_id, body={
                    "expectedStateRevision": expected_state_revision, "requestKey": request_key})
        name = "job_run_again" if operation == "run-again" else "job_run_" + operation.replace("-", "_")
        register(name, invoke,
                 f"Canonical {operation}. Explicit scope and CAS; query receipts, never infer completion from acceptance.")

    for operation in RUN_OPERATIONS:
        run_tool(operation)

    def operation_get(operation_id: StrictStr) -> dict:
        return client.call("operations", "get", object_id=operation_id)

    def operation_find(operation: StrictStr, request_key: StrictStr) -> dict:
        return client.call("operations", "find", body={"operation": operation, "requestKey": request_key})

    def actions() -> dict:
        return client.call("actions", "list")

    register("job_operation_get", operation_get, "Query an original canonical operation; no replay.")
    register("job_operation_find", operation_find, "Recover original operation by operation + requestKey in the same principal/profile.")
    register("job_action_list", actions, "Read published canonical Actions and pinned Action refs for Job definitions.")
    return tuple(names)
