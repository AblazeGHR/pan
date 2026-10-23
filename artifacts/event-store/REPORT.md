# TA candidate: durable Event Store foundation

Branch: `feature/event-trigger-core-20261022`
Worktree: `D:/project/pan-worktrees/event-trigger-core-20261022`
Accepted starting baseline: `b694ea53bb23651770ad61debf3184e3c5a32274`
Implementation: `c818b7f15c0afae37432813768e03c49409f46ff`
Exact final tested source: `4a55e6776ad0571772b1b98263e0bf7e4c382f92`

## Executable coverage matrix

| Requirement | Implementation / executable evidence | Boundary |
|---|---|---|
| Validate, commit, then deliver | Bus.prepare/publish_async, Service.accept, SQLite FULL commit; test_commit_precedes_fanout_schema_and_write_failure | No effect or subscription on failed admission |
| At-least-once Event matching | Pending inbox dispatcher, durable matching release, startup scan; test_crash_after_commit_before_delivery_and_frozen_trigger_set, test_partial_run_write_recovery_dedup_and_no_automatic_run_retry | No exactly-once effect guarantee; live plugin delivery remains best effort |
| Event+Trigger dedup | Retained ledger pair, same-ID fingerprint check, concurrent16 resend regression, HTTP duplicate after restart | Retention-bounded, not permanent after records expire |
| Bounded storage | Event100000/256MiB logical, SQLite320MiB, payload64KiB, Run10000/64MiB logical; capacity and retention tests | Pending/uncertain protect records; capacity stops new admission visibly |
| Seven-day retention / replay | Fixed maintenance/startup/60s, bounded page/replay APIs, Bearer authorization; API and expiry tests | No arbitrary delete, no retry of existing terminal Runs; uncertain replay409 |
| Attempt foundation | Claim persists one attempt; mirror checkpoints, cancellation, deferral/recovery tests | No retry API/policy or automatic second attempt |
| Raw Worker event / query / Pause | test_actual_running_event_persists_before_query_pause, core-running/http-evidence.json | Real owned HTTP and fakeCLI, sentinel unchanged, no paid provider |
| Real external Node / query / Pause / replay | test_node_durable_event_http_replay_restart_and_uncertain, http-evidence.json; SQL durable-row assertion and persisted receipt | Offline interruption fixture is controlled, not a physical kill/power-loss experiment |
| Production UI / plugin / delay coexistence | test_eta_final_combined.py, combined-final browser.json and recovery/browser.json, screenshots and combined-http.json | Existing production UI reused; no new storage/replay frontend implemented |
| Serial isolation and close | All8 test_automation_concurrency regressions; slow commit cancellation heartbeat, revoked waiting handle regression | Short matching/admission coordination; no lock spans Action execution |
| Query checkpoints and legacy gates | Three selected query tests, Group actual-spawn pin and high-risk gates; core suite | No Job Template/new Action snapshot/parallel-mode feature |
| Plugin readiness and generation | Selected real rejection/restart/lifecycle/EOF regressions plus combined HTTP | Existing runtime identity retained; no public publisher or arbitrary source |
| Ledger growth | test_eta_ledger_growth.py, combined-final/ledger-growth.json | Finite1/100/1000 sample; no long-duration throughput claim |

## Validation and reproduction

`validation.json` records the exact argument-array command, canonical Python,
source SHA, exit code, elapsed time and encoding. Re-run that command from this
worktree. It uses `scripts/run_tests_isolated.py` and the canonical read-only
`D:/project/Pan-main/.venv/Scripts/python.exe`, not a local full-repository suite.
Final result: **58 passed, 0 failed, 0 skipped**, one existing warning, exit0
in35.43s. Final JUnit: `final.xml`; output: `final.log`. Default Windows encoding is cp936,
utf8_mode=0, PYTHONUTF8/PYTHONIOENCODING unset. New harness text IO/subprocess
decoding is explicit UTF-8. Existing pydantic unresolved-lifespan annotation warning
is reported separately, without changing production code or suppressing warnings.
Static compileall and diff checks are recorded in `static.json`.

`source-c818/` preserves the earlier55+2 passing test logs for the earlier source;
those counts are not added to the final count. `initial.xml/log` preserve the
initial1failed/27passed result: the no-public-publisher assertion expected404,
but adding GET discovery at that path correctly makes POST405. It was updated to
assert405, retaining the publication denial. `static-initial.json` records a diff
check failure on generated historical browser log blank lines; historical files
were preserved under this phase's evidence and restored to their tracked bytes.

A direct SQLite reconnect probe found that setting max_page_count only at startup
returns4294967294 on a new connection rather than81920. The final implementation
sets it on every connection; the store regression asserts81920 after initialize
returns. No large disk-filling experiment was performed.

## Migration and unfinished boundaries

The complete wire/data contract and migration policy is
`docs/design/Event-Trigger-Action-v0.2-event-store.md`; core-contract, implementation
supplement, plugin protocol and Pan tool/HTTP docs point to it. Events use a new
schema-v1 SQLite file beside the existing JSON registry. No old volatile facts are
synthesized. Legacy running records become uncertain with an inferred legacy
attempt; queued work claims normally. Existing Action/Session/Group contracts stay.

Old indefinite dedup is now bounded by retained records. Unknown/naive legacy
finish timestamps and queued/running/uncertain records are protected. An oversized
legacy ledger is preserved and growing recovery writes may fail startup; operators
must back up profiles and avoid mixed-version access. No automatic uncertain
reconciliation or deletion UI is supplied. JSON rewrites still limit throughput.
Physical SQLite cap excludes transient rollback journal and filesystem overhead.

Core synchronous Worker observation has a bounded128-entry pre-commit staging
queue; overflow or failed persistence is diagnostic. Abrupt death in that window
can lose an unacknowledged fact. Successful durable publication begins its guarantee
at commit. Disk corruption, real hardware/power-loss durability, sustained capacity,
real provider behavior and overall human acceptance remain unverified. Deferred
Job Templates, new Action snapshots, retries, parallel modes, durable subscriber
transport, configurable retention and new UI are not implemented or claimed.

## Isolation and final handoff

Both real HTTP harnesses and combined Chromium use owned temp profiles, random
ports checked against8767/8768 and deterministic fakeCLI. Their evidence records
PID/create-time ownership, ownedProcessesRemaining=[] and portReleased=true.
The core-running poisoned-parent sentinel remains unchanged. Only local production
assets/read-only dependencies were reused; caches stay in this tree. Historical
artifacts/eta-final bytes are restored; new evidence is isolated in this directory.

No main/practical or integration-tree movement, push/release, existing service
operation, user data access, paid provider, shared dependency writes or subagent
dispatch occurred. Candidate awaits MA independent review; overall manual user
acceptance remains pending. Stop here after delivering the clean candidate.
