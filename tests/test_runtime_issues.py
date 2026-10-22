import json

from packages.core.runtime_issues import map_runtime_issue, runtime_issue_schema


# The current worktree did not include the externally referenced latest
# session-errors.jsonl. This is the Codex app-server envelope shape preserved by
# the existing wrapper; the test keeps its raw fields and correlation facts.
CODEX_QUOTA_SAMPLE = {
    "type": "codex.turn_error", "thread_id": "thr-qa", "turn_id": "turn-17",
    "error": {"code": "usage_limit_reached",
               "message": "You've hit your usage limit. Please try again later."},
}


def test_codex_quota_structured_sample_maps_without_guessing():
    result = map_runtime_issue(CODEX_QUOTA_SAMPLE, adapter="codex",
                               metadata={"sessionId": "ses-1", "workerId": "w-1", "generation": 4})
    issue = result["issue"]
    assert issue["classification"] == "codex_quota_exhausted"
    assert issue["confidence"] == "structured"
    assert issue["correlation"] == {"sessionId": "ses-1", "workerId": "w-1", "generation": 4,
                                     "threadId": "thr-qa", "turnId": "turn-17"}
    assert result["event"]["type"] == "runtime.issue.v1"


def test_codex_capacity_is_separate_from_quota():
    quota = map_runtime_issue({"type": "error", "error": {"code": "quota_exhausted"}}, adapter="codex")
    capacity = map_runtime_issue({"type": "error", "error": {"code": "model_capacity_exceeded"}}, adapter="codex")
    assert quota["issue"]["classification"] == "codex_quota_exhausted"
    assert capacity["issue"]["classification"] == "codex_capacity"


def test_cbc_429_is_distinct_from_codex_quota():
    issue = map_runtime_issue({"type": "error", "status": 429, "message": "Too many requests"}, adapter="cbc")
    assert issue["issue"]["classification"] == "cbc_rate_limited"
    assert issue["issue"]["classification"] != "codex_quota_exhausted"


def test_structured_fields_beat_conflicting_text():
    issue = map_runtime_issue({"error": {"code": "model_capacity_exceeded",
                                           "message": "quota exhausted"}}, adapter="codex")
    assert issue["issue"]["classification"] == "codex_capacity"
    assert issue["issue"]["confidence"] == "structured"


def test_tool_failure_does_not_become_quota():
    issue = map_runtime_issue({"type": "codex.item_error", "item": {
        "type": "commandExecution", "status": "failed", "error": "quota word in tool output"}}, adapter="codex")
    assert issue["issue"]["classification"] == "unknown"


def test_strict_adapter_text_and_unknown():
    assert map_runtime_issue({"message": "Usage limit has been reached"}, adapter="codex")["issue"]["classification"] == "codex_quota_exhausted"
    assert map_runtime_issue({"message": "429 quota exceeded"}, adapter="codex")["issue"]["classification"] == "unknown"
    assert map_runtime_issue({"message": "quota exhausted"}, adapter="claude")["issue"]["classification"] == "unknown"


def test_correlation_and_delivery_are_separate_and_dedupable():
    a = map_runtime_issue(CODEX_QUOTA_SAMPLE, adapter="codex", metadata={"taskId": "task-1"},
                          delivery={"observed": True, "classified": True, "persisted": True,
                                    "delivered": False, "deduped": False})
    b = map_runtime_issue(CODEX_QUOTA_SAMPLE, adapter="codex", metadata={"taskId": "task-1"},
                          delivery={"observed": True, "classified": True, "persisted": True,
                                    "delivered": False, "deduped": True})
    assert a["issue"]["issueId"] == b["issue"]["issueId"]
    assert a["issue"]["delivery"]["persisted"] is True
    assert b["issue"]["delivery"]["deduped"] is True


def test_sensitive_fields_are_redacted_and_raw_bounded():
    result = map_runtime_issue({"type": "error", "error": {"code": "quota_exhausted",
        "message": "quota exhausted", "access_token": "secret"}, "extra": "x" * 10000}, adapter="codex")
    raw = result["issue"]["observation"]["raw"]
    assert raw["error"]["access_token"] == "[redacted]"
    assert len(raw["extra"]) <= 2049


def test_schema_is_versioned_and_exposes_three_layers():
    schema = runtime_issue_schema()
    assert schema["version"] == 1
    assert schema["eventType"] == "runtime.issue.v1"
    assert set(schema["delivery"]) == {"observed", "classified", "persisted", "delivered", "deduped"}


def test_session_error_record_carries_projection_without_changing_kind():
    from packages.core import session_error_log as log
    prepared = log._prepare_record(kind="native_operation_error", error_text="native failed",
        error_text_source="cli.error", terminal=False, session_id="ses-1", adapter="codex",
        runtime_issue=map_runtime_issue(CODEX_QUOTA_SAMPLE, adapter="codex"))
    assert prepared[0]["kind"] == "native_operation_error"
    assert prepared[0]["runtimeIssue"]["issue"]["classification"] == "codex_quota_exhausted"
