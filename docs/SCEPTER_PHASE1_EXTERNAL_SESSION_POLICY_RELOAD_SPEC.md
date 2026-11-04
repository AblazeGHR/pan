# Scepter Phase 1 Design Spec — External Sessions, Policy Context, Durable Inbox, Reload Generation

Date: 2026-10-01

## 0. Purpose

This document is the authoritative design baseline for the current Scepter Phase 1 work.

It exists because Pan/Scepter agents do NOT have access to the originating ChatGPT conversation or Notion context. Do not infer missing design intent from older prompts. When this document conflicts with earlier task wording, follow this document unless a newer explicit instruction says otherwise.

Current Phase 1 has three tracks:

1. Direct development: External Agent Caller Identity / Policy Context / External Session Adapter.
2. Direct development: Manifest Reload correctness + Generation.
3. Investigation only: whether existing queue_pending/report/worker-consumption can be reused as a durable inbox/mailbox substrate.

The primary architectural goal is to preserve and reuse Pan's existing Session / managed relation / report / queue mechanisms rather than creating parallel orchestration systems.

---

# 1. Core model

## 1.1 Principal

Principal answers: **who is this identity?**

A Principal is a stable identity/security subject. It is NOT a conversation and NOT automatically a Session.

Conceptual fields:

- principal_id
- type
- provider
- external_subject or equivalent stable authenticated identity
- status
- metadata / claims as needed

Potential principal types:

- human
- internal_agent
- external_agent
- service

Do not overbuild a general IAM system in v1.

## 1.2 Principal to Session cardinality

**A Principal may own or control multiple independent Sessions. Do not map every Principal to one global Session.**

This is a hard design rule.

Example:

Principal: ChatGPT caller
- Session A: long-running collaboration between this Chat and TA-1
- Session B: another Chat / separate task
- Session C: temporary investigation

Principal: QQ caller
- Session Q: QQ-specific context

Different ChatGPT conversations, QQ conversations, Slack contexts, etc. MUST NOT enter or share a Session merely because they correspond to the same user/Principal.

Session is the isolation boundary for:

- conversation/history
- managed / managed_by relationships
- report subscriptions
- durable inbox / pending messages
- task context
- provider delivery binding
- session-scoped policy constraints

## 1.3 Session

Session answers: **which persistent collaboration/orchestration context is this?**

A Session may exist without a Pan Worker.

This is compatible with Pan's existing conceptual separation:

- Session = persistent orchestration identity/context
- Worker = disposable execution process

The design should strengthen this separation.

## 1.4 Worker

Worker is an optional execution entity associated with Session behavior when using a WorkerAdapter.

An External Session MUST NOT automatically spawn a Pan Worker.

---

# 2. Session Adapter model

External Agents should be modeled as adapters, but do NOT force them into the exact semantics of existing worker/CLI adapters.

Use a conceptual hierarchy:

SessionAdapter
- WorkerAdapter
  - codex
  - cbc
  - kimi
  - claude
  - ...
- ExternalSessionAdapter
  - chatgpt
  - qq
  - slack
  - webhook / generic external client
  - ...

## 2.1 WorkerAdapter responsibility

WorkerAdapter remains responsible for local/executable worker semantics, for example:

- spawn
- resume
- interrupt
- process lifecycle
- CLI/model execution
- permission mode specific to the CLI worker

Do not pollute WorkerAdapter with provider-delivery-only methods unless there is a real shared abstraction.

## 2.2 ExternalSessionAdapter responsibility

ExternalSessionAdapter is responsible for provider-specific external interaction.

Minimal conceptual surface:

```python
class ExternalSessionAdapter:
    provider: str

    async def resolve_identity(context): ...
    async def deliver(session, message): ...
    async def notify(session, event): ...
    async def health(): ...
```

Exact interface shape may differ after code inspection, but responsibilities should remain:

- external identity resolution / binding
- outbound delivery
- wakeup / notification semantics
- provider health/readiness

Provider-specific behavior belongs here, not in report/mailbox core.

Examples:

