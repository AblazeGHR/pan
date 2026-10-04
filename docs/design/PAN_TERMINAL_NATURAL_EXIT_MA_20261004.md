# Natural shell exit: direct MA repair and verification

## Failure observed in a real browser

The Chromium/React/real ConPTY controls in `natural-exit-initial` and
`natural-exit-process-control` send `echo NATIVE_FINAL_中文&exit 7`.
The tail reaches the browser, but the record and input role stay running/control.
An authenticated, owned diagnostic connection independently observes root exit
code 7, `reader_done=true`, `drain_stop_reason=eof-timeout`, and runner state
running. This is a real missing lifecycle transition, not a failure inferred
from the launcher PID. The earlier immutable 2604-test result did not cover it.

## Repair boundaries

- Runner: require actual retained-root exit and finished drain with a genuine
  integer DWORD code; UNKNOWN, missing code and unfinished reader do nothing.
  A five-second tail grace permits live consumers to read. Without a consumer,
  the watchdog uses the existing single-flight whole-Job cleanup; an exhausted
  cleanup window reports failure rather than claiming exited. Detached terminals
  also stop after their actual root exits; this does not revoke live detach.
- Service: consume authenticated completed-drain metadata only after an empty,
  non-gap page at the final frontier. Reuse the same tracked stop, retaining owner
  and secret until runner cleanup, engine cleanup and identity-bound launcher
  death all agree. Cache shell code separately from runner cleanup code 0.
- Browserless GET/list: consume only already-completed three-proof cleanup; do
  not launch stop from a query. The final record binds runtime exit metadata to
  the same runner identity. The new `natural-exit` reason is a MA-approved
  lifecycle classification, not a new IPC operation or protocol version.
- Heartbeat: `Thread.join()` returns None, not a success flag. Check thread
  liveness, release its connection in thread-finally and retain the owner if it
  is still in flight. This fixes two independently reproduced ownership bugs.
- WS: reader completion does not imply sender completion. Wait at most two
  seconds for FIFO tail/end delivery including the in-flight frame, then use
  normal close 1000. Failure/slow sender uses 1013. A closed owner's unavailable
  interval is reported as gap then terminal-state, not an endless resume wait.
- React: revoke input immediately on transport close, but on normal 1000 let the
  existing render FIFO apply received tail/end frames. Abrupt close invalidates
  queued frames. A closed socket alone never proves the shell still runs.

Output completeness remains a separate fact: ConPTY natural root death can end
the bounded reader drain without pipe EOF. Code 7 does not manufacture EOF or
`output_complete=true`; the UI explicitly reports unconfirmed completeness.

## Evidence

All new evidence is under `audit/terminal/implementation/browser/`; older
evidence is retained without rewriting its outcome.

- `natural-owner-pre-v2.xml`: 9 failed / 6 passed before Python repair.
  The original pre file has the same count but one fixture incorrectly called
  the keyword-only heartbeat constructor; that fixture error is not a product
  finding. v2 corrects it and independently reproduces the join-return bug.
- `natural-exit-focused-direct.xml`: 22 passed / 33 deselected, covering actual
  browserless exit plus proof/type/tail/heartbeat gates and sender stall gates.
- `natural-end-frame-final.xml`: 3 passed / 53 deselected, including the ended
  gap case and the delivered/stalled sender cases.
- `natural-exit-ws-final-uv.xml`: 65 passed; this collection preceded the final
  added ended-gap test. It is not described as a 66-test run.
- `natural-render-pre.json`: the queued-tail/graceful-close fixture fails before
  the frontend repair; `natural-render-final.json` confirms panel/keyboard/stream
  30 passed. Scoped ESLint and TypeScript/build pass (36 compressed assets).
- `natural-exit-owner-post/result.json`: actual Chromium receives Chinese tail,
  terminal-state exited with code 7, `output_complete=false`, reader/process facts
  true; screen tail preserved, input disabled, public registry exited/code 7,
  harness exit 0. No provider or pre-existing Pan service is involved.

- `natural-owner-neighbor-direct.xml`: 190 passed / 1 skipped, 388.74s,
  service/rework/runner/WS/WS-MA and the original 15 gates. It is an iterative
  neighboring run: the final gap guard and record-sync adjustments were made
  while it ran, so it is not called an immutable final-source run.
- `natural-owner-final-source-direct.xml`: 23 passed / 33 deselected on final
  source, including browserless proof and ended-gap delivery. Final real browser
  rerun `natural-exit-final-source/result.json` passes with harness exit 0.
- Complete frontend `frontend-natural-exit-final.json`: 1247 passed / 0 failed /
  0 pending; this is separate from Python and browser acceptance.

A targeted run is not substituted for a new whole-repository run. The earlier
2604-test immutable run remains a historical source-specific result.

## Limits

### Public record/read consistency follow-up

The final stream event now revokes all further commands before the close frame
arrives. Its typed terminal status triggers one authoritative list refresh while
retaining the selected final screen. Known exited/lost owners no longer offer
reconnect/detach; cleanup-failed owners still retain retry. These two new UI gates
fail before the follow-up and pass afterwards (32 panel/keyboard/stream gates).
The refreshed selector and unchanged tail are measured in actual Chromium at
`natural-record-ui-browser-final` and `natural-public-exit-browser-final`.

REST/MCP's shared read projection adds optional `exit_code`, `output_complete`,
`process_exit_seen` and `reader_done` only when evidence is supplied. Exit code
must be a genuine integer DWORD, flags must be real bool; invalid/unknown is
null, not coerced, and absent evidence does not add invented metadata. No secret
or arbitrary diagnostic field is exported. Five pre-fail/one positive control
become six passing gates; REST/MCP neighboring uv run is **69 passed**, including
its real ASGI create/read/snapshot/close case. Actual final browser REST read at
the sent frontier confirms exited/code 7/output_complete=false.

`frontend-exit-record-final.json` is the complete follow-up frontend result;
earlier 1247/1242 results remain attached to their earlier source versions.
TypeScript/build (36 compressed assets) and scoped ESLint pass. No deployment
or existing Pan restart is implied.

The additional ended-record selection gate fails before repair (one failed /
three passed), then panel/stream selection passes **21/21**. Selecting a known
exited/lost record no longer constructs a new WS stream; it reports the actual
record limitation instead. It does not dispose the still-selected final tail
on an automatic list refresh. `ended-selection-reselect-control/result.json`
measures original tail/code 7/read metadata, then explicitly selects empty and
the exited record: the historical-screen notice appears, reconnect is disabled,
WS connection count does not increase, harness exit 0. Both manuals state that
ended screens are not permanently archived. Build/scoped ESLint pass; only TS,
fixtures and docs changed during the frozen Python run.

Finite local Windows/Chromium checks only. No new claim about old Windows builds,
POSIX, all browser engines, arbitrary native TUI fidelity, cross-user/network
authentication or hours-long durability. Main/practical, deployed assets and
port 8768 remain untouched. The exit fallback retains the existing conditional
OS-cleanup boundary rather than promising a hard kernel SLA.
