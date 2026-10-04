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

## Human acceptance boundary

Acceptance checkout is `D:/project/pan-worktrees/terminal-final-integration-ma-20261004`,
branch `integrate/terminal-ma-final-20261004`. It contains the React Terminal UI
and PR6 Rewind integration, independently prepared frontend/sidecar dependencies
and the built frontend. Main/practical are not advanced, no push/restart performed.
Real CBC/provider-backed Rewind remains a human/runtime gate: existing machine
tests use an owned fake menu, not a provider account or user's transcript.
