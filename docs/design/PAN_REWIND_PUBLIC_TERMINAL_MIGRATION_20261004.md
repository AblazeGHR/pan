# PR6 rewind: public terminal core migration

## Scope and source

PR6 source `387a43ec` was imported with provenance into this isolated branch
(`d1f3da91`). Its original seven Python files collect 62 behavioral tests;
all 62 passed both before and after the migration. Main/practical and running
Pan services were not changed. Provider commands were not executed.

## Ownership and automation

- `rewind/driver.py` keeps fork/resume arguments, existing imports and the
  `RewindDriver`/`rewind_in_pty` return interface. It owns the final cleanup gate.
- `rewind/automation.py` contains CBC-specific menu, anchor and restore gates.
  Its menu surface wraps `AutomationContext`: lease-mediated input, continuously
  updated observer text, and public runtime exit facts. It owns no process,
  queue, Job or lifecycle primitive.
- `rewind/pty_session.py` assembles real `ConPtyBackend`, retained-handle
  `backend.probe`, assigned Job, `PtyRuntime`, `PyteScreenObserver` and control
  lease. The runtime continuously drains even while menu logic is sleeping.
  Its output log is capped at 256 KiB. Pyte is a partial automation observer,
  not a browser recovery engine.
- The former unbounded queue, pywinpty production spawn, pre-read liveness
  exit, post-termination PID scan, and unjoined private reader are removed.
  Platform-marked pywinpty remains only as a reference dependency, consistent
  with the terminal implementation plan.

## Cleanup gate and retained owners

`completed` is emitted only after `CleanupReport.ok` is true. On cleanup failure
the public result has `success=false`, stage `failed`, the real cleanup report,
and a process-local opaque `owner_id`. The hybrid flow consequently does not
truncate the forked transcript. File restoration facts remain separate from
cleanup facts; cleanup failure does not pretend to undo a restored file.

`driver.retry_cleanup(owner_id)` retries the same retained object, not an
unverified PID. A bounded registry (32 active/retained owners) reserves capacity
before spawn; it refuses new spawn when full. Confirmed cleanup removes the
owner. Atomic-spawn exceptions also retain their original cleanup attempt.
Owner references are process-local: an ID alone is not cross-process authority.
There is no new Web retry endpoint in this phase.

Rewind does not depend on OS Ctrl-C working: final cleanup uses the accepted
retained-handle termination and Job-wide cleanup path, with interrupt disabled.
This does not close the separate interactive Ctrl-C product gap.

## Verification

1. Original PR6 seven Python suites: **62 passed**, unchanged behavioral
   assertions. Only the private restore fake replaced `.proc.isalive()` with
   the restricted `.alive()` interface.
2. Real ConPTY + local fake CBC menu + fake fork: **2 passed** in uv with pyte.
   Success restores a temporary file, confirms cleanup, then truncates only
   the temporary fork transcript. Injected guard failure preserves owner,
   reports failure, leaves the transcript intact, and retries to convergence.
   No provider/account/user transcript is used.
3. Combined original + real tests: **64 passed**; machine-readable evidence:
   `audit/terminal/implementation/browser/rewind-core-regression.xml`.
4. Additional deterministic ownership gates: **5 passed** (direct): admission
   before spawn; original atomic-spawn owner retry; partial input rejection;
   completed event only after confirmed cleanup (positive and negative).
5. React rewind UI/store plus adjacent history navigation: **25 passed**.
6. `pnpm build`: success, **36 compressed production assets verified**.

The direct base environment lacks pyte and therefore skipped the two real
tests; the uv run installed the declared requirements and executed both.
This is not a claim of actual CBC menu compatibility, provider-enabled rewind,
full Pan runtime acceptance, or browser rewind interaction acceptance.

## Remaining gates

The whole combined terminal regression, full repository regression, actual CBC
behavior and deployed Pan acceptance are separate gates. Ctrl-C OS semantics,
durable detach under the real ambient Job, long-run backpressure, remote
authorization and cross-platform behavior retain their prior limitations.
