# Quota policy v0.3 foundation

The [safety revision](quota-policy-v0.3-safety.md) supersedes the v1 Worker/command
contract below: persistent user-disabled accounts, fresh runtime/process/task
identity, verified restart successors and guarded wakes are now required.

This batch extends RuntimeIssue diagnostics with durable policy state and existing
generic scheduled quota-query Jobs. Production starts **manual** with an unavailable
adapter. No account scan, quota HTTP request, model request, switch, restart or send
occurs merely by importing/starting Pan. Backend admission is not MA acceptance.

## Real error evidence and classification

`tests/fixtures/runtime_issues/provenance.json` identifies the exact UTF-8 JSONL
source, one-based line, timestamp/event ID and SHA256 of each original line
(excluding newline). The source was read-only `D:/project/Pan/data/logs/session-errors.jsonl`.
Latest actual quota record at extraction was line508/event
`se_9434f64c9f4055ab9a79f92bcf55e77b`,2026-10-22T04:58:06.973789+00:00;
its `error.codexErrorInfo` is **usageLimitExceeded**, without a synthetic code.
The independent latest capacity record was line551/event
`se_1f8e146499db5277a1c9162d32e8ccd4`,2026-10-22T07:12:07.875427+00:00,
with **serverOverloaded**. The fixture preserves provider error, nulls, native
field names, error text/provenance and runtime correlation. It omits history,
raw diagnostics and unrelated usage rather than distributing user task contents.
No credentials were present in the selected provider error. No remote reproduction
was requested or performed. Later appends to the source do not change these hashes.

The parser now recognizes those real Codex enum strings before text. Tool/item
failures remain unknown even if an embedded error looks like quota. Generic quota
or capacity prose does not classify; fallback accepts only narrow adapter messages.
Host correlation copies scalar whitelisted fields, never a Session object/history.

## Authority and persisted state

State is `automation/quota-policy.json`, UTF-8 atomic replace/fsync off the event
loop, under the existing single owned profile lifetime. Defaults are manual,
unknown quota and empty cases/commands/exclusions. Source ledger revision advances
only on actual change. No mutex spans adapter/provider/Session effects.

Trusted Worker error samples feed a bounded128 in-process observer queue consumed
by the existing scheduler tick. `Runtime.observe` also accepts the logical
`runtime.issue.v1` envelope. There is no public issue publication/receipt endpoint.
Ingress before the policy transaction is volatile; this does not claim durable
delivery from the diagnostic JSONL or replay historical samples automatically.
Committed cases, dedup keys, attempts, exclusions, plans and command outcomes survive
restart. The diagnostic source JSONL remains the original evidence, not a replay log.

Dedup uses Session/Worker/generation + task/execution/queue identity + classification;
an error frame and its terminal projection count once even if their issue IDs differ.
Missing operation identity or stale/unbound Worker account returns unknown. The host
inventory must prove the emitting Codex Worker's account equals the current verified
account; global current-account text alone is insufficient. Record actual account,
Session and Worker/generation separately. Cases/seen/commands/exclusions/retry-account histories each cap4096; capacity
rejects visibly without evicting tombstones. This bounded prototype has no archival UI.

## Decisions

* CBC rate-limit: plan only `cbc_next_model` on the same Session/Worker/generation.
  The trusted adapter owns the user-approved fallback model list and existing
  Pause/Illegal/model checks. The backend does not invent a fallback model.
* Codex capacity: refresh both windows and current identity. If current account has
  remaining five-hour **and** week quota, plan `continue_once` once per account.
  A distinct later capacity observation can exclude that account only after the
  retry has a confirmed receipt. Blocked/unknown retry cannot authorize a switch.
* Quota exhausted skips the capacity retry. Choose another non-excluded account
  whose two windows are known and below100%. Order by nearest future weekly reset,
  then lowest weekly utilization, five-hour utilization and opaque ID as stable tie.
* Unknown/error/stale query, missing reset, boolean/NaN/out-of-range usage or changed
  account during query cannot select/switch. Max observation age300s; reset timestamps
  must be future numeric UTC epoch seconds. An expired window requires a fresh query.
* If no candidate, schedule one reset query Job at the earliest verifiable time at
  which an account's exhausted windows could both be available. For weekly exhaustion
  wait weekly reset. No known future reset means blocked. Capacity exclusion alone
  is not guaranteed to clear at reset, and is never cleared automatically.

## Side effects and uncertainty

