# Terminal current-main isolated integration candidate

## Source and authority

This is a local candidate on `integrate/terminal-ma-final-20261004`, worktree
`terminal-final-integration-ma-20261004`. Merge commit `1b7272c3` joins:

- current main `e6a5e84e4d814e752bd73560354523d558b16edb`;
- accepted Terminal branch `1abe2f8d04814738bd1a3eff49949468872ef4e9`.

Both merge-tree preflight and actual merge were conflict-free. This is not a
fast-forward of main to the older Terminal branch: it preserves main's subsequent
Session persistence, usage, native import and queue changes. Relative to main,
`packages/core/session.py` only adds the seven-line serialized
`create_with_available_name` helper. No canonical branch was moved, no push or
deployment performed, no existing Pan service restarted, and port 8768 unused.

Dependencies were prepared independently in this candidate: frontend frozen
lockfile/offline install and sidecar offline `npm ci`, without changing either
lock. No shared dependency junction was introduced. Merge precommit TypeScript
checks ran normally. Historical audit log whitespace was preserved, not rewritten
to manufacture a clean historical diff-check.

## Candidate verification (distinct from original-tree whole regression)

| Layer | Actual result |
| --- | --- |
| Complete frontend Vitest | 1276 passed, zero failed/pending |
| Frontend production build | 36 compressed assets, successful |
| Terminal Python selection, uv | 169 passed |
| Rewind/usage/native-import/queue selection, uv | 126 passed |
| Session incremental file, uv | 16 passed |
| Real complete browser/ConPTY chain | passed=true, harnessExit=0 |
| Real natural-exit/archived-selection browser | passed=true, harnessExit=0 |
| Real browser-generated Origin rejection/positive controls | passed=true, harnessExit=0 |

Evidence is in `audit/terminal/implementation/browser/candidate-main-*`.
The complete real browser fixture verifies Chinese input, Ctrl-C interaction,
control takeover, resizing, reload/recovery, supported durability layout,
browserless retention eviction/recovery and confirmed close revoking control.
These are measured paths, not proof of every TUI, ancestor Job or deployment.

The natural-exit fixture verifies final Chinese output, root exit code 7,
`output_complete=false` (process death does not fabricate EOF), authoritative
record `exited`, input disabled, and later selecting that ended record opening no
new socket. Historical screens are not persisted: the UI explicitly says the old
process cannot be reconnected, while preserving the current connection's tail.

## Original frozen whole-repository result

The original Terminal tree's Python remained exactly `586ec3f0` while its whole
repository run executed: **2632 passed / 1 failed / 10 skipped**, 1052.26s. The
single failure is the existing incremental-save performance factor assertion
(1.632ms vs 5.463ms), not a Terminal functional assertion. One isolated rerun
passes (2.07s); the repair range changes neither that test nor Session persistence.
Cause is not established. The original whole run remains **not all-green**.
Copied XML files retain the original result and its passing control; they are not
candidate whole-repository runs. Earlier all-green whole runs remain historical.

## Handoff boundaries

This candidate is verified locally, **not deployed and not main/practical**.
Complete candidate whole-repository testing subsequently ran (result below). Dependency warnings
about unresolved lifespan annotations/Starlette remain recorded in XML; no claim
of warning-free validation is made. Browser screenshots and transcripts are
fixture artifacts on owned ephemeral listeners, not existing user sessions.

Cross-platform support, arbitrary remote exposure/authentication, cross-user
Windows security, every TUI rendering mode, long-duration resource stability,
all ambient-Job durable layouts and cross-sidecar restart recovery are not
certified by these checks. Write cancellation budgets remain conditional, not
hard OS deadlines. F5 confirmation remains a conservative approximation without
atomic historical generation binding. Explicit authorization is still required
to advance canonical branches, push, build practical or restart deployed Pan.

## Candidate full-regression work in progress

A subsequent whole candidate Python run is in progress; it is not yet an
accepted result. One early failure was independently isolated:
`test_queue_edit_cannot_make_text_disagree_with_parts` expects
`parts_text_conflict`, whereas the current-main queue parser returns
`invalid_queue_body`. The same single test fails on canonical main `e6a5e84e`
without the Terminal merge (read-only test, isolated temporary fixtures, cache
disabled). The merged test file is unchanged relative to main and the queue
update implementation has no Terminal delta. Candidate attachment-file rerun is
13 passed / 1 failed; a separate backend performance file passes 8/8.
This is a reproduced main baseline assertion conflict, not a root-cause claim or
permission to change attachment semantics. It remains a failure; no assertion
was weakened and no canonical file edited. Final whole results will supersede
only this in-progress statement, not these measured controls.

### Current-main browser security control

`candidate-main-browser-origin-control/result.json` uses real Chromium-generated
headers (no forged Origin overrides). Same-site different port, different
loopback hostname and opaque sandbox `Origin:null` each witness HTTP 403 through
CDP wire status and WS unopened/zero received frames. JavaScript cannot read the
CORS denial body, as expected. Same-origin hello/claim succeeds; original runner
PID/FILETIME remains unchanged after all attacks; explicit close confirms exited
and owned harness exits zero. This is a measured current-main local browser gate,
not all-browser, remote or cross-account authentication certification.

## Final whole candidate result and quota stop

The frozen candidate whole run finished **2714 passed / 3 failed / 10 skipped**,
1056.42 seconds, two dependency warnings. Python sources/tests remained unchanged
relative to `09ceb806` throughout. The exact JUnit evidence is
`candidate-main-full-repository-uv.xml`. This is **not an all-green candidate**.

Failures:

- `test_queue_edit_cannot_make_text_disagree_with_parts`: the reproduced main
  baseline error-code conflict described above.
- `test_perf_incremental_vs_full`: incremental-save performance ratio assertion;
  candidate isolated file previously passed 16/16, but that does not erase this
  whole-run failure or prove its cause.
- `test_queue_api_edits_only_queued_user_and_reorders_all_sources`: expects
  `queue_item_readonly`, receives `queue_item_edit_expired`. No separate main
  baseline rerun or causal attribution was performed for this final failure.

At completion the live five-hour quota query reported **usedPercent=100**,
`stale=false`, observed at `2026-10-04T12:28:21.469682+00:00`. Work stopped as
requested; only this final handoff and XML archival were completed afterwards.
No further repair/dispatch/deployment or investigation was started. The completed
test process has exited; all earlier browser harnesses exited zero. Main/practical
remain untouched. The candidate is a local integration handoff with strong
Terminal/browser evidence and explicit outstanding repository regression failures,
not unconditional final acceptance or deployment authorization.

## User-authorized resumed closeout

The subsequent closeout aligns the two queue contract tests with current main
without changing production queue guards, retains all incremental performance
thresholds and adds a structural no-history-scan guard. Focused three-file
regression: uv 54 passed and direct 54 passed. Whole run on `43ba670d`:
2717 passed / 1 failed / 10 skipped; all original three failures passed.
Its new failure was an empty witness-file read in the real crash/restart test.
Waiting for exact completed content instead of filename existence retains the
acceptance condition; the lifecycle file rerun is 5 passed. Historical failed
XML remains untouched. Final whole acceptance is pending a frozen-source Job.

User separately authorized arbitrary cwd for agent Jobs. The candidate removes
only cwd project containment, preserving existing-directory validation and
Session permission/delivery rules. Focused Job regression: 60 passed. Actual
deployed completion-notification probe succeeded; this does not deploy the cwd
change. See `PAN_AGENT_JOB_CWD_CLOSEOUT_20261004.md` and the dedicated human
acceptance guide. Canonical branches and service are unchanged.
