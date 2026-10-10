# Codex native-control recovery and topbar actions

Base: `b01fc674f4d30c4fe0de128dde41ed5a4e29da9c`.
Worktree: `D:/project/pan-worktrees/codex-native-recovery-20261010`.

## Observed production state (read-only)

Session `ses_9050125110e181db`: Worker `worker-1`, worker status idle,
native enabled, ownership mode switching, generation 0, output cursor 0.
No control switch, kill, restart, Session write or service restart was performed.
These facts establish the stuck state, not the original cause of its incomplete
kernel/TUI handshake. Existing production logs do not establish that cause.

## Changes

- TUI topbar retains Restart, delayed Restart, Interrupt, desktop Takeover and
  Kill. Start remains available with no Worker. Authoritative native status can
  supply the Worker id when the ordinary Session/Worker projection is absent.
- Takeover warns in terminal mode that it stops the Worker and opens external
  PowerShell; it is not seamless native/Pan ownership handoff.
- History presentation is separate from actual ownership. A failed Pan handoff
  cannot trap view selection. Unknown/native ownership still suppresses Pan
  input, and the bottom queue remains available. Late native acknowledgement
  cannot reopen a terminal view the user just left.
- Failed POST immediately reconciles GET status without guessing Pan ownership.
  Pending downgrade also preserves the last confirmed ownership.
- Worker consumes only its latest issued handoff's valid late receipt, including
  authoritative rejection state. Superseded/unsolicited receipts and generation
  rollback cannot reopen input. Successful confirmation clears stale errors.
- Returning to an already-confirmed Pan state/no Worker does not require a new
  spawn or completion of pending Session conversion. Actual native/unknown
  ownership still requires confirmation.
- Public errors include a bounded static HTTP code, not free response text.

## Verification

- Isolated Python: native control owner/API/conversion/transport/completion and
  real Codex kernel round-trip: **52 passed**. The real CLI proof uses a private
  home and local synthetic provider, no user account/model traffic.
- Final affected frontend collection: **34 passed**.
- Final complete Vitest with `--maxWorkers=2 --minWorkers=1`: **1392 passed**,
  zero failures. JSON: `vitest-final.json`.
- Chromium real components against intercepted APIs: terminal action visibility,
  queue, input generation, refresh, idle upgrade, rejected-switch navigation and
  ownership-gated composer all passed; page errors empty. This is UI acceptance,
  not production Session mutation.
- Final TypeScript/build: passed, **62 compressed assets verified**. Existing
  large-chunk warning remains.
- `git diff --check`: passed.

### Default concurrency failures retained separately

Candidate intermediate full default-concurrency run: 1388 passed / 2 failed
(1390 collected before two final regression tests were added).
Unchanged base `b01fc674` in a separate worktree: 1382 passed / 2 failed
(1384 collected). Both fail the same exact tests:

1. App responsive WorkspaceRail: opens a deferred detail panel, closes it, and
   reopens with fresh content.
2. EditorPane: loads Markdown preview on demand and retains preview/edit/split
   mode switching.

Both await lazy content and fail to find it within the existing test wait. No
timeouts/assertions were relaxed, and neither file was edited. The baseline
comparison establishes these failures also occur without this patch; it does
not establish their deeper cause or guarantee every scheduling regime.
Raw default candidate/base JSON is retained, not replaced by the passing run.

## Deployment boundary

No merge, push, practical build, protected-service restart or live Worker action.
Main and practical remain at the original base. The user's affected Session has
not been repaired in-place. No full Python repository run or live old-thread
upgrade was performed in this batch; the original handshake failure remains
unconfirmed, while navigation/lifecycle escape and late-receipt recovery are
covered independently.
