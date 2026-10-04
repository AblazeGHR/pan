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
Complete candidate whole-repository testing has not been run. Dependency warnings
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