### ChatGPT adapter
Potential behavior:
- maintain/bind authenticated external identity/session
- durable inbox remains source of truth
- notification may use MCP Events/webhook later
- provider callback/wakeup is best-effort delivery/wakeup, not durable storage

### QQ adapter
Potential behavior:
- deliver through existing QQ bridge/gateway
- preserve current QQ account/channel routing
- preserve process ownership safety
- possibly use Runtime Reconciler / Service Supervisor later

---

# 3. Report model: reuse existing mechanism

Do NOT create a separate "external report" subsystem.

Report target remains a **Session**.

Desired path:

```
TA / child Session
    ↓ completion report
Target Session
    ↓ existing durable queue/inbox substrate
Target Session Adapter
    ↓
Provider / Worker behavior
```

Examples:

### Worker Session target
report -> target Session pending queue -> existing Worker consumption semantics

### ChatGPT External Session target
report -> target Session durable inbox -> ChatGPT ExternalSessionAdapter -> provider-specific wakeup/delivery

### QQ External Session target
report -> target Session durable inbox -> QQ ExternalSessionAdapter -> QQ delivery

The report subsystem itself should not know whether the target is ChatGPT, QQ, Slack, etc.

A TA that is managed by / subscribed to a specific external Session may report directly to that Session. Another ChatGPT Session or QQ Session belonging to the same Principal MUST NOT receive it.

---

# 4. Durable inbox / mailbox direction

The current design preference is to reuse existing `queue_pending` if the code supports it safely.

Do NOT create a second independent message store unless the investigation proves reuse is structurally unsafe.

Conceptual layering:

```
existing queue_pending / message persistence
            ↓
      durable inbox core
            ↓
  ┌────────────────────┬─────────────────────┐
  │ Worker Queue View  │ Mailbox View        │
  │ auto consumption   │ list/read/ack       │
  │ FIFO/task semantics│ external consumers  │
  └────────────────────┴─────────────────────┘
```

Important:

- Queue and Mailbox are not necessarily identical APIs.
- They should share durable message persistence if feasible.
- Mailbox isolation boundary is Session, NOT Principal.
- External Session may have no Worker but still owns durable inbox state.

## 4.1 Adapter comes after durability

Do NOT route:

report -> provider adapter -> network callback

Prefer:

report/message
    ↓
durable Session inbox
    ↓
ExternalSessionAdapter
    ↓
provider

This ensures:

- ChatGPT callback failure does not lose report
- QQ offline does not lose report
- transient network failure does not lose report
- delivery can retry
- provider-specific wakeup is not the source of truth

## 4.2 Message state distinction

If needed, distinguish:

- queued / persisted
- delivery attempted
- delivered
- read
- acknowledged

Do not conflate provider delivery with mailbox read/ack.

---

# 5. Caller Identity

Current Pan behavior relies heavily on `PAN_AGENT_SESSION_ID` for internal managed-agent identity. That is insufficient for external callers.

Introduce a unified caller-resolution layer.

Conceptual runtime object:

```
CallerContext
- principal_id
- session_id
- source
- auth_method
- claims / metadata as required
```

Possible source values:

- internal_worker
- external_mcp
- http_api
- scheduler
- system

Internal worker path may continue to resolve from `PAN_AGENT_SESSION_ID`, then normalize into CallerContext.

External callers must resolve through authenticated provider/connection context and bind to an appropriate Principal + Session.

Do not equate "missing PAN_AGENT_SESSION_ID" with "anonymous" if another trusted identity mechanism exists.

---

# 6. External Session bootstrap / binding

Do not require the model/client to manually remember to call identity_create then session_create for every connection.

Preferred behavior is an attach/bootstrap path associated with authentication/connection establishment:

external caller
    ↓
resolve authenticated provider subject
    ↓
lookup/create Principal
    ↓
lookup/create or explicitly bind the correct external Session
    ↓
establish CallerContext

Important: because Principal is 1:N Session, "lookup default Session" must not accidentally merge distinct conversations.

The provider adapter or binding layer needs an external conversation/context key or equivalent stable binding so that:

