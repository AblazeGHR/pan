# Action integration candidate — 2026-10-21

Disposition: **ready for independent MA review, not accepted or merged into main**.
This continues the isolated review at `54189ff4db415d6fddd9ea87ec040a1c577ceb66`.
The author of the B1 repair performed this follow-up; it is not independent MA
acceptance of that author's own implementation. Historical failure evidence and
the previous not-accepted disposition remain available in README and B1-REPAIR.

## Integration and concrete changes

- Rechecked clean worktree, branch `integration/action-groups-review-20261021`,
  HEAD, constraints, frozen design and original audit before proceeding.
- Merged `978beef4680f33bed05c156a7592e6b075db3b46` into this isolated branch as
  `7aee59d9157d6f3873497e29779476002349348e`, without conflicts. This includes
  Task A's history search and model catalog on top of the already integrated
  history read baseline. No command moved main/practical.
- Task A's history read, search index, model catalog, Codex metadata hook and
  their tests match `978beef4` exactly. MCP tools, manifest and HTTP routes coexist
  with Action endpoints. No Task A API or permission check was removed.
- Reproduced a settings cancellation/failure window: durable plugin selection was
  saved, but restart registration was after the awaited notification. Cancellation
  or a notification exception could strand an existing Worker without its restart
  intent. Register the existing delayed restart before notification; keep the
  committed settings, propagate the notification error/cancellation and release
  reservations. No new restart mechanism or permission flow was introduced.
- Reproduced synchronous catalog IO on plugin `core_enabled`. Its directory load
  and the analogous watcher directory load now use the same cancellation-safe
  off-loop IO helper as snapshot/discovery. A blocked reader no longer prevents
  heartbeat progress. This is a repair of remaining IO paths, not a new runtime.
- Six new regression cases: notification cancellation/error (2), slow catalog
  core-enabled/watcher entry (2), and actual native admission versus unbuild in
  both orders (2). Existing tests, assertions, thresholds and skip markers remain.

## Reservation and lifecycle review

Coordinator registration/removal is synchronous event-loop memory mutation:
no awaits, filesystem work or Worker launch inside that short section. A token
is a logical resource reservation, not a global mutex. Shared Groups plus an
exclusive Session are registered atomically. A waiter owns no partial claim and
re-reads the Session selection after waking. Unrelated Sessions and heartbeat
advance while the injected slow launch, Action disk or Session disk is held.

Lock order remains:

1. Settings: existing per-Session settings lock -> atomic Session/Group claim ->
   ordered persistence ticket. Startup: existing per-Session spawn lock ->
   Session/Group claim -> startup/persistence. Nested same-task claims reenter.
2. Consumer/native: atomic Session/Group claim -> existing queue lock -> durable
   admission/write/receipt. The final production `_queue_handoff` is covered,
   including a real stream consumer preparing running after unbuild saw idle.
3. Build/unbuild: exclusive Group first; only after admission enumerate owners.
   Unbuild then claims those Sessions together, rechecks running, commits detach,
   publishes retirement, requests normal restart and cleans files. It never waits
   for settings/spawn/queue locks. The potential reverse-order waits therefore do
   not form a cycle. Different releases may serialize on the existing off-loop
   store writer, while unrelated Session metadata uses its own ordered writer.

If native/consumer admission wins, unbuild waits for admission to retire and
rejects running. If unbuild wins, waiting input cannot cross the old pinned
release boundary after detach. Startup and settings are also excluded until the
release transaction retires. All three modes retain immutable definitions and
real startup pins; an unpinned new Group on an existing gateway is withheld until
restart. Rebuild cannot replace the running Worker's loaded definition. Generic
gateway plugins retain their existing immediate logical selection behavior.

Cancellation while waiting acquires nothing. Acquired reservations release in
`finally`. IO waits for its writer to finish before cancellation propagates;
release runs in an owned shielded task until commit or rollback finishes.
Registry publication failure restores every live config before attempting every
durable restore, and reports IDs if any restore also fails. Cleanup failure after
retirement reports failure with the Group already unregistered and restart intent
already registered. Settings write failure keeps old bindings and disk bytes.
Durable settings success followed by request cancellation retains the restart
intent, including the newly covered notification window.

The earlier six fixes remain exercised: streamed API output/time limit, encoded
MCP result bound and subsequent-call health, boolean property schemas, exhaustive
rollback attempts, gateway real-pin enforcement, and optional-properties Panel
rendering. The Panel source is unchanged in this continuation.

## Evidence and limits

Raw JUnit, source hashes, commands, timeout-plugin provenance and exact diff
inventory are under `integration/`. `failure-first.xml.gz` records **3 failures**
against merged pre-fix production sources; these are reproductions, not a passing
run. The native interleaving and all repair cases passed after correction.

The first coexistence run passed 156 cases; the first lifecycle run passed 156
cases. They overlap and must not be summed. Their interpreter lacked
pytest-timeout, so they are preliminary evidence only. Installed pytest-timeout
2.4.0 with `--target` into this tree's ignored validation directory, without cache
or dependency installation into any shared environment. The final union rerun
loads that plugin and preserves repository `timeout=300` and per-test overrides.
Final results are recorded in integration/checks.json and final.xml.gz.

Final tested source commit: `423d443c88925131c0a039ff209a01243b8b6cf0`.
The final 11-file union completed **250 passed, 0 failed, 0 skipped**, exit 0,
in **72.64 seconds**: 63 Action cases; 66 Task A read/search/catalog cases;
28 generic plugin configuration/platform cases; 32 native admission cases;
5 delayed-restart HTTP cases; 42 adapter-validation cases; 14 prompt-spawn cases.
The later evidence-only commit does not change these sources. Raw records retain
existing deprecation/property-recording warnings and the harmless assert-rewrite
warning from explicitly importing the local timeout plugin before pytest loads it.

Task A's protocol tests exercise actual owned HTTP and stdio MCP without provider
calls or credentials. Generic plugin tests also cover actual stdio/HTTP/SSE and
three-mode Action execution. Lifecycle race tests use controlled Worker/process
fixtures, not paid CLI execution. No new production-browser run was needed for
unchanged frontend/build inputs: prior 47-case Vitest, lint, typecheck/build and
owned browser/mobile evidence are retained in `b1/`, with their earlier provenance;
they are not relabeled as current-run checks. Python compileall and whitespace
checks passed for the final changes.

Residual limits for MA: claims are process-local and have no writer-priority
fairness guarantee; multi-process sharing is not supported by this repair. A
process crash between multiple durable files or repeated rollback disk failure
still has no recovery journal. No paid native CLI restart, POSIX descendant
cleanup, full suite, remote CI, service deployment or human acceptance is claimed.
This is not a project-wide conversion of legacy synchronous filesystem paths.
No product scope change is proposed. Independent MA source/acceptance review is
the remaining gate; this task stops at the clean candidate.

Protected listeners were rechecked: 8768 remained PID 6032; 8767 was unused.
No deployed service was called, stopped or restarted. No push, release, paid model,
subagent, shared dependency write or change to main/practical was performed.
Every command finished under three minutes; no persistent long-command Job was
needed. The renewed 95% stop instruction remains in effect for any later notice.
