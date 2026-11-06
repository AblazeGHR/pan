# P6 JSON scheduled-task handoff and lifecycle candidate

Baseline: `9807ec8febcaedb4b1a36640805b57eaaf95fbff`.
Branch: `implement/action-unification-p6-20261106`, original isolated worktree.
The exact candidate SHA is supplied with delivery. **This is a partial P6
candidate, not overall migration completion or MA acceptance.**

The revision 4 Action-unification plan §12 requires source preservation,
profile-wide writer exclusion and original operation recovery. Its §16/§17
capability requirements remain in force. The old retirement-only assertions
are evidence of the former boundary, not authorization to remove capabilities.

## Source and identity contract

`packages/scheduler/json_handoff.py` adds an offline host preview/apply adapter
over FinalService, the existing CreatorRuntime/admission/typed executor,
OwnerFence and final_operations. It creates no scheduler pass, effect engine,
retry engine or separate operation ledger.

Eligibility is deliberately specific:

* The profile must have been enrolled as cooperative **before its data existed**
  using the existing fresh-profile protocol. No existing/unknown installation
  is enrolled or retrospectively attested by this adapter or its tests.
* Every selected source is read as original bytes with duplicate-key/nonfinite
  JSON rejection. Unknown fields, unresolved resources, missing checkpoints and
  unsupported mappings are preserved/blocked. Sources/journals participate in
  preview CAS. Unselected live sibling records block profile-wide cutover.
* Apply checks source hashes, exact fixed ActionRefs, policies, cursor, counter
  facts and owner generation. Existing live or abandoned legacy leases block
  the existing profile claim; none is deleted or reset. The durable original
  intent precedes target commit. A crash leaves the source fenced and resumes
  only the same reviewed intent/operation. Source JSON/journal bytes stay intact.
* jobId and taskId are retained as distinct identities. Original revision and
  generation, when present, remain in the binding; absent values use a local
  compatibility revision and are not presented as recovered old writer epochs.
* Already materialized Run/Attempt/effect records transfer only with their full
  canonical checkpoint, matching profile, Job/config revision, plan digest,
  fixed ActionRefs, admission/Step references and effect/Attempt identities.
  The original runtime aggregate is copied verbatim; no new Run or Attempt is
  allocated to impersonate old execution. Unknown remains uncertain and is not
  sent again. A queued original Run may execute its first effect using that
  same Run; its later receipt increments the original counter exactly once.

The positive checkpoint tests use an explicitly cooperative synthetic
predecessor and actual WP02 admission/executor output. They do **not** prove
that a historical production JSON record has that checkpoint, that a stopped
unknown writer is safe, or that real CLI/QQ execution was accepted.

## Dispatch, calendar and count semantics

The pure calendar/cursor conversion was extracted from `legacy_source_plan`
into `legacy_schedule_plan`. The v1 source adapter retains its original schema,
owner and pending-publication checks; JSON validates its own source contract
before using the same calendar calculation. No JSON record is relabelled as a
v1 source owner to pass validation.

The original next-fire fact is restored, with an explicit reviewed timezone;
it is not recalculated from current time. Pause uses the existing clock's
advance policy, producing no catch-up Event for a paused occurrence. A future
scheduled Run freezes the original dispatch format
`taskId:entryId:int(fire_timestamp)`. Manual invocation preserves its original
task-scoped key and leaves the scheduled cursor unchanged. Explicit requestKey
and expectedRevision controls are additive.

The old source at `68be5bd4^:packages/core/background_jobs.py` increments
runCount **after** `_run_job_action` returns; the old manual `engine.run_now`
increments it too. Creator.producedCount instead counts published Events.
These are not interchangeable. Compatibility count is original runCount plus
distinct newly returned effect Runs, excluding only original already-counted
receipts. Existing effect/clock fences enforce maxRuns. Event publication or
admission alone does not increase runCount; unknown provider results count
once and never authorize re-send. No second mutable budget counter is added.

