# Queue receipt, compact edit, native Steer — TA delivery

MA: `ses_aa02ab3cd00ae7d2`. One TA completed the three tasks sequentially; no subagents or test suites were added or run. This branch is a candidate for MA review, not integrated or released.

## Baseline and execution boundary

Canonical main root `D:/project/Pan-main`, branch `main`, HEAD `839d5ff3b494ce4a76c8ababd03c2fd9828be162`. Practical root `D:/project/Pan`, branch `practical`, same HEAD at initial inspection. Main untracked files: `docs/SCEPTER_SESSION_MAILBOX_ARCH_REVIEW_BRIEF_20261002.md`, `docs/design/Scepter-Session-Inbox-Architecture.md`, `packages/web/node_modules.backup-20261003/`. Practical untracked files: `docs/SCEPTER_PHASE1_EXTERNAL_SESSION_POLICY_RELOAD_SPEC.md`, `packages/web/node_modules.backup-20261003/`. Preserved all. No stash/reset/main or practical ref operation, push, service restart, or request to 8767/8768.

Isolated root `D:/project/pan-worktrees/queue-optimistic-steer-layout-20261007`, branch `feature/queue-optimistic-steer-layout-20261007`, created from that main HEAD. Repository/parent AGENTS.md was absent. Read `docs/skills/pan/SKILL.md` and canonical `.workflow/constraints.md` before changes. User screenshot was viewed.

At final inspection canonical main/practical had independently advanced to `e58e3a9846613fc5c574ad735a69ea6c741b05a4` (including delayed restart). Merged this exact main into the feature branch only. Worker/server changes auto-merged; resolved the HTTP reference conflict by retaining delayed restart documentation and the new actual Steer receipt description. Repeated quality checks and backend experiments on the combination. Main/practical dirty lists remained unchanged. The original implementation commit is `7ddcb6e0`; subsequent combination/evidence commits are part of this delivery.

Canonical Python interpreter reused. `package.json` SHA256 `C719902161F24170FAB65116F7BCEDC77E8499A316B5366CDFFCCC0C8946FFC3`, lock SHA256 `249D2FC3A1A0FD511EF440B4F51224F946F1F58DD850060B0BAE7D78A164B149` matched main/practical/worktree. Initial main dependency junction failed tsc because xterm dependencies were absent. Replaced only the owned junction with practical dependencies after matching hashes; no install. Vite subsequently used an isolated cache and native config loader to avoid shared dependency cache writes. Initial default Vite/build startup may have touched standard shared Vite cache; no shared package/source installation or modification was intended. Owned junction is removed before delivery.

Long commands ran through real Pan durable Job Registry/runner with an isolated registry and synthetic Sessions under ignored `runtime/`. This did not contact a canonical service or native user store. Registry terminal notificationState remains pending because no live scheduler was started; completed status, identity, exitCode and logs are captured in `job-facts.json` and `quality.log`. It is not a claim of canonical notification delivery.

## Root causes and resulting behavior

1. Enqueue UI used local Worker status to append a queued body to the conversation. Pause/lock/recovery state therefore appeared delivered even though HTTP only confirmed durable queue storage. Removed enqueue conversation optimism universally; success feedback remains queue/toast. Only server delivery/history produces sent body. Server FIFO also wrote history during reservation before actual handoff; moved that write to the actual handoff callback. Once handed off, a receipt save failure retries persistence rather than requeueing the already accepted body.
2. Queue edit controls and hint occupied separate rows. Combined them in one compact row; the details/attachment/error region and text editor have independent constrained overflow. Desktop resize remains the top edge only. Ordinary RichTextComposer remains mounted, queue attachments stay in the edit transaction, and cancel restores the ordinary draft. Fullscreen and readonly/disabled guards remain.
3. Queue Steer accepts only original item ID plus positive exact revision. Server takes the full body, structured parts and original metadata under lifecycle/queue locks, persists a pending marker before native control, and consumes after authoritative handoff. Pause everything, readonly, locks, revision and any active edit lease fail closed. Native capability is exposed by `/api/adapters`; unsupported adapters hide the button, unsuitable runtime states show a disabled reason. Pending markers block edit/delete/FIFO and survive missing receipts rather than inviting duplicate send.

Codex control now correlates actual `turn/steer` RPC responses with Pan receipts; writing an RPC request alone is not acceptance. Idle/late native control is rejected rather than carried into the next FIFO turn. Claude uses its existing running streaming stdin/drain boundary; there is no separate provider RPC acceptance receipt for Claude. Unknown outcome remains visible/locked without automatic resend. Actual provider deployments were not exercised.

## Identity audit

Provider protocol uses user-role input for both ordinary input and steer. That wire role is separate from Pan `source`, `kind`, `sourceSessionId`, `taskId`, `taskIdSource`, `eventId`, channel/envelope/report/notice identities. Original durable envelopes are retained in `queueEnvelopes`; public attachment parts omit internal server paths, history retains canonical parts. Queue delivery and history carry the same Pan message identity and receipt source. Native reimport matching copies established Pan metadata; a reimport that would drop preserved queue envelope identities refuses replacement explicitly.