- the same durable external conversation can resume the same Scepter Session when appropriate
- a new independent conversation gets a different Scepter Session
- different providers never silently share Session state

Do not use IP address or transient TCP connection ID as identity.

---

# 7. Policy Context

Caller identity and authorization must be unified.

Conceptual PolicyContext:

```
PolicyContext
- caller
- action
- target
- capability
- source
- resource_scope
```

All privileged MCP/API operations should pass through one authorization entry point.

Hard security invariant:

**Missing or unresolved identity must fail closed for privileged operations.**

Anonymous/public access, if any, must be explicitly allowlisted for harmless endpoints such as health/public metadata/bootstrap.

Do NOT preserve a fail-open path where unresolved caller identity bypasses access checks.

## 7.1 Reuse existing policy inputs in v1

Do not build a full enterprise IAM system in Phase 1.

Reuse and normalize:

- pan_access
- managed / managed_by relationships
- readonly restrictions
- existing session ownership/control semantics

Longer-term policy can become capability-oriented, e.g.:

- session.read
- session.create
- session.assign
- session.manage
- mailbox.read
- mailbox.send
- plugin.manage

But Phase 1 should focus on a single authorization entry point and fail-closed behavior.

---

# 8. External Agent track — implementation scope

This track MAY directly modify code.

## 8.1 Required outcomes

1. Principal model or equivalent stable identity representation.
2. Principal supports 1:N Session relationships.
3. External Session can exist without Pan Worker.
4. Minimal ExternalSessionAdapter abstraction, clearly separated from WorkerAdapter semantics.
5. CallerResolver / CallerContext normalization.
6. Unified authorization entry point for privileged MCP/API operations.
7. Identity missing/unresolved => privileged operation denied.
8. Existing managed/report/queue behavior reused wherever possible.
9. Report target remains Session.
10. Architecture allows provider-specific delivery for external Session targets.
11. Existing internal Worker behavior remains backward compatible.

## 8.2 Non-goals for v1

Do NOT build:

- full RBAC system
- generic OAuth provider framework
- multi-tenant enterprise IAM
- fine-grained filesystem ACL system
- plugin secret permission system
- all provider adapters at production completeness
- MCP Events end-to-end production implementation unless naturally trivial and non-disruptive

## 8.3 Acceptance criteria

- One Principal can own/control multiple isolated Sessions.
- Two Sessions for the same Principal do not share history/report/inbox unless explicitly connected.
- External Session can be created/bound with no Worker.
- No code path auto-spawns Worker solely because a message/report reaches an External Session.
- TA/child Session report can target a specific External Session.
- Existing Worker Session report behavior continues to work.
- Caller identity from internal Worker and external context enters the same normalized policy decision path.
- Missing identity no longer bypasses privileged checks.
- Tests cover identity resolution, fail-closed behavior, Session isolation, and no-worker external Session semantics.

---

# 9. Manifest Reload + Generation track

This track MAY directly modify code and is intentionally parallel to External Agent work.

Current code audit found:

1. Auto-change detection relies on global maximum manifest mtime + file count, which can miss changes to a file whose new mtime remains below the current global max.
2. Reload failure preserves old configuration (good), but API behavior can make it look like reload succeeded because old non-empty config is returned.
3. Existing atomic replacement and last-known-good behavior should be preserved.

## 9.1 Required changes

Use per-file identity/fingerprint.

Acceptable examples:

- path + mtime + size
- per-file content hash
- equivalent deterministic fingerprint

Do not rely only on one global max mtime.

Reload result should explicitly distinguish:

- applied
- unchanged
- rejected

Expose enough diagnostics to know:

- current generation
- attempted generation or candidate version if useful
- errors/rejection reason
- whether last-known-good remains active
- which files changed if practical

Generation must increase only when a new configuration is successfully applied, unless codebase conventions strongly justify a different explicit definition.

## 9.2 Required semantics

Suggested state transition:

detect change
    ↓
parse + validate candidate
    ↓
if invalid:
    rejected
    keep last-known-good
    generation unchanged
    surface errors
else if equivalent:
    unchanged
    generation unchanged