## Entry compatibility matrix and red/green evidence

| Old behavior / gap | Canonical successor in this candidate | Named evidence / limit |
| --- | --- | --- |
| Active JSON source had no reviewed successor | Explicit source preview/apply, same Job/task identity and unique writer | P6_JSON_01; actual baseline retained.py loaded from 9807 gives the original gap red |
| Source writer still active / abandoned lease | Existing OwnerFence claim refuses, original bytes remain | P6_JSON_03 / 10 / 11; crash recovery and stale nonce tested |
| Original next-fire, runCount/maxRuns | Existing calendar/cursor and effect receipt facts | P6_JSON_04 / 08; paused grid, manual count, maxRuns and Event dedup |
| Original Run/Attempt/effect / queued dispatch | Exact complete checkpoint transfer, same frozen identities | P6_JSON_05 unknown and queued cases; actual shared executor output, cold no replay |
| GET task/list/history | Current task fields plus additive canonical successor; original raw history retained; new Runs keep old history field names | P6_JSON_06 real TCP; original task/run/dispatch IDs and count asserted |
| POST task pause/resume | Existing Creator lifecycle; in-flight Runs are not paused or rebound | P6_JSON_04 / 06 / 07 / 08; real TCP and standalone stdio |
| POST task run-now | Existing Job admission and runtime executor; cursor unchanged, original manual intent receipt | P6_JSON_04 / 06 / 07 / 09; same-key replay; disabled clock stays disabled |
| DELETE task | Retire the canonical task graph, retain source bytes/history; unsettled execution/publication blocks | P6_JSON_04 / 06; unknown deletion refusal and real TCP delete |
| Legacy MCP names | scheduler_pause/resume/run_now/delete restored as controlled adapters; exact target access checks precede write | P6_JSON_07 actual standalone core stdio/IPC; existing core/native transport regressions |
| Preserved Worker HTTP empty response | Owned loopback client bypasses external proxy routing; asserts HTTP status + JSON type | Original red -> direct TCP/real owned-process green, also in final combination |

The original Scheduler alias route-owner/tombstone/MCP inventory red is kept as
`json-scheduler-alias-retirement-red.xml.gz` (six failures). The four restored
entries are now covered by named source-bound successor behavior rather than
the incompatible assertion that every old address must be a tombstone.
Unrelated retirement assertions remain intact. The historical inventory
manifest is unchanged; restored names are accounted for explicitly.

Manual HTTP/MCP responses retain the outer task/run envelope and nested old
run field names, with additive canonical operation facts. **Admission returns
accepted, not a claim of synchronous provider dispatch/completion.** Clients
query the same Run/operation after an ambiguous reply. Full equivalence with
the retired synchronous run-now result, including every provider result and
WS terminal timing, is still an open compatibility item; this slice does not
call that difference accepted product parity.

## Worker HTTP investigation

The unchanged owned-process TCP test reproduced the JSONDecodeError on the
baseline at the start of this slice. `httpx._utils.get_environment_proxies()`
reported two proxy patterns, a global proxy and no loopback bypass (addresses
or credentials were not recorded). Windows system proxy discovery can apply
without *_PROXY environment variables. The test's owned 127.0.0.1 listener
therefore requires a direct client.

`trust_env=False` makes this isolated probe reach its own listener; status and
Content-Type assertions now precede JSON parsing. The original two concurrent
starts, distinct Worker/process IDs, live owner checks, explicit kills and
process exit assertions all pass unchanged. No production Worker/service code
or system proxy configuration was changed. The harmless stand-in processes
remain synthetic CLI evidence, not real model-CLI acceptance.

## Verification layers

All tests used `scripts/run_tests_isolated.py`, temporary stores/Session/auth,
owned subprocesses and dynamically allocated listeners checked against
8767/8768. Final interpreter:
`C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe`.
Timezone regression groups used the existing read-only
`PYTHONTZPATH=E:/software/python-userbase/Python312/site-packages/tzdata/zoneinfo`.
No global dependency installation was needed.