Tasks use canonical full text/parts, never the 200-character list preview. Report/system/channel items use existing full formatter with the original structured envelope preserved. Unsupported kinds, unknown parts, stale/missing canonical attachments, or structured report/channel attachments are rejected with an explanation and retained. This is lossless Pan identity preservation, not a claim that a provider understands all Pan semantic envelopes natively.

## Actual isolated evidence matrix

Evidence JSON contains inputs, controls, HTTP response, queue, history and metadata; browser evidence is actual Chromium module/component execution with all API/WS intercepted locally. Backend experiments call the actual endpoint/worker methods and persistence against fixture Sessions, with a scripted native receipt peer. They do not prove live provider/service E2E.

| Input / state | Actual result | Evidence |
|---|---|---|
| paused, absent Worker, recovery, running, all locked, reports pause | Enqueue receipt creates queue=1, chat=0; baseline produced chat=1 in five non-running states | baseline-ui.json, fixed-ui.json |
| HTTP failure | no invented chat/queue; send returns false | fixed-ui.json |
| delivery event before late enqueue HTTP | chat=1 from authoritative receipt, queue=0; stale HTTP does not resurrect item | fixed-ui.json |
| Session switch during HTTP | captured Session receives queue; newly selected Session chat remains empty | fixed-ui.json |
| duplicate receipt | one chat row, queue=0 | fixed-ui.json |
| FIFO reservation, native write failure | no premature history; failure retains queue | fifo-baseline.json, fifo-fixed.json |
| FIFO accepted + transient persistence failure | one history, queue consumed, ack true; baseline requeued an accepted item | fifo-baseline.json, fifo-fixed.json |
| queue Steer accepted, repeated request | one native control/full body, one history, queue consumed; repeat cannot send again | backend-results.json |
| provider rejects | no history, original queue retained with error | backend-results.json |
| pause/manual lock/reports lock/readonly/lease/stale revision/idle/unsupported | zero native controls, no history, queue retained | backend-results.json |
| pending receipt + duplicate/edit/delete/FIFO race | pending visible; duplicate/edit/delete rejected, FIFO has no selectable unit | backend-results.json |
| running/idle with pending_restart flag in combined code | running native control remains legal; idle refuses before native write | backend-results.json |
| task agent source + IDs/event/envelope + text >200 chars | full native body and original metadata retained in history/WS envelope | backend-results.json |
| report, system notice, QQ channel | full formatted native body, original structured metadata retained | backend-results.json |
| canonical attachment | canonical file target sent, parts/identity retained | backend-results.json |
| stale attachment, unknown part/kind, report attachment | explicit refusal, original queue retained | backend-results.json |
| native RPC before response / accepted / rejected / idle late control | no success receipt before RPC response; correlated accepted/rejected receipt; idle request rejected | bridge-results.json |
| native history reimport identity match | Pan message ID/source/envelope/parts preserved | bridge-results.json |
| desktop 1280×800, mobile 390×740, short 320×280, mobile fullscreen | X/✓ visible on same row (32px desktop/36px mobile); crowded attachments/errors do not eject controls; editor scrolls, root/body do not | component-results.json, PNGs |
| desktop resize; cancel edit | only top-edge handle adjusts height; ordinary draft restored, edit lease released | component-results.json |

## Quality and remaining acceptance

Python syntax, tsc -b, scoped ESLint, isolated Vite build, `verify-precompressed-assets.mjs`, `git diff --check`: completed exit 0, see quality.log / job-facts.json. No pytest/Vitest/Playwright suite was run; browser scripts are task-specific direct experiments. Initial missing-xterm failure is dependency environment evidence, not a source regression.

Unverified: actual Codex/Claude provider processes and native store, live canonical HTTP/WS reconnect, real touchscreen/soft keyboard/safe-area behavior, IME composition interaction, successful attachment upload/save transaction through real HTTP, missing-receipt late reconciliation after 30 seconds/process restart, durable hard-crash injection. Source guards for disabled/IME/lease and ordinary draft/attachment separation remain, but are not relabeled as measured. Existing ordinary FIFO cannot guarantee provider exactly-once across a hard crash before the durable receipt reaches disk; queue Steer persists its fail-closed marker before control. An ambiguous Steer remains locked pending authoritative reconciliation; there is no automatic retry/unlock flow.

Shared merge boundary with restart TA `ses_9050125110e181db`: this branch modifies `worker.py` receipt/reservation/commit/control and queue Steer lifecycle locking, plus queue HTTP server paths. It does not implement delayed restart or touch that TA worktree. Queue Steer requires an already running Worker; idle returns an explicit error, so it cannot use an old idle Worker ahead of a pending restart. Running Steer takes the existing lifecycle lock; MA must check the combined delayed-restart changes preserve legal running controls and idle restart priority. No coordination request was sent through forbidden canonical ports.

The final branch includes the already accepted delayed restart from current main. Direct combined experiments exercise running/idle Worker flags; a full delayed-restart/provider lifecycle interaction remains unverified. Existing `_maybe_restart_pending` retirement fences are retained before FIFO handoff.

MA must review this exact commit before FF and practical build. No push/release/real-service restart is authorized here. The commit hash is reported in the TA completion message.
