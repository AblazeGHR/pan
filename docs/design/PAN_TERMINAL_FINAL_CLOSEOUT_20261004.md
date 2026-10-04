# Final candidate regression closeout

Baseline: local integration candidate `a12431d6` joining current main `e6a5e84e`
and accepted Terminal code. Historical whole-run failures remain archived in
`candidate-main-full-repository-uv.xml` (2714 passed / 3 failed / 10 skipped).
No production code is changed by this closeout.

## Queue contracts

Two older tests did not follow current main's body editor:

- Structured attachments are edited using the leased JSON template, not a plain
  competing text field. Plain text receives `invalid_queue_body`. Updated gates
  additionally check unchanged queue on refusal, retained edit lease, rejection
  of forged attachment identity and a successful text-only edit preserving the
  complete attachment metadata.
- Agent/report bodies are now editable, but require the target's own edit lease.
  A consumed user-row lease yields `queue_item_edit_expired`, not `readonly`.
  Updated gates check no unauthorized mutation and successful own-lease edits
  preserving source/kind/identity and updating the durable delivery ledger.

These align tests with the current implementation instead of removing main's
new capability to satisfy obsolete expectations. Production guards are unchanged.

## Incremental-save benchmark

The whole-run warmed ratio failed although isolated file controls passed. Its
old sampling measured all small operations first and all large operations later.
This establishes a measurement-window confound, not a proven antivirus/scheduler
root cause. Sampling now interleaves sizes in one window, reverses order each
round and uses 21 medians; **all original thresholds remain unchanged** (full
growth >2x, incremental growth <3x, large-size speedup >=2x).

A separate deterministic guard warms 100- and 20000-row histories, then forbids
iteration or slices wider than the 20-row tail. The next save must append exactly
one JSONL row, advance persistence/summary counts, preserve the new content and
keep metadata below 50KiB. This catches O(N) history scans regardless of elapsed
time; timing checks are not used as the sole correctness witness.

Focused uv regression: **54 passed**, `final-closeout-three-files-uv.xml`.
Focused direct regression: **54 passed**, `final-closeout-three-files-direct.xml`.
The frozen `43ba670d` whole run finished **2717 passed / 1 failed / 10 skipped**,
1055.998 seconds (`final-closeout-full-repository-uv.xml`). The original three
failures all passed, but this is still not an all-green result.

The remaining failure is the crash/restart lifecycle test reading an empty
`restart-witness`: cmd redirection creates/truncates its file before writing
echo output. Existence alone was the wait condition. The test now waits for
the exact expected text, retaining its final equality, shell identity, restart
and cleanup assertions. Deterministic empty/partial-content controls require a
later complete value and a wrong-content control must time out. The real
lifecycle file rerun is **5 passed**, `final-closeout-pan-lifecycle-uv.xml`.
Prior failed XML is preserved, not retroactively declared successful. A new
whole run will be executed through a durable Job on the frozen repaired source.

Final Job dispatched: `job_e98f4895dafb6ddc426e548f`, target
`ses_be105a81379e8cb1`, frozen Python source `17b33e35`. Output XML:
`audit/terminal/implementation/browser/final-closeout-job-full-repository-uv.xml`.
Log: `D:/project/Pan/data/background_jobs/logs/job_e98f4895dafb6ddc426e548f.log`.
No final result is claimed at dispatch. The Agent enters idle to receive the
terminal notice, then will inspect exit code/XML and complete the handoff.

### Background-run isolation incident (run invalidated)

The above Job run is **invalidated**, not an accepted regression result. The
wrapper removed Agent identity variables but missed the Runner-injected
`PAN_BACKGROUND_JOBS_DIR`. That environment override supersedes tests' patched
`DEFAULT_ROOT`, so some Job tests wrote into the deployed registry. The concrete
`job_retry.json` fixture had no `createdAt`; old `list_jobs` sorts a mixture of
its empty-string fallback and numeric timestamps, raising TypeError. Actual
`GET /api/jobs` and `/api/jobs/next` returned 500.

The registry had already marked the real Runner orphaned/failed, so the ordinary
cancel API returned its terminal record without killing the still-running
process. The recorded owned Runner PID 40500 was independently checked against
its exact psutil creation time 1791128280.5850768 before stopping only its tree.
The offending fixture was verified by jobId/target/creator/missing timestamp and
quarantined, not deleted, at
`audit/terminal/implementation/browser/job-isolation-incident/job_retry-test-artifact.json`.
Both live GET endpoints then returned 200 without a service restart. Other test
artifacts/possible record changes are not yet exhaustively audited; recovery is
not declared complete. Future regression launch must clear inherited Pan data
directory/configuration overrides before invoking tests; notification belongs
to the outer Job Runner, not its isolated pytest child.

The directory-support source change was never deployed. The GUI incident was
triggered by this Agent's test-launch isolation error, exposing an existing
mixed-sort defect, not by the deployed service loading that source change.

### Containment and prevention follow-up

18 source-attributable fixture records have now been quarantined (not deleted).
Live Jobs list/next both return 200; no synthetic fixture target IDs remain.
There is no complete pre-incident registry snapshot, so unknown prior metadata
cannot be reconstructed or certified unchanged. The invalid Job run is not
reused. Exact recovery boundaries and backups are under job-isolation-incident/.

The new isolated test entrypoint clears inherited PAN_* application settings for
pytest only. Public pytest bootstrap/fixtures also isolate Session, Job,
scheduler, Terminal and config storage; tests must explicitly opt into owned
temporary settings. docs/TESTING.md and CODEBUDDY.md document this for future
feature development. Related suites: 132 passed; entrypoint retention/scheduler
suite: 100 passed / 1 skipped. Mixed legacy Job timestamps now use a numeric
read-only sort key without rewriting records. All changes remain undeployed.

Real Terminal/API/WS adjacent regression under the new fixtures: 79 passed
(`job-isolation-terminal-adjacent-uv.xml`). Frozen repaired source `680c9bcf` is
now running in `job_2e3e267da17418da9bac6024` through
`scripts/run_tests_isolated.py`. Final XML target:
`audit/terminal/implementation/browser/final-isolated-job-full-repository-uv.xml`.
Target Session is `ses_be105a81379e8cb1`; no final result is claimed at launch.
The 5h live quota query was 34% used, stale=false, observed at
2026-10-04T16:04:41.296001+00:00. The Agent enters idle to receive completion.

## Human acceptance boundary

Acceptance checkout is `D:/project/pan-worktrees/terminal-final-integration-ma-20261004`,
branch `integrate/terminal-ma-final-20261004`. It contains the React Terminal UI
and PR6 Rewind integration, independently prepared frontend/sidecar dependencies
and the built frontend. Main/practical are not advanced, no push/restart performed.
Real CBC/provider-backed Rewind remains a human/runtime gate: existing machine
tests use an owned fake menu, not a provider account or user's transcript.