else:
    atomically apply
    generation += 1
    result = applied

Keep current backward compatibility as much as possible, but do not preserve misleading success reporting.

## 9.3 Acceptance criteria

- Editing any watched manifest file is reliably detected.
- Invalid manifest cannot replace last-known-good.
- Invalid reload is reported as rejected, not success.
- Unchanged reload is distinguishable from applied.
- Generation semantics are deterministic and tested.
- Existing atomic reload behavior remains.
- Existing users that do not inspect new fields are not unnecessarily broken.
- Tests specifically reproduce the old max-mtime leak case.

---

# 10. Queue / Durable Inbox investigation track

This track is INVESTIGATION ONLY. Do not modify code.

The goal is to determine whether current `queue_pending` can safely become the durable inbox substrate described above.

Investigate current real code and report exact file paths/functions.

## 10.1 Questions to answer

1. What is `queue_pending` exactly?
   - in-memory/persistent representation
   - storage location
   - serialization
   - item fields
   - ordering guarantees

2. Who writes to it?
   - agent_assign
   - agent_send
   - report completion
   - manager/report subscriptions
   - scheduler/background mechanisms
   - other paths

3. Who consumes it?
   - Worker startup
   - Worker idle transitions
   - specific queue-drain loops
   - any implicit spawn behavior

4. Is there a hard assumption that every pending item must eventually be consumed by a Worker?
   - identify exact functions/guards
   - distinguish structural assumption from current caller behavior

5. Can a Session with no Worker already hold pending items safely?
   - if yes, under what conditions?
   - if no, what is the minimal blocker?

6. How does report_subscribe/completion report route today?
   - target resolution
   - manager relation
   - queue insertion
   - delivery trigger

7. Which existing fields can be reused for mailbox semantics?
   - message/task id
   - payload
   - timestamps
   - source/target
   - state

8. Which new fields are actually necessary?
   Candidate possibilities:
   - message_id
   - type
   - delivery_state
   - read_state
   - acknowledged_at
   Do not add fields merely because they were suggested; assess necessity from code.

9. How can Worker Queue behavior remain unchanged while adding Mailbox View?
   - FIFO
   - assign/send semantics
   - taskId associations
   - worker auto-consumption

10. What minimal code change path would enable:
   report -> External Session durable inbox
   without Worker auto-spawn/auto-consumption?

## 10.2 Required output

Return a concise implementation-oriented report containing:

- current flow diagram
- exact file/function evidence
- blockers
- reusable pieces
- minimal safe refactor path
- regression risks
- test plan

Do not redesign the whole plugin system.
Do not implement changes.

---

# 11. Interaction between tracks

Tracks A and B are directly parallel.

Track C investigation may run in parallel, but code changes to queue/mailbox should wait for its evidence.

Expected convergence:

External identity/session
        + queue investigation
                ↓
External report -> durable external Session inbox
                ↓
Mailbox list/read/ack
                ↓
provider-specific ExternalSessionAdapter delivery
                ↓
later: MCP Events/wakeup

Manifest reload/generation independently enables later:

Plugin manifest identity/schema
        ↓
Capability Registry
        ↓
Runtime Reconciler / Service Supervisor
        ↓
Scepter MCP v2 projection

---

# 12. Architectural invariants

Treat these as hard constraints unless implementation evidence proves one impossible and you explicitly report why.

1. Principal is NOT Session.
2. Principal is 1:N Session.
3. Session is the collaboration/history/report/inbox isolation boundary.
4. External Session may exist without Worker.
5. Worker is optional execution backend, not proof of Session identity.
6. Report targets Session, not provider.
7. Provider-specific behavior belongs in ExternalSessionAdapter.
8. Durable message persistence comes before provider delivery.
9. Mailbox should reuse queue persistence if structurally safe.
10. Identity missing must not bypass privileged authorization.
11. Internal Worker and external caller should normalize into one Caller/Policy pipeline.
12. Preserve existing Pan orchestration semantics unless a change is explicitly required.
13. Prefer minimal migration and backward compatibility over broad rewrites.
