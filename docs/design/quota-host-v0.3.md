# Quota production host adapter (default off, read-only production binding)

This batch binds the policy to the existing Pan owner/Worker/Session and local
quota store plus cc-switch read interfaces. It does not authorize real effects.

The [provider identity correction](quota-provider-identity-v0.3.md) supersedes
this document's initial source assumption: provider IDs and their own query API
are the policy domain; managed/native mapping is not needed for provider quotas.

## Actual interface audit

The existing GUI CLI contract `pan-cc-switch-gui/v1` implements `auth status
--json`, provider list/quota and auth default. Auth status exposes managed IDs,
default and reauth flags; it does not expose their mapping to native ChatGPT
account/profile identity or each managed account's five-hour/week limits.
Provider quota is provider-scoped, not evidence of an arbitrary managed account.
Never query guessed commands, read the GUI DB or introduce OAuth handling.

The production source reads all enabled official provider quotas and brackets
them with provider list reads. Policy currentAccountId means provider ID in the
explicit cc-switch-provider:codex domain. Auth status and CodexQuotaStore remain
separate optional managed/native metadata. No cross-domain mapping is inferred.
Only the latest raw snapshot is classified by actual duration using codex_quota;
merged old windows must not acquire a new timestamp from a partial update.
Preserve local observedAt/receivedAt, providerUpdatedAt (unavailable), source,
window/reset fields and timestamp meaning. No active WHAM refresh is introduced.
Freshness requires both observation and receipt no older than300 seconds; a future,
partial, duplicate-kind, missing-reset or expired-reset window is unknown. The
read path bounds cache bytes at2MiB and does not create lock/cache files. The
bridge uses its existing configured executable/contract verification and timeout;
the quota store uses its existing profile and quota-root resolution. No new env
variables, account registry or polling loop are introduced.

## Configuration

The existing policy ledger adds `host:{enabled:false,sessionRoles:{}}`. Enabled
means opt-in read projection, not effect permission. User Bearer PUT
`/api/automation/quota-policy/host` accepts exactly
`{enabled:boolean,sessionRoles:{sessionId:"ma"|"ta"},expectedRevision:integer}`.
Roles are explicit configuration because Pan removed its old role field; names,
managed depth, templates or capabilities cannot infer MA/TA. Changes fence old
plans via policy epoch. No credentials, arbitrary operations or effect ports can
be supplied by API. disabledAccountIds remains independent and defaults empty.

## Inventory authority

Reuse legal_offline.SERVICE_ID as the single runtime epoch, also attached to
trusted runtime-error metadata. Inspect only registered Workers and cached active
Session objects. Probe the retained subprocess handle using the existing native
PID/raw FILETIME helper; a bare PID, oneshot registration without a live process,
unknown handle or changed object/generation is unknown, never a live identity.

Unfinished task requires current Worker taskId/executionSeq, matching Session
active_task_id and an outstanding execution operation for the same existing
service/token/generation/execution. Safe terminal/retirement or a persisted
terminal result makes it unavailable. Restart clears those runtime fields today;
do not copy the frozen task back or invent post-restart continuation proof.

## Effect seam and remaining production wiring

The adapter exposes only the existing fixed policy operations. A trusted in-code
business port may prepare asynchronously, then call an admission ticket at its
actual non-awaiting acceptance boundary. Ticket rechecks persisted dispatching
command/epoch/config, disabled accounts, fresh inventory, current native process,
same task/receipt and business gates. A nonblocking store gate rejects contention
rather than blocking the event loop. Port must return a matching receipt; once an
effect is admitted an exception/unknown outcome cannot be automatically replayed.
An admitted operation invalidates the pre-effect quota/account sample, including
on unknown outcome. A following restart must observe the account again. Tickets
are owner-thread-only and single-use; their callback must accept synchronously,
never run blocking CLI/network/file I/O or schedule an unguarded delayed effect.
This seam is tested with injected owned fixtures; it is not a second controller
or a substitute for transactional CAS at an external GUI writer.

Production mutation ports remain unbound where existing entries do not support
these guards: auth default/provider switch has no remote policy+identity CAS;
ordinary send_session may spawn offline and has no frozen-task consumer guard;
ordinary restart clears execution context and has awaits before stop/spawn;
web model patch has no equivalent epoch/task admission guard. Calling these
legacy entries after inventory alone would violate the contract, so it is blocked.
MA resume Job admission and MA-last restart likewise remain unbound until that
full chain can persist and prove the required receipts. Existing policy order,
verified idle-successor rules, Pause/Illegal/leases and unknown non-replay remain.

No production effects can be enabled by this batch's API/config. Next phase must
add guarded seams to those existing business entries and verified per-Worker
provider launch evidence; provider quota reads already use the existing CLI.
