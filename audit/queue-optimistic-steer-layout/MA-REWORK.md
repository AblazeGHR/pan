# Narrow rework after MA rejection of c726d742

Original branch/worktree retained. Parent is `c726d74278bb47bb221a786b7b063be574feb36c`; main/practical remain `e58e3a9846613fc5c574ad735a69ea6c741b05a4`. No FF, push, service operation, canonical port request, native user data write, shared install, or test suite. All new commands were short foreground operations; no new OSJob or Job polling. Existing historical Job facts in REPORT.md describe the first delivery, not this rework.

## 1. Bridge terminal must not depend on a missing Steer response

Baseline reproduction loads the rejected bridge source via read-only `git show` in an isolated Python module, then supplies real `turn/start` response and `turn/completed` notifications to the real reducer. With the Steer RPC response omitted, `run_turn` did not return, `_turn_active` stayed set, and no result was published.

Removed Steer requests from the terminal-loop condition. Pending RPC correlations now belong to the bridge lifetime and its real reader, independently of a turn. The reader can emit a genuinely late receipt while idle; the next turn can also consume a queued late response. Registration is protected across the request write so a fast response cannot outrun correlation. `run_turn` clears active in `finally`; normal completion clears it before final controls/result. Output writes are serialized across the reader/main threads. Missing requests do not produce success or rejection; at 128 outstanding correlations, new controls are explicitly refused before native RPC write rather than growing without bound. A native request write failure is reported as unknown, not a safe rejection.

Fixed sequence: actual `turn/completed` publishes normal result without a Steer receipt; active is false; a later native RPC response settles only its original request; a subsequent `turn/start`/`turn/completed` publishes a second result. See `rework-baseline.json`, `rework-fixed.json` first entry. No actual Codex provider was started.

## 2. Queue Steer transaction and cancellation ownership

Encoding happens before reservation. Marker persistence uses the existing ordered Session ticket/`DurableOutcome` API, exposed through an optional `save_async(..., outcome=...)` parameter. A cancelled writer's actual success/error is observed; cancellation alone does not mean committed or failed.

Before any control write, cancellation or save error clears the pending/request/Worker identity reservation and runs an independently owned durable cleanup. Rechecks after persistence ensure the captured Worker object, generation, process and running turn still match. A real Task.cancel after the marker reached disk produced zero control writes, no history, and an available queued item on disk. A Task.cancel with a gated writer failing before commit produced the same usable result, with observed first outcome `succeeded=false,error=OSError` and successful cleanup. The post-commit cancellation's first outcome was `succeeded=true`.

The synchronous control write remains inside the queue/lifecycle critical section, preserving pause/lease linearization. Once that write is attempted, a write/drain error is conservatively unknown. Receipt callbacks and an independent finish owner are installed before the caller next yields; cancelling the HTTP/outer task cannot abandon the actual handoff. The finish owner waits for drain/receipt outside lifecycle locks, with a 30-second bound; timeout keeps the item blocked and the future's genuine late receipt callback remains owned. Snapshot/delivery broadcast exceptions are logged without abandoning settlement. Settlement persistence is independently owned and retried once without repeating the control write; persistent storage failure falls back to the existing dirty-history persistence path and remains a durability limitation.

Each attempt retains request ID, captured Worker ID/generation and the original queue object identity. A late receipt may settle that original accepted handoff even after replacement; it cannot consume a new item with the same ID or wake a replacement generation. Transient operational attempt fields are omitted from retained public envelopes.

## 3. Lifecycle boundary

The spawn lock now covers only validation, marker persistence and synchronous write. Neither drain, broadcast nor RPC response wait holds it. Queue lock likewise does not cover receipt wait or delivery broadcasts.