| Layer | Result |
| --- | --- |
| Scheduler/JSON/Trigger/clock/retirement/Worker combination | **162 passed**, 0 failure/error/skip; `json-scheduler-combination-final.xml` |
| Canonical HTTP/clients/source migration/owner/Session runtime | **174 passed**, 0 failure/error/skip; `json-scheduler-canonical-final.xml` |
| Latest JSON source after count and return-field follow-ups | **22 passed**, `json-scheduler-delivery-final.xml`; overlaps combination and adds queued-checkpoint coverage |
| Real HTTP | Owned task lifecycle/history TCP and two concurrent owned Worker process starts/kill passed |
| Real MCP | New legacy alias calls through real core standalone stdio/IPC; existing core/native client suites passed |
| Source/static | AST parsing, `git diff --check`, isolated `python -m packages.jobs.final_contracts --check` passed |
| Frontend / production build / Chromium | Not rerun; no React/dist edits. Python tests are not build/browser evidence |
| Remote CI / Linux/macOS / real migration / deployment / manual acceptance | Not performed |

The two broad groups preceded the final disabled-pause/return-envelope/count
follow-ups. The last 22-case source verification covers those changes and concurrent lifecycle CAS/read queries. Earlier
intermediate product/harness reds remain indexed separately, not counted as
passes. Artifact hashes, source hashes, exact commands, baseline and protected
refs are recorded in `evidence/action-unification-p6/json-scheduler-validation.json`.
Failed XMLs redact only ephemeral owned-test token reprs and are compressed;
raw and redacted hashes are distinguished. Prior P6 red artifacts are retained.

## Remaining implementation and acceptance blockers

1. **Not every old dispatch has canonical execution records.** The standard
   `scheduled_records.build_record` persists runCount/lastFireAt/undeliveredFires,
   but no Run/Attempt/effect checkpoint. Old runs.jsonl has dispatch_key/run_id,
   not the complete Attempt/effect/frozen-plan authority required here. Such a
   source with historical/active dispatch facts remains blocked; this candidate
   does not invent missing identities. A shared original-dispatch reconciliation
   adapter remains unfinished, not declared model-impossible.
2. **Live running resources are not handed off.** A status-running process or
   Worker needs its original identity/lease/receipt proof and existing resource
   recovery adapter. Complete unknown checkpoints stay query-only; a queued
   proven checkpoint may continue without changing its Run. This is not full
   live-process migration.
3. **Multi-entry schedules, pending/undelivered dispatches and notices** still
   require a complete entry/occurrence/cohort mapping; sources remain intact.
   Unknown writer/lease sources cannot gain eligibility from fixtures or flags.
4. **Known Action families are not all connected here.** The JSON mapper uses
   the existing pinned runtime Session/assign/QQ products. Shell and other old
   families need their existing approved mappings wired to this host; these are
   implementation gaps, not blanket claims that simple records are complex.
   QQ mapping is not real QQ/provider acceptance.
5. **Complete old CRUD/config and synchronous result parity remain open.**
   Old create/update (including schedule/target/text edits) are not restored by
   the four lifecycle aliases. Every legacy provider result/history error field
   and terminal notification/WS timing still needs family-level equivalence.
   The current accepted envelope is honest but does not close synchronous parity.
6. **Migration is an offline host port, not a complete operator HTTP/MCP/CLI
   workflow.** Real stdio validates the adopted runtime lifecycle, not remote
   enrollment or unattended real-profile migration. D6 preservation and
   original effectful Trigger-plan gaps from the previous report still apply.

No push, main/practical update, deployment, protected-port operation or real
data/profile access occurred. Normal repository hooks are used for the
candidate commit. Source completion, local tests, build, runtime transports,
remote CI, migration and MA/human acceptance remain separate claims.
