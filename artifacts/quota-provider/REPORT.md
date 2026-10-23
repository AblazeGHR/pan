# Provider identity source correction candidate

Base5ba14d841552eea25f56044547a8e1d0930698ed, original core worktree and branch
feature/event-trigger-core-20261022. Exact frozen source is recorded in
verification.json (1ea60582eff3275acb97816ab0f4733fdcc07018); subsequent commits contain evidence only. Candidate
delivery is not MA acceptance or real-provider validation.

## Implemented and available after opt-in

| Capability | Implementation / evidence |
|---|---|
| Provider/native/managed separation | Policy identityDomain cc-switch-provider:codex; currentAccountId/accounts[].id compatibility fields mean provider IDs. Independent native and managed metadata never establish Worker identity. docs/design/quota-provider-identity-v0.3.md is the superseding contract. |
| Existing official provider query | Narrow internal Bridge.read_only over existing provider list/quota CLI, existing executable verification, no new commands or credential handling. All <=32 official providers sampled, <=128 catalog entries, <=32 tiers, total8s budget. Not opt-in by default. |
| Lock-free source IO | No policy/Session/bridge locks across queries. No Pan bridge lock or quota cache writes. Per-call local deadline does not corrupt other bridge requests. Existing invoke list/quota/switch/default-account behavior preserved. |
| Known provider quotas without cross-mapping | Fully valid provider fixture yields known snapshot and rank even when managed/native metadata fail. Current provider and official set must match before/after; disabled IDs not queried/ranked/reset candidates. |
| Strict unknown | Wrong app/ID, errors, unavailable/failed result, invalid percent, partial/duplicate/scoped/oversized tiers, unknown/future/stale/wrong-unit query time, missing/expired/naive reset, changed catalog -> unknown and no selectable accounts. Retains source, query milliseconds/seconds, local receipt, missing providerUpdatedAt explicitly. |
| Persistence and concurrency | Existing ledger stores provider observation and typed quota. Disabled configuration changed during blocked query rejects old publication; no long lock blocks an unrelated Session. |
| No historical Worker backfill | Real Bindings projection with known global provider still reports Worker accountId None; runtime issue yields unknown, no effect command. No profile/native/managed->provider mapping added. |
| API/lifecycle compatibility | Default-off host/Bearer/revision/restart behavior remains; owned HTTP starts production lifespan with injected CLI/profile source, persists provider-known quota and metadata, closes owned server/restarts without leaked effects. Existing Panel/MCP bridge tests pass. |

## Actual missing proof / not implemented

CLI list/quota does not expose a provider-to-Worker launch receipt. Worker creation
records a native profile key after spawn; fork copies that key. Neither proves the
provider configuration loaded by a particular PID/generation. Current global
provider is therefore never filled into historical Workers.

The new contract specifies the minimal explicit providerLaunchBinding record on
the existing execution operation: typed provider ID, launch/config receipt and
digest/reference, owner epoch, Session/Worker/generation and retained PID/raw birth,
timestamps and live verification. Actual guarded launcher/config attestation and
record emission are NOT implemented. No caller can set a verified flag or account
mapping through this batch's API. Unknown Worker identity still blocks effects.

Existing provider switch really has expectedCurrentId verification; it lacks the
combined atomic policy/Worker/task admission. Provider data is now independently
readable; that missing effect/launch proof is a different boundary. Existing
switch/model/restart/wake ports remain unbound. No production mutation enabled,
no actual switch/restart/wake executed, no further empty effect ports added.

CLI before/after checks cannot detect A->B->A or unreported configuration/managed
default edits (no catalog revision in protocol). Known sample is query evidence,
not immutable launch identity. A disabled current or a failed non-disabled official
provider makes the aggregate unknown. Some non-managed official subscriptions may
not be queryable when not current; never switch just to query them.

## Migration

Existing opaque disabledAccountIds is preserved, default empty, exact denylist on
selected provider IDs only. No conversion or assertion of old IDs' domain; equal
strings may conservatively deny a provider and are never cleared automatically.
No actual deployment input/profile touched. Old quota/observation is replaced on
next opted-in query, not silently mapped. Domain-less historical commands/Jobs
need explicit fencing before any future production effects are enabled; current
production effects are unavailable regardless. No account registry or poller added.

## Validation

Command: D:/project/Pan-main/.venv/Scripts/python.exe scripts/verify_quota_provider.py.
Exact argv, interpreter, SHA, encoding and exit codes: verification.json.

- final-source.xml:82 passed (provider39 + accepted host43).
- final-policy.xml:84 passed (policy and safety relevant to normalize/disabled).
- final-bridge.xml:19 passed (existing protocol, Panel/MCP modes and guarded fake writes).
- final-http.xml:1 passed; final-host-http.xml:1 passed.
- Total187; failures0/errors0/skips0. All commands, diff-check and compile exit0.
- Windows utf8Mode0, gbk/cp936, no PYTHONUTF8/PYTHONIOENCODING. Explicit UTF-8
  fixture, subprocess decode, JSON/log IO. No install/shared dependency mutation.
- before.xml: preserved exit1 reproduction at base5ba14d84 with new fixture/test
  and read-only helper present but ReadSource unchanged: valid official provider
  query still produced unknown. This dirty-source failure is not the frozen run.
  first.xml/extended.xml/compat-before-freeze.xml are passing exploratory evidence,
  not added to final187. Assertions preserved; native-metadata tests now separately
  assert provider-domain independence instead of requiring policy unknown.

## Cleanup and untouched boundaries

http-evidence.json and compat-http-evidence.json record own temporary roots,
server PID(s), portReleased=true and ownedServerStopped=true. cleanup.json checks
those owned PIDs and ports again. Retained-process test child is terminated/waited
and post-exit unknown asserted; fake CLI subprocesses exit with bounded run.

No real account/credential/provider read, switch, user Worker restart/wake,
existing service access, port8767/8768, other worktree/main/practical/integration
movement, React change, push or release. Actual GUI account quota and all effect
behavior remain untested. Stop for MA review; overall human acceptance pending.
