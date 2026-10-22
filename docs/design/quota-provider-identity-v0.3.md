# Provider quota identity correction (supersedes host source assumptions)

This batch changes only the default-off read source, typed identity contract and
sampling validation. No real GUI, account/provider query or mutation was executed
for implementation or testing. Existing host effect ports remain unbound.

## Evidence from existing interfaces

Source audit: `packages/cc_switch_bridge/provider.py`, Bridge.listing/_invoke and
normalize_quota; external CLI source read only at
`D:/project/cc-switch-pan/src-tauri/src/pan_minimal_cli.rs` provider list/quota/
switch and quota_output, plus services/subscription.rs tier constants. CLI
contract is pan-cc-switch-gui/v1. This source audit is not a real account probe.

`invoke('list', {app:'codex'})` returns currentProviderId and providers with
category official. `invoke('quota', {app:'codex',id:providerId})` returns tiers,
usedPercent, resetsAt and queriedAt. `invoke('switch', {...,expectedCurrentId})`
checks the current **provider** and verifies the selected provider after switching.
The previous statement that all selectable identity/quota required a managed/native
mapping was incorrect. Provider IDs can be sampled/selected in their own domain.
The existing switch expectedCurrentId check is real but is not an atomic policy +
Worker/task CAS; its business effects remain outside this batch.

The CLI provider list exposes no native-account/profile or Worker launch binding.
Managed OAuth quota internally resolves a provider's configured managed account or
its default, but the list output does not expose that relationship. Never infer it
from equal strings, labels, quota values or auth status. Non-managed official
subscription providers may return not_available when not current: keep unknown,
do not switch just to query. No GUI DB read, guessed API or credential extraction.

## Domains and stable public fields

| Domain | Authoritative fields | Policy meaning |
|---|---|---|
| cc-switch-provider:codex | provider list currentProviderId, category official, quota response providerId | Selected policy domain; opaque provider ID is passed to existing provider switch, never auth default. |
| cc-switch-managed:codex | auth status default_account_id/accounts[].id | Independent optional metadata only. No quota ranking or Worker identity inference. |
| codex-native | existing CodexProfile account_id/profile_key + quota cache | Independent optional native metadata only; never mapped to provider by global current value. |

`hostObservation.identityDomains` declares the three subobjects. New
`hostObservation.provider` contains identityDomain, status, currentProviderId,
providers (per-ID known/unknown/disabled), observedAt, stable, catalogDigest and
fixed safe reason. Known rows retain queriedAtMillis (CLI Unix milliseconds),
queriedAt (seconds), local receivedAt, source, providerUpdatedAt:null and the
exact recognized tier/window. queriedAt is query completion, not provider update.

Policy quota now carries `identityDomain:'cc-switch-provider:codex'`. Existing
`currentAccountId` and `accounts[].id` are compatibility names for **provider IDs
in that explicitly stated domain**. They are not managed/native IDs. Unknown
provider snapshots retain their domain and contain no selectable accounts. Legacy
injected adapter fixtures with no domain remain supported; arbitrary domains are
rejected. No mapping/migration of historical command identities is performed.

`disabledAccountIds` remains the existing opaque persistent denylist, default
empty and unchanged. Production sampling compares its exact strings against
provider IDs only; it never converts them to/from managed/native IDs. Historical
ambiguous entries may conservatively exclude an equal provider ID and must not be
cleared automatically. This revision applies no real user's deployment inputs.
Future effect binding must fence old domain-less command/Job histories before
enabling any production mutations; this batch cannot enable them.

## One-shot bounded sampling

The existing Job tick invokes the source once, off-loop and outside policy,
Session or Worker locks. No new poller, controller, account registry or replay.
Bridge.read_only is a narrow internal path over the same CLI runner/executable
verification: list, managed metadata, quota only. Unlike invoke it takes no bridge
file lock and writes no Pan quota cache or lock file. Per-read local runner budgets
do not overwrite another Bridge operation's mutable deadline. Existing invoke
write/Panel behavior remains unchanged. The external GUI may own its own quota
refresh/cache lifecycle; no external credentials or cache were touched in tests.

Budget8 seconds total; <=128 catalog entries, <=32 official providers, <=32 tiers.
Provider IDs are nonempty, unique <=256 chars, no leading dash or whitespace.
Only exact official category is sampled. Disabled providers are retained as
disabled diagnostics and not queried or eligible for rank/reset. Before and after
sampling, currentProviderId and the full sorted official ID set must agree. This
does not prove absence of an external A->B->A change or an unreported provider
configuration/managed-default edit: the current CLI exposes no catalog revision.
Therefore this sample is never promoted to Worker launch proof/effect admission.

Every enabled official provider must return a matching app/ID, ok/available,
successful subscription result, no error, exactly one five-hour and seven-day
window, valid finite percent0..100, timezone-aware/numeric future reset and fresh
queriedAt (<=300 seconds, no future values, explicit milliseconds). Recognized
names: five_hour/seven_day; exact old display aliases `5 小时`/`7 天` are accepted.
Model-scoped seven_day_* is not an account-wide week. Partial, ambiguous, stale,
failed, expired-budget or catalog-changing sample -> overall unknown, no ranking
or switching. Known individual rows remain diagnostics only if overall unknown.
Current nonofficial/disabled -> overall unknown. Sampling never selects all on
error. Raw messages, labels, URLs, tokens and credential fields are not published.

Publishing rechecks host epoch/config and accountConfigRevision after all IO;
disabling a provider mid-sample rejects the previous publication. Last observation
persists in the existing policy ledger only; it is not another account registry.

## Minimal explicit launch-binding design (not implemented or fabricated)

Current Worker.create_worker records only codex_profile_key after spawning; fork
copies it. This is neither provider ID nor proof of what a process loaded. CLI
list/quota/switch has no launch receipt/config revision. Existing Workers therefore
keep provider accountId unknown even when current provider quota is known.

Next guarded-launch seam must use the existing Worker lifecycle and persist a
`providerLaunchBinding` extension on its existing execution operation, not a new
registry. Required schema: version1, identityDomain, providerId, launchId,
runtimeEpoch, sessionId, workerId, strict generation, retained PID/raw FILETIME,
native profile reference if available, immutable launch-config digest/reference,
capturedAt, verifiedAt and trusted launcher receipt identity. No credentials.

The launcher must explicitly choose the provider, prove the child loaded the
provider-qualified fixed configuration, probe the retained process, then persist
that receipt before publishing a usable binding. A global provider read before/
after spawn is insufficient: without a pinned configuration or native attestation
there is still a race. No public caller-provided verified flag is acceptable.
IO stays off-loop; only a short in-owner publication compares exact process,
generation and epoch. Persist failure -> no usable binding. Same process binding
may survive global provider changes; it never changes to the new global provider.
Restart/fork/new generation require their own receipt; no copying predecessor
binding. Reloaded historical receipt is audit only until live ownership and exact
process identity are revalidated; unknown/old epoch cannot resume effects.

This batch deliberately creates no such receipt or Worker account binding. It
does not retrofit old Workers or add a setter to the API. Actual effect wiring,
unknown command fencing, launch attestation and restart/task continuation remain
blocked on these specific missing proofs, not on managed/native cross-mapping.
