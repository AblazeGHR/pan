# Frontend stream/history repair and browser acceptance — 2026-09-22

Base: `28be089`; branch: `fix/frontend-full-e2e-20260922`.
Worktree: `D:\project\pan-worktrees\frontend-full-e2e-20260922`.
All investigation, implementation and validation were performed directly by the MA; no TA was dispatched.

## Confirmed defects and fixes

1. **History response between deltas duplicated a live row.** The rendered object was a clone of the runtime object. History adoption compared object references, so it adopted the same runtime slot twice. Adoption now checks the stable runtime key as well. `sessionStore.interleaving.test.ts` reproduced two copies after two deltas and a history response before the fix.
2. **A second queued question moved before the first completed turn.** Queue mutations updated `currentMessages`/`Session.history` but not the Session transcript. Terminal fallback adopted the second question at the beginning of the runtime region; subsequent canonical alignment then repeated the earlier turn. Queue insertion, edit, deletion and delivery now update the same transcript, including background Sessions; cloned delivery rows preserve their runtime key. Browser failures were reduced to focused tests.
3. **Codex tool-to-assistant transition duplicated the final answer.** A tool delta registered the turn's first assistant alias. Final answer lookup found that earlier alias before the exact answer item. Exact native item identity now wins over compatibility aliases.
4. **Reconnect left a partial answer beside the complete canonical answer.** The terminal frame could be lost while offline, and the frontend ignored `resync.snapshot.details.lastResult`. Reconnect now finalizes matching buffered tasks from the durable snapshot before refreshing history. Snapshot requests include Sessions with live buffers, not just the selected Session. Idle focus refresh remains coalesced; live recovery requests the terminal boundary first.
5. **First observed mid-item delta lost its prefix.** When no previous item existed, the client used the last chunk instead of `stream_text`. It now uses the cumulative body for assistant/thinking rows from the first frame.
6. **Delta hot path performed unnecessary work.** Runtime-key membership used a linear search inside a loop; an identity fallback also called `indexOf` from inside `findIndex`. These are now Set membership/direct indexes. Unchanged live rows retain object identity. Historical Markdown subscribes only to Session ID (links additionally to workdir), avoiding reparsing all visible history on every delta.
7. **Plain-text queue editing failed for the actual rich-text composer.** The composer supplies structured `parts` even for plain text. The backend incorrectly treated edits of those parts as attachment conflicts. Pure-text edits now update both text and parts, including the delivery ledger. Attachment-bearing parts retain the existing conflict guard; mutations remain inside the queue lock and rollback restores the original parts.

No global text-based deduplication or cross-task prefix matching was introduced.

## Validation

- Entire frontend Vitest suite: **74 files, 694 tests passed**. Baseline before the changes was 73 files / 683 tests passed, despite the browser failures above.
- Relevant backend regression: **62 passed** across replay/resync, WebSocket backpressure, history persistence, terminal publication, Steer, Codex Worker integration, structured attachment parts, and unified queues.
- TypeScript build and Vite production build passed. Existing vendor chunk-size warning remains.
- ESLint passed for changed frontend modules and the new browser runner. This is not a claim that unrelated repository lint is clean.
- Pytest reports the environment's pre-existing `Unknown config option: timeout` warning.

## Real browser test contract

Entry point: `packages/web/e2e/frontend-full.e2e.mjs` (`pnpm e2e:frontend-full` after building).
The harness launches the built production React application, real FastAPI HTTP/WebSocket handlers, real Pan queue consumers, real Worker subprocesses and durable Session/JSONL persistence. Only the model CLI boundary is deterministic: CBC compound envelopes and Codex wrapper-shaped deltas/finals come from `tests/support/frontend_stream_cli.py`. No paid provider/model is invoked. This is not validation of a live Luna provider or of the protected production instance.

Browser actions use real card clicks, contenteditable input, Send, queue edit/delete controls, wheel pagination and the back-to-bottom button. Read-only store inspection uses the existing `?panE2E=1` seam. Fault injection is test-only: delayed real HTTP responses/snapshots, duplicated/reordered transport frames and obsolete events through the real broadcaster.

Coverage:

- Four sequential Codex turns interleaved with four CBC compound turns; A→B→A both during streaming and after background completion.
- Loaded-history prefix retention, summary refreshes, canonical/store comparison after every turn.
- 700ms historical-response delay, rapid switching, duplicate transport frames, reversed live delivery, delayed authoritative snapshot.
- Older terminal events replayed in reverse order; obsolete deltas with the same actual Worker identity and generation.
- Physical WebSocket interruption during delta generation, including completion while offline.
- Actual queued text edit and deletion while a real Worker is held at its provider boundary, switching Sessions before delivery, and verification that only edited text reaches the provider/history.
- All **5000** history rows loaded through wheel pagination; streaming, upscroll opt-out, return to bottom, and A→B→A with the entire window retained.
- Browser reload and server cold restart on the identical persisted data root.
- Canonical role/content/order versus store; each mounted virtual DOM row versus its corresponding display index; increasing DOM order and non-overlapping geometry. Collapsed tool groups are checked as groups; canonical/store comparison covers full tool contents.
- A live subscriber rejects transient duplicate fixture answers, not just duplicates remaining after terminal convergence.
- Browser frame intervals/long tasks measured while streaming with 5000 loaded rows; assertions reject p95 ≥100ms or any frame interval ≥1s. These are acceptance limits, not a general performance guarantee.

## Isolation and reproducibility

The runner refuses to start if **8765** is occupied. Each run uses a fresh `packages/web/test-results/full-<timestamp>` data root. `server-identity.json` records checkout, port, actual PID and data root; the evidence also records launcher PIDs. Cleanup terminates only the process tree created by the harness, then asserts the port is free. Restart preserves that run's Session files.

The Python interpreter defaults to the existing `D:/project/Pan/.venv/Scripts/python.exe`; override with `PAN_E2E_PYTHON`. The final run's evidence path, process IDs, scenario results, browser measurements and cleanup are recorded in the companion `frontend-full-e2e-20260922.json`. Full raw frames, requests, screenshots and persisted data remain under its runtime path.

For this machine, shared dependency shims retain an old relative path; invoke the installed tools directly if pnpm's shim fails:

```powershell
cd D:\project\pan-worktrees\frontend-full-e2e-20260922\packages\web
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
node node_modules/vitest/vitest.mjs run --silent
node e2e/frontend-full.e2e.mjs
```

The fake CBC executable can produce a CLI-discovery warning in the test UI; its actual Worker path is exercised successfully. The fixture is not an installed vendor CLI.

The commit hook's `npx tsc -b --noEmit` also hits the stale shared shim path. The same check was run successfully with `node node_modules/typescript/bin/tsc -b --noEmit`; only this commit bypasses hook discovery, without changing the repository hook or shared dependencies.

## Delivery boundary

Changes are isolated to this repair branch. This work does not switch, merge or restart `practical`/`main` or the production 8768 service. Production deployment and live-provider acceptance remain distinct from the browser acceptance recorded here. The automated scenarios protect the confirmed failures above; they cannot prove the absence of every possible frontend bug.
