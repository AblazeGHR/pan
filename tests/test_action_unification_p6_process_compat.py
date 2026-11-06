"""P6 canonical process Job projection through retained old readers."""
from tests.test_job_final_http_service import h


def _process_definition(service):
    action = next(item for item in service.actions(actor={"type": "user", "id": "local"})["items"]
                   if item["id"] == "product_process_logs_v1")
    return {
        "name": "P6 process compatibility",
        "steps": [{"stepId": "process", "actionRef": service.action_ref(action["id"], actor={"type": "user", "id": "local"}),
                    "arguments": {"argv": {"from": "literal", "value": ["python", "-c", "pass"]},
                                  "cwd": {"from": "literal", "value": "."}},
                    "completion": {"contract": "process-exit"}}],
        "retention": {"policyRef": "p6"},
    }


def test_canonical_process_old_view_cancel_history_and_unknown_boundary(h):
    actor = {"type": "user", "id": "local"}
    created = h.service.write("job", None, {"definition": _process_definition(h.service),
        "expectedRevision": 0, "requestKey": "p6-process-compat-create"}, actor=actor)["result"]["object"]
    enabled = h.service.control("job", created["id"], {"operation": "enable",
        "expectedRevision": created["configRevision"], "expectedStateRevision": created["stateRevision"],
        "parameters": {}, "requestKey": "p6-process-compat-enable"}, actor=actor)["result"]["object"]
    run = h.service.create_run(enabled["id"], {"expectedRevision": enabled["configRevision"],
        "requestKey": "p6-process-compat-run", "input": {}, "cause": {"type": "manual"}}, actor=actor)
    view = h.service.legacy_background_view(enabled["id"], actor=actor)
    assert view["jobId"] == enabled["id"] and view["kind"] == "background-process"
    assert view["history"] and view["history"][0]["runId"] == run["runId"]
    cancelled = h.service.legacy_background_cancel(enabled["id"], actor=actor,
        request_key="p6-process-compat-cancel")
    assert cancelled["cancelResult"]["status"] in {"accepted", "pending", "completed"}
    assert cancelled["jobId"] == enabled["id"]
