# System quick fields registry v0.1 (2026-10-23)

Status: isolated backend candidate. No main/practical move, no push, no real
service/account/provider operation is part of this batch.

This contract defines the canonical camelCase identity fields shared by Events,
Triggers, Actions, Jobs, RuntimeIssue diagnostics and discovery clients. It is
additive: existing Event payloads, Trigger records, Action releases and business
gates keep their current semantics.

## Field registry

Exactly these ten fields are canonical. Every definition is `type:"string"`,
`optional:true` (an Event may omit it), `nullable:false` (a present value is
never null) and has a bounded value of 1..256 characters without surrounding
whitespace or control characters.

| Field | Source | Sensitive | Action target | Trusted from |
|---|---|---|---|---|
| `sessionId` | Pan Session | no | yes | host |
| `sourceSessionId` | originating Session | no | yes (cross-Session) | host |
| `workspaceId` | workspace | no | no | host |
| `workerId` | Worker runtime | no | no | host |
| `taskId` | external/assigned task | no | no | host |
| `executionId` | Job/Run execution | no | no | host |
| `attemptId` | execution attempt | no | no | host |
| `queueItemId` | Session Queue item | no | no | host |
| `threadId` | adapter-native thread | yes | no | host or structured adapter envelope |
| `turnId` | adapter-native turn | yes | no | host or structured adapter envelope |

`actionTarget:true` means a Session Operation may select the field explicitly
as `system.<field>`. Only `sessionId` and `sourceSessionId` are target fields.
Using one still goes through the existing target existence checks, per-target
serial execution, Pause/Illegal behaviour, owner/revision/generation checks and
lease rules. The registry does not grant cross-Session authority; a Trigger
still needs normal user authorization to be enabled.

`threadId`/`turnId` are marked sensitive because they are provider-native
conversation identities. They are accepted only from host metadata or a
structured adapter envelope, never from free text.

## Event envelope and trust boundary

```json
{
  "id": "evt_...",
  "type": "runtime.issue.v1",
  "schemaVersion": 1,
  "source": {"type": "core", "id": "runtime"},
  "occurredAt": "2026-10-23T00:00:00Z",
  "system": {
    "sessionId": "ses_...",
    "workerId": "worker-...",
    "threadId": "thr-...",
    "turnId": "turn-..."
  },
  "payload": {
    "errorCode": "serverOverloaded",
    "message": "..."
  }
}
```

Rules:

- Only a trusted `core` runtime may attach `system` through the Bus contract.
  A plugin handle cannot publish system identity; a payload cannot create it.
- `payload` remains source-defined and is validated only by the Event schema.
  Payload keys with the same spelling are ordinary data and never replace
  `system`. `canonical_event()` never reads payload to build system fields.
- No identifier is inferred from message text, tool output or error prose.
  Missing identity stays omitted; there is no `unknown` session/target fallback.
- RuntimeIssue correlation is host metadata first. Only `threadId`/`turnId` may
  additionally come from a structured adapter envelope. A provider envelope's
  `session_id` is not promoted to Pan `sessionId`.
- If host metadata supplies both a canonical name and an alias with different
  values (`sessionId` vs `session_id`), mapping raises `SystemFieldError`
  (`Conflicting supplied aliases...`) instead of silently choosing one.

## RuntimeIssue durable path

`Service.start` registers the trusted core owner `runtime` for
`runtime.issue.v1`. RuntimeIssue mapping remains owned by
`packages/core/runtime_issues.py`; the Automation service composes two consumers
in the single observer callback:

1. the existing `quota_policy.observe(mapping)` path, unchanged;
2. `Service.observe_runtime_issue(mapping)`, which re-publishes the mapping's
   Event through `Bus.prepare` with the core `runtime` handle.

The prepared Event is put on the existing bounded 128-slot `Service.ingress`
queue; `ingest_core` then calls the normal `Service.accept` path, so Event Store
capacity, Trigger matching, Run admission, retries, Pause/Illegal/lease gates and
diagnostics are the production path, not a side channel. `Bus.prepare` validates
the RuntimeIssue payload schema and only the core handle may attach
`system`; quota consumption still receives the original mapping even if Event
ingress is full or refuses it.

Refusals are observable in the bounded Bus diagnostics (`runtime-issue-ingress`,
`runtime-issue-quota`, and the existing `event-admission`), never retried
unboundedly. Same-loop calls enter the 128-slot ingress directly. Cross-thread
calls first reserve one of at most `ingress.maxsize` callback slots, then use
`call_soon_threadsafe`; each callback releases its reservation in a `finally`,
including schema/queue/closed failures, and `Service.close` resets outstanding
reservations. This bounds pending cross-thread callbacks; it is not a single
global in-flight limit because the ingress queue is a separate bound.
`Service.close` sets `stopping` before shutdown and clears the global observer,
so late Worker errors cannot be admitted after close. Successive Events keep
canonical `system` and Event identity; legacy Events without a system block
retain their previous fact fingerprint.

Worker sampling isolates identity rejection: if host/adapter aliases conflict,
`map_runtime_issue` still raises `SystemFieldError`, but the Worker terminal and
transient sampling wrappers return no RuntimeIssue and continue writing the
original diagnostic JSONL/report with the original status. No Event is produced,
so an ambiguous identity cannot become an executable Action target.

## Input aliases and canonical storage

Input accepts only the snake_case aliases listed by the registry:
`session_id`, `source_session_id`, `workspace_id`, `worker_id`, `task_id`,
`execution_id`, `attempt_id`, `queue_item_id`, `thread_id`, `turn_id`.
No arbitrary aliases such as `targetSession` are accepted. Equal canonical and
alias values are idempotent; conflicting duplicates fail closed.