`Adapter.query`, `inventory`, `perform(command,key)` are host-only injection points,
with bounded waits10s/5s/10s. Production defaults raise `Unavailable`; unknown quota
is explicit and commands remain blocked. No HTTP payload can install an adapter,
submit a quota snapshot, confirm a command or request an arbitrary operation.

Before `perform`, persist the exact command ID and `dispatching`. Only a matching
`{key,status:"confirmed"}` receipt advances the plan. Exceptions/timeouts/unknown
receipts and recovered dispatching become uncertain and never replay automatically.
Unavailable before effects becomes blocked; explicit host reconciliation is future
work. The adapter must verify expected account and Worker generation at each actual
write, honor existing business gates, and use idempotency/CAS/readback. These are
required interfaces, not claims of a completed real provider adapter.

On confirmed switch, freeze all live Codex Workers and prior running TAs. Restart
non-MA Codex Workers first (including idle, excluding CBC); after every restart has
confirmed, wake only the prior running TAs. After wakes confirm, create the new
account's unique95% monitor and MA one-time resume Job. Persist the confirmed MA Job
ID before planning MA restart last. An unknown restart/wake/Job receipt blocks later
steps; no automatic resend. A newly observed capacity case is separately recorded;
this foundation does **not** yet implement a live MA loop that interposes newly
arriving capacity between all restart steps. Such cases require host orchestration.

## Quota query Jobs and MA boundary

Reuse ordinary `kind:"scheduled-task"`, `action.api:"quota_query"`, the existing
schedule entry evaluation, Job files, public list and runs.jsonl. No new poll loop
or parallel Job registry. Legacy mutable aliases/run-now reject this protected kind.
Query results contain only known/unknown and threshold status; full normalized
account windows are in the policy ledger. Queries never store credentials or raw
adapter errors. An interrupted query Job is uncertain/disabled, not proof of95%.

`monitor95` binds a verified current account, checks every60s on the scheduler tick,
and stops after the first fresh >=95 observation, creating one durable `wake_ma`
command. Reminder means recheck; it is not an implicit switch authorization or
proof of a delivered message. Account change cancels an obsolete monitor. A new
confirmed switch creates a new bound monitor; only one is enabled. A reset Job is
one-shot and refreshes quota before a wake. Switching to manual increments an epoch,
cancels pending commands/Jobs and fences late notices from restarting automatic mode.

## HTTP

* GET `/api/automation/quota-policy`: revision/mode/epoch, safe quota and ranked
  accounts, case/command receipts, Job IDs and adapter availability. Stale quota is
  projected unknown on read. Local loopback management trust, as existing automation.
* PUT same path: user Bearer, exactly `{mode,maSessionId,expectedRevision}`. Unknown
  fields reject400, auth401, invalid mode/stale revision409. Defaults never enable.
* POST `/api/automation/quota-policy/query-jobs`: user Bearer, exactly
  `{kind:"monitor95"|"once",expectedRevision}`. Unknown current account cannot bind a
  monitor; once query may run and report unknown. Jobs are server-identified and
  idempotent per epoch/purpose/bound account. No arbitrary schedules/scripts.

## Remaining boundary

The existing cc-switch bridge has provider quota/default-account APIs, but there
is no verified managed-account five-hour/week query plus coordinated account /
Worker-generation receipt API spanning the full plan. This batch therefore ships
the fail-closed adapter interface and deterministic fake, not a guessed real
switch/restart implementation. No actual OAuth, account switch, model fallback,
MA wake or95% production monitor was executed. React, automatic reconciliation of
uncertain writes, operator archive/unexclude API, distributed/mixed-version writers
and full MA orchestration remain outside this independently reviewable foundation.


### Cross-file recovery boundary

A query Job file and the policy ledger are separate atomic files, not one atomic
transaction. Job IDs derive from profile/epoch/purpose/account. Concurrent creates
converge on that protected ID; a repeated request after Job creation but before
ledger linking repairs the link without a second Job. A stale epoch Job is cancelled
by the scheduler before querying or waking. A command crash after `dispatching`
remains uncertain rather than retrying a possibly completed effect. Generic Job
retention remains the existing registry policy; this batch adds bounded policy
ledgers, not a replacement Job archive/retention system.


Shutdown fences admission and dispatch after every pending query/command claim.
The owner waits for its current bounded scheduler tick before releasing the
profile lock. An already dispatched operation can finish with a receipt; shutdown
does not interpret it as undelivered or blindly repeat it. Reset-wake commands
retain the frozen blocked Codex Worker identities for host recovery review.
