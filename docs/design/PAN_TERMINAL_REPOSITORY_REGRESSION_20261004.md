# Repository integration regression

The full `tests/` uv selection completed: **2558 passed / 9 failed / 10 skipped**,
596.61s. Original XML is `audit/terminal/implementation/browser/full-repository-uv.xml`.
It is a failed run, not a claim that the repository passed. The run started at
`23e790c1`; independent lifecycle work was completed while it ran, so it is not
represented as an immutable final-HEAD certification.

Six failures were older test expectations already fixed in frozen main
`e6a5e84e`: backend history message IDs (one), worker history identities (three),
Codex ancestor/descendant directory matching (one), and POSIX Windows-prefix
handling (one). The four test files have been synchronized byte-for-byte with
that main snapshot using reviewed patches. No corresponding production files
changed; the explicit message-identity assertions were restored, not dropped.

The Terminal route-registration test assumed the global Pan app had never run
its lifespan. Earlier tests left a real `closing` runtime, producing the correct
503 closing rather than the assumed 503 not-ready. The test now scopes
`app.state.terminal_runtime=None` with monkeypatch and restores the prior owner.
It still checks the real app/router and exact no-runtime response.

The remaining two failures were an incremental-save timing threshold (19.380ms
full vs 9.840ms incremental, narrowly below 2x) and a Windows `socketpair()`
WinError 10055 while constructing an asyncio loop. No performance assertion or
source-validation contract was relaxed, and no underlying cause is claimed
beyond those measured failure facts.

All seven affected test files subsequently ran together: **185 passed**, 15.22s,
`full-failures-targeted-final-uv.xml`. This verifies the repaired expectations and
shows the two transient failures did not recur in that run; it is not a full
repository rerun or a guarantee against resource-pressure/timing failures.

Tests use isolated session stores, deterministic fake CLI providers, and a
fresh loopback test port, never the existing 8768 service. No main/practical
branch, remote push, provider account, or deployment was changed.

## Complete React fixture regression

The first JSON-reported Vitest run recorded 1210 passed / 19 failed across
112 files (`frontend-full.json`). Eighteen failures concerned stale expectations
or shared test state: queue enqueue optional arguments, truthful edit-lease
fixtures, DONE transcript rows, worker/event-patch reset, settings switch counts,
and adapter quick-action placement. The matching existing main test corrections
were reviewed and used only where this tree's production contracts agree.
Notification and removed-edit behavior differ from current main, so their
existing assertions were preserved rather than importing incompatible changes.
One static layout expression now explicitly requires the PR6 RewindStatusBar
between the chat stage and InputRow instead of forbidding that component.

An intermediate run retained in `frontend-full-final.json` has 1227 passed /
2 failed; the incompatible notification/removed-edit expectations were corrected
back to this tree's existing behavior. Final `frontend-full-accepted.json`:
**1229 passed / 0 failed**, all 112 test files. No frontend production file was
modified in this repair. TypeScript and production build subsequently passed;
the asset verifier reported **36 compressed production assets**. This fixture
run is distinct from the real Chromium browser acceptance and real Pan lifecycle
tests, and does not grant provider/account or deployment acceptance.

## Final fixed-source Python run and targeted follow-up

At `db294fd5`, the final whole-repository uv run completed **2569 passed,
2 failed, 10 skipped**, 915.64s (`full-repository-final-uv.xml`). The newly added
natural slow-client test was not in that run's collected set. No performance
threshold failed this time. The two failures have concrete causes:

1. The composition test-only launcher replaced its JSON report while the parent
   held a Windows read handle: WinError 5, followed by a missing final report.
   Report/control-result replace now retries PermissionError within two seconds,
   and exhaustion still raises; assertions and production launcher are unchanged.
2. Shutdown evidence polling exhausted the service state's lifetime stop-resend
   cap. An explicit later close now gets a fresh bounded resend allowance under
   the existing single-flight arbitration lock; internal shutdown polling does
   not reset the cap. A real pressure test additionally showed that a lost final
   IPC reply could leave a successfully exited runner unconfirmed. The runner's
   final identity-bound cleanup record can now deliver that same cleanup fact,
   but engine convergence and independently proven process exit remain mandatory.
   Wrong identity, non-boolean confirmations, retained owner/tree, exit 6, and
   unfinished pipe all reject this alternate report channel.

The affected service/composition/shutdown/pressure selection recorded **98 passed /
1 failed** (`final-cleanup-pressure-uv.xml`): both original full-run failures pass;
the new pressure fixture's eight immediate retries ended before async cleanup
finished. A 20-second total retry deadline retains exact close=exited assertions.
Its final uv rerun passes (`slow-observer-final-uv.xml`): **5340953 total bytes,
5340841 observed by fast connection, zero fast gaps, same PID, explicit close
success**, 22.80s. An observer intentionally did not consume its bounded receive
queue. This is a finite natural-pressure check, not long-steady-state certification.
Initial proxy-environment and premature-close fixture failures are retained.
The nine strict final-record gates plus two shutdown gates pass direct and uv:
**11/11 each** (`final-record-gates-{direct,uv}.xml`).

The whole repository was **not rerun after these last changes**. Its latest whole
run remains 2569/2/10; the repaired subset and pressure result are separate facts.

Final direct-interpreter pressure control also passes: **5340825 total bytes,
5340713 seen by fast connection, zero fast gaps, same PID, confirmed close**,
23.69s (`slow-observer-final-direct.xml`). Thus the finite pressure/cleanup path
has real direct and uv positive controls; neither is a long-duration stress claim.

## Frozen-source whole-repository confirmation

The subsequent immutable run at `4823fc0e` completed **2581 passed / 0 failed /
10 skipped**, 837.54 seconds, with two dependency warnings. The JUnit result is
`full-repository-4823-final-uv.xml`. No Python source or tests were changed while
that run was executing. Both formerly failing tests and the final cleanup-record
and finite pressure tests are in this collected set.

The ten skips are explicit environment/capability boundaries: a gitignored skill
copy, unavailable directory-symlink support, three absent machine-local Kimi
fixtures, three tests requiring a sibling worktree's esbuild, absent zoneinfo
timezone data, and one same-service detach case whose actual ancestor Job refused
durability. That last case is not a positive durability result; previously retained
direct/browser/cross-Pan positive evidence remains separate. No provider account,
existing Pan service, or port 8768 was used. This run does not grant deployment,
cross-platform, long-steady-state, or all-mode rendering acceptance.