`Service.accept` and `EventStore.append` canonicalize before dedupe and commit.
An Event ID reused with different canonical `system` is a different fact and is
rejected. `fact_identity` therefore includes `system` alongside
`type/schemaVersion/source/payload`. Stored Event rows contain only canonical
camelCase names. There is no bulk migration of historical rows.

## Discovery API

`GET /api/automation/system-fields` is read-only, unauthenticated and available
before the Automation runtime starts, like `/api/automation/schema`. It returns:

```json
{
  "version": 1,
  "canonicalOnly": true,
  "fields": [
    {
      "name": "sessionId",
      "type": "string",
      "description": "Pan Session identity associated with the Event",
      "source": "session",
      "optional": true,
      "nullable": false,
      "sensitive": false,
      "actionTarget": true,
      "targetKind": "session",
      "crossSession": false,
      "requiresOwnerCheck": true,
      "trusted": true,
      "trustedFrom": "host",
      "availableIn": ["event", "trigger", "action", "job", "gui"],
      "aliases": ["session_id"]
    }
  ],
  "aliases": {"session_id": "sessionId"},
  "actionTargets": ["sessionId", "sourceSessionId"],
  "trust": {"system": "...", "payload": "...", "inference": "..."},
  "limits": {"fields": 10, "maxValueLength": 256, "maxSystemBytes": 4096}
}
```

`GET /api/automation/schema` advertises the path under `systemFields`. The MCP
tool `system_fields` returns the same contract. GUI and Agent clients should
render canonical names from this response instead of hardcoding aliases.

## Trigger argument mapping

A Trigger argument may bind a trusted system field:

```json
{
  "arguments": {
    "target": {"from": "system", "path": "sessionId"}
  }
}
```

`path` accepts the canonical name or the explicit `system.<name>` spelling.
Resolution reads only the Event `system` object. A missing field fails the
match visibly (`System field unavailable: ...`); it never falls back to
`payload.sessionId` or to a text-derived value. Bindings are shape-checked when
the Trigger is stored, so unknown system fields are a 400-class configuration
error rather than a later silent Action call.

A Session Operation Action may target the same trusted field:

```json
{
  "execution": {
    "kind": "session_operation",
    "operation": "pause_everything",
    "selector": {"mode": "event", "path": "system.sessionId"}
  }
}
```

Only `system.sessionId` and `system.sourceSessionId` pass Action validation.
`system.workerId`, bare `system`, and unknown fields are rejected. Existing
`payload.*` selectors remain available for backwards compatibility, but they
are payload data and do not gain system-field trust.

## Limits and non-goals

- At most ten system fields per Event; each value 1..256 characters; serialized
  system object at most 4096 bytes by construction.
- No inference, no text scanning, no automatic `sessionId` from `taskId`.
- No new Event publication route, no change to plugin ownership/generation
  admission, no relaxation of Pause/Illegal/lease/owner/revision gates.
- No React UI change in this batch; the discovery contract is the GUI handoff.
- No real provider, account, QQ, OAuth, keyring or private exporter operation.

## Verification (candidate)

All commands ran from this isolated worktree through the repository's isolation
entrypoint (`E:/software/miniforge/python.exe` here), which removes inherited
`PAN_*`, sets an owned `PAN_TEST_HTTP_PORT`, and never uses 8767/8768. The final
combination run is on merged main `c9c9fe92160bd257eb4b7d53b0669fa85aa1cf2a`.

| Command | Result |
|---|---|
| `python scripts/run_tests_isolated.py -- python -m pytest tests/test_worker_runtime_issue_identity.py tests/test_system_fields.py tests/test_runtime_issues.py -q` | 25 passed |
| `python scripts/run_tests_isolated.py -- python -m pytest` on the 22 targeted system/event/automation/plugin/MCP/Action/Job/startup files | 305 passed |
| `python scripts/run_tests_isolated.py -- python -m pytest tests/test_system_fields_http.py -q` | 1 passed; fakeCLI native capacity -> durable `runtime.issue.v1` -> Trigger system argument -> Pause target; `queuePaused=true` |
| `python scripts/run_tests_isolated.py -- python artifacts/system-fields-registry/verify_http.py` | real 127.0.0.1 ephemeral-port GET 200; POST 405; 10 canonical fields; schema pointer present |

The closed-loop evidence is
`artifacts/system-fields-registry/http-closed-loop.json`: Event source
`{"type":"core","id":"runtime"}`, `system.sessionId` equal to the Worker host
Session, observation code `model_capacity_exceeded`/status `429`, Run argument
`targetSessionId` equal to that Session, `targetIds` equal to `[sessionId]`, and
Pause's existing `queuePaused` business state confirmed. No real provider was
contacted.

The consolidated list is `test_worker_runtime_issue_identity.py`,
`test_system_fields.py`, `test_system_fields_http.py`,
`test_runtime_issues.py`,
`test_event_store.py`, `test_automation_core.py`,
`test_event_trigger_reliability.py`, `test_automation_concurrency.py`,
`test_automation_retry_parallel.py`, `test_plugin_events.py`,
`test_plugin_event_lifecycle.py`, `test_automation_http.py`,
`test_event_store_http.py`, `test_mcp_handbook.py`, `test_action_groups.py`,
`test_action_ids.py`, `test_action_persistence.py`,
`test_automation_retry_http.py`, `test_job_action_templates.py`,
`test_job_system_actions.py`, `test_report_action_coexistence.py` and
`test_http_runtime_startup.py`.

Unfinished boundaries: no React/GUI selector, no bulk migration of historical
Event rows, no plugin-provided system identity (host wiring remains future
work), and no real provider/account/OAuth/QQ/keyring/private-exporter test.
