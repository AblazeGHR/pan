# Canonical global Run history and process log retention

User scope: `run_history`. Job definitions, Event Creators, schedules, Triggers,
and original operation/admission/effect identities survive cleanup. The retired
`PUT /api/jobs/settings/completed-retention` continues to return 410. No legacy
executor, cancellation, writer, or cleanup engine is reactivated.

## HTTP contract

Both endpoints require the existing authenticated local User authority. An
agent's delegated single Job grant cannot change or read global maintenance
settings. Unauthenticated requests return 401; trusted agents return
403 `authorization_denied`.

`GET /api/job-maintenance/retention-settings` returns:

```json
{
  "revision": 0,
  "requestScope": "opaque SHA256 of the existing principal/profile/update scope",
  "cleanupScope": "run_history",
  "rules": {
    "completed": {"enabled": false, "days": null},
    "failed": {"enabled": false, "days": null},
    "timed_out": {"enabled": false, "days": null},
    "cancelled": {"enabled": false, "days": null},
    "logs": {"enabled": false, "days": null}
  },
  "configValidity": {"completed": true, "failed": true, "timed_out": true, "cancelled": true, "logs": true},
  "lastRuns": {"completed": null, "failed": null, "timed_out": null, "cancelled": null, "logs": null}
}
```

`PUT /api/job-maintenance/retention-settings` accepts a nonempty patch of the
five known rules. At least one of `enabled` or `days` is required per patch.
The top-level fields are exactly:

```json
{
  "expectedRevision": 0,
  "expectedRequestScope": "64 lowercase hex characters from the authenticated GET",
  "requestKey": "owned-retention-settings-1",
  "rules": {"completed": {"enabled": true, "days": 30}, "logs": {"enabled": true, "days": 7}}
}
```

The response is the existing canonical operation receipt, HTTP 200:
`{operationId,requestKey,status:"completed",objectRefs:[],result:{settings},links:{self}}`.
`result.settings.revision` is the revision actually saved by that operation.
Its self link queries the original receipt. Same key and identical body return
that original receipt with `replayed:true`, including its original revision.
When the PUT response is lost before the client receives an operation ID, use
the read-only `GET /api/operations?operation=update&requestKey=<original-key>`.
The scope remains the existing principal (type/id), canonical profile ID and
`update` operation. This is **not** `/api/jobs/operations`. A successful lookup
returns the original receipt including `result.settings` and its saved revision;
it does not add `replayed`. A missing receipt returns HTTP 404 `not_found`.
The client must preserve an unknown outcome and must not automatically resend.
GET and the original PUT receipt's `result.settings` include `requestScope`, a
stable opaque SHA256 derived from the existing actual principal/profile/update
scope. This does not change operation scope or expose credentials. The client
binds a pending original key to it; on reopen it first authenticates and GETs
settings, and queries the original key only when the scopes match. A different
scope retains the pending record without querying or starting a new write.
401/403 responses do not expose it. It is stable across same-profile cold
restarts and differs for another principal or profile.
`expectedRequestScope` is required on PUT and must match the current actual
principal/profile/update scope before any settings or receipt write. A malformed
or missing value returns 400 `invalid_schema`. A valid but different scope
returns 409 `request_scope_conflict` with zero settings/operation writes. The
original body, including its scope, remains valid for same-scope cold replay and
read-only original-key lookup.
Same key and different body return 409 `idempotency_conflict`; a new key with
stale `expectedRevision` returns 409 `revision_conflict`. Invalid shape, nonbool
enabled, bool/fractional/out-of-range days return 400 `invalid_schema`.
Unavailable service returns 503 `provider_unavailable`; storage failures use
the existing 503 `storage_unavailable` contract. Owner conflicts retain their
existing owner fence errors.

Missing settings bootstrap from the existing `config.jobs` rule sections using
the existing strict parser. Missing enabled defaults to false; missing days to
null. Invalid imported fields produce `configValidity[rule]:false` and disabled
execution. Saving a valid explicit patch repairs that canonical rule. After the
first save the settings are stored per profile in the existing canonical
`final_documents` table; unrelated config and legacy bytes are not rewritten.
The canonical GET is the authoritative UI read path after save.

`days` is null or an integer 1..36500. Disabled rules or null days never clean
anything. Patches preserve omitted fields. Each rule uses its own days and
persistent daily gate. `lastRuns[rule]` is null or
`{at:UTC_epoch_seconds,expired:int,protected:int,errors:[{runId,attemptId,code}]}`.
These observations do not increment the user settings revision. They do not
claim that an operation has cleaned every protected resource.

## Execution and storage

The existing FinalService pulse invokes maintenance; there is no new task or
scheduler. Eligibility and aggregate reduction share the canonical owner
writer transaction. A successful rule admission persists its result and time
in that same transaction. A cold owner honors the same 86400 second gate.

History rules use terminal Run `finishedAt`. Failed Runs count as `timed_out`
only with an original matching Attempt/effect rejected timeout receipt and
confirmed cleanup proof. A configured deadline, message text, or Job projection
is insufficient. Unknown/uncertain/active state, malformed Pin metadata,
effective Pin, pending effect admissions/notifications, recovery holds,
unconfirmed cancellation/ownership, rerun references, old independent execution
generations, and unsettled owned process resources protect cleanup.

An independent terminal notification can be acknowledged only when its Job,
effect, Attempt fence and entire receipt equal the already durable canonical
receipt. Stale, changed and unknown notices remain unacknowledged and protect
the resource. This reuses the existing execution outbox and acknowledgement.

History cleanup removes frozen plans, steps, detailed Attempts/effects, output,
reconciliation copies and acknowledged Run notifications from the normal
aggregate. A compact `expiredRuns` fact preserves identity/digest, original Run
disposition, admission/count, notification IDs, and minimal fenced log records.
Independent execution rows subsequently lose their frozen plan and detailed
result payload; acknowledged payload copies are also reduced. Existing rows
keep the original request fingerprint, Attempt fence and disposition so cold
original operation queries and admission deduplication cannot create an effect.
This reduces stored JSON payloads; it is not secure disk erasure or a promise
to shrink SQLite allocated pages immediately.

Before canonical expiry, the original execution documents receive a fenced
`historyPurge` intent in one execution-store writer transaction, after rechecking
all original Attempt/effect/result identities. This blocks admission of another
Attempt during the interval before payload reduction. Same-fence original
receipt lookup/replay remains a query. If canonical commit rolls back or the
host exits, the next owner pass releases an aborted intent only when the live
original Run/Attempt/receipt and history digest still match. A committed
tombstone keeps the fence until reduction; unknown identities remain protected.

Logs are independent of history. A terminal Run and the original log file
must both be old enough. Only the original controlled `logRef` under the
execution root is eligible, after exact Job/effect/Attempt/fence validation,
owned tree and supervisor exit proof, and live/archive reference checks.
Directory pinning and plain single-link file verification reject path escape,
symlinks/reparse points, hardlinks, and identity changes. No arbitrary caller
path or recursive deletion is accepted. A durable purge intent is saved in the
original execution document before unlink; an interrupted pass resumes only
that exact identity. A successful receipt records `logExpired`.

Expired Run detail returns 410 `history_expired`; original operation queries
continue to return the original identity and result. Original history queries
project an explicit `history_expired` marker. Expired log reads return 410
`log_expired`, and discovery projects `status:"expired",reason:"log_expired"`.
Definitions, next scheduled fire and run admission limits remain effective.