Real `restart_worker`, `kill_worker`, and delayed-restart coordinator/terminal persistence/publication/bookkeeping paths were invoked while Steer awaited a response. All reached the substituted OS stop boundary promptly; no RPC was supplied until afterward. The terminal path retained its normal assistant terminal row and later exactly one accepted original Steer row. The OS boundary intentionally raises a fixture error to avoid any real process/service operation: these experiments prove entry/locking/ownership, not a completed OS restart. Old acceptance did not change the replacement Worker status; replacing the queue item object left the new body/revision untouched.

## Additional FIFO / public receipt checks

FIFO already-accepted handoff cancellation now observes its writer result and hands durable cleanup to an independent owner. Real cancellation with committed first receipt and with failed first receipt both left ack=true, queue empty and exactly one history row on disk; neither case requeued or rewrote provider input. Ordinary write-failure/transient-save experiments remain green.

Public delivery messages are recursively sanitized, including nested retained envelopes. Actual queue Steer and ordinary FIFO attachment delivery events contain zero `__serverPath` keys; canonical attachment paths remain in private history parts. Source/task/report/channel/parts preservation from the original implementation is unchanged. No local queuePaused-only delivery inference was reintroduced.

## Actual result matrix

| Direct interleaving | Control writes | Durable final queue / original history | Result |
|---|---:|---|---|
| marker committed, then Task.cancel, before provider write | 0 | 1 available / 0 | reservation cleaned |
| Task.cancel + writer failure before marker commit | 0 | 1 available / 0 | failed ticket distinguished, cleanup committed |
| cancel caller after control write | 1 | 0 / 1 | owner still settles receipt |
| snapshot/delivery broadcast failures | 1 | 0 / 1 | logged; settlement survives |
| first settlement save fails | 1 | 0 / 1 | persistence retried, no control retry |
| Worker replacement before old acceptance | 1 | 0 / 1 | original item only; replacement stays idle |
| queue object replaced before old acceptance | 1 | new item remains / 0 | old receipt cannot consume new identity |
| write raises after attempt, then actual acceptance | 1 | 0 / 1 | before receipt: pending, history empty; acceptance required |
| Restart / Kill while waiting | 1 each | 0 / 1 after late acceptance | real wrapper reaches isolated OS boundary |
| real terminal → idle → delayed restart while waiting | 1 | 0 / 1 Steer + 1 normal assistant terminal | coordinator reaches isolated OS boundary |
| no receipt for actual 30 seconds, then late acceptance | 1 | pending / 0 at timeout; 0 / 1 after late receipt | no fake success or retry |
| accepted FIFO + Task.cancel, first writer committed/failed | no additional native write | 0 / 1 each | owned durable cleanup, ack=true |
| public Steer/FIFO attachment receipts | — | internal paths retained privately | zero public internal-path keys |

`rework.py` supplies the full deterministic input/disk/event artifacts; `rework-evidence.py` checks and generates `rework-summary.json`. These are direct task experiments, not suite additions. Logs include deliberately injected broadcast, OS-boundary and disk exceptions; Python 3.14 also logs shielded-future disk exceptions when cancellation coincides with writer failure. The real outcome is consumed and cleanup verified on disk; these messages are not unobserved task ownership claims.

## Quality and unverified boundaries

Foreground Python syntax, tsc -b, scoped lint, production build, precompressed verification (56 assets), and diffcheck completed exit 0; `rework-quality.log`. Backend/bridge/FIFO direct experiments completed exit 0. No pytest, Vitest, or browser suite was added/run. Frontend layout is unchanged by this rework; previous component evidence remains historical evidence for that unchanged code.

Unverified: real provider/OS process kill/restart, live HTTP/WS, native user data, true hard-crash recovery, permanent filesystem outage recovery, shutdown cancelling all in-process owners, actual Claude drain failure on a real process. Unknown attempted writes still remain blocked without automatic retry/reconciliation UI. A hard crash after accepted provider input but before its final durable receipt cannot be claimed exactly-once; this rework preserves fail-closed Steer reservation and does not pretend that missing provider evidence is success.

MA must review the new exact SHA. No acceptance, integration, or release is claimed.
