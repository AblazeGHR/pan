# Quota policy safety revision: disabled accounts and Worker/task identity

This revision supersedes the weaker v1 Worker and command identity in
`quota-policy-v0.3.md`. Production effects remain unavailable in this batch.

## Account configuration

`automation/quota-policy.json` adds `disabledAccountIds: []` and
`accountConfigRevision: 0`. Old files default these fields in memory; malformed
values fail closed. IDs are opaque nonempty strings (<=256 chars, no surrounding
whitespace), unique, maximum128. Unknown/offline account IDs may be disabled.
No user's account is a product default. Capacity `excluded` remains separate;
reset, fresh quota, recovery and manual/automatic changes cannot clear disabled.
The configuration path is relative to the owned profile; production writes use
the protected API, not an uncoordinated edit of a live ledger. No production
profile is changed by this implementation/test batch.

User Bearer PUT `/api/automation/quota-policy/disabled-accounts` accepts exactly
`{disabledAccountIds: string[], expectedRevision: integer}`. Invalid fields/types
are400, missing auth401, stale revision409, persistence failure503. GET the existing
policy endpoint to obtain both revisions and current disabled list. A real change
increments the account configuration revision and policy epoch, cancels unclaimed
commands and fences old query Jobs. Clearing/replacing the list is an explicit user
write, never implicit account recovery or enabling automatic mode. Ranking and
future reset selection omit disabled accounts; dispatch rechecks configuration.

## Trusted adapter contract v2

`inventory()` returns `{runtimeEpoch, observedAt, currentAccountId, workers}`.
The epoch is an opaque owner/boot identity, stable within one owned runtime and
different after owner restart. `observedAt` is fresh UTC epoch seconds (<=5s old).
The originating RuntimeIssue correlation must carry the same trusted host epoch;
the parser accepts it only from supplied host metadata, never a provider payload.
Existing producer metadata without that epoch remains diagnostic-only until the
production inventory/producer projection is wired in the next phase.
Each verifiably live Worker supplies:

```json
{
  "sessionId": "opaque-session", "workerId": "opaque-worker", "generation": 2,
  "adapter": "codex", "accountId": "opaque-account", "role": "ta",
  "status": "running", "alive": true,
  "processIdentity": {"pid": 1234, "birthToken": "verified-process-creation-token"},
  "task": {"taskId": "durable-task-id", "executionSeq": 7, "state": "unfinished"}
}
```

The host must derive role from explicit Session role metadata, never name/label.
Process identity is the host's verified retained process identity (Windows raw
creation FILETIME, or equivalent), not an unchecked PID. Task identity must come
from authoritative task/execution lifecycle data. `task:null`, completed/unknown
state, or missing fields cannot become an unfinished task by inference.

Frozen command Workers also carry runtimeEpoch. Restart checks the exact current
live epoch/session/Worker/generation/process; absent/offline targets are skipped,
never spawned. A confirmed restart receipt must include `successor`, a verifiable
live successor Worker with the same epoch/session and a strictly newer generation;
the runtime verifies this against a fresh inventory before accepting the receipt.
An invalid/unknown successor is uncertain, not a reason to repeat restart.

Only a frozen explicit TA that was running with a verified unfinished task can
produce a wake command. The command carries its verified successor and the frozen
task identity. Both planning and immediately-before-dispatch checks require the
current successor to remain running, live and on that same unfinished task. Late
completion, idle, offline, unknown identity or unexpected successor shrink the
eligible set; missing frozen evidence blocks rather than inventing a task. MA
targets require explicit MA role and configured Session ID as well. Role changes
cannot silently promote a target into either role.

Commands carry contractVersion2, expected account configuration revision, runtime
epoch and applicable frozen Worker/task preconditions. Legacy pending commands
without these identities become blocked; recovered dispatching remains uncertain.
Legacy query Jobs lacking v2 metadata are disabled/blocked, never upgraded into a
wake of whatever task happens to be current.

## Remaining production wiring (next phase)

1. Read fresh five-hour/week windows and verified actual account from the existing
   cc-switch bridge; extend its contract if managed-account windows are unavailable.
   Do not read its DB directly or implement a second OAuth/account registry.
2. Project the single Pan owner epoch, retained Worker PID/birth/generation and
   authoritative unfinished task/execution data into inventory. Until this exists,
   identity is unknown and no recovery effect is admitted.
3. Implement only the fixed command operations. At the actual effect admission,
   compare accountConfigRevision/disabled IDs, runtime epoch, process and task under
   the owning business gate, and preserve Pause/Illegal/model/queue checks. Never
   turn restart into start-if-missing or wake into a fresh guessed assignment.
4. Return idempotent correlated receipts and verified successor identities; persist
   unknown effects for explicit reconciliation. No blind retry after timeout.

Core preflight and ledger claim are coordinated locally. They do not constitute
a cross-process atomic transaction with a future external adapter: that adapter
must enforce the supplied CAS preconditions at its actual write. No lock spans
external I/O here. No real provider, account, restart or wake is used in this batch.
