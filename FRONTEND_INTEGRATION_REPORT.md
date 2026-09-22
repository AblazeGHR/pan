# Frontend integration report — 2026-09-22

## Integration target

- Worktree: `D:\project\pan-worktrees\frontend-consolidated-20260922`
- Branch: `feature/frontend-consolidated-20260922`
- Base: `a6a2ed53ac17d5be8f043d8110d21686bf7489a0`
- Boundaries: no merge to `main`, no push, no access to the 8768 service.

## Selected changes

### `94b62e3` — cold history reads off the FastAPI event loop

Selected from `5110f28`. It keeps JSONL/list cold reads from blocking dashboard
WebSocket traffic and adds bounded-copy, race, paging, heartbeat, and real cold-load
tests. This complements the message-consistency work rather than replacing it.

### `44b9ccd` — narrower sidebar subscriptions and recovery coalescing

Selected from `b324aa1`. It narrows Zustand subscriptions for Sidebar, TopBar, and
SessionList, memoizes SessionItem preview derivation, and coalesces duplicate browser
recovery signals. The commit applied cleanly over the repaired WebSocket pipeline;
all ordering and reconnect unit tests were rerun afterward.

### `6534682` — queue edit/send contract, ported onto the repaired stores

Selected behavior from `c6a5c93`, not its old store implementation. The integration
keeps the current canonical-id, tombstone, equal-revision repair, pending projection,
and terminal convergence logic. Added contracts cover edit-vs-send exclusion,
single edit transactions, Session switches, optimistic projection updates, canonical
delivery convergence, and cancel behavior. An authoritative snapshot also closes an
edit whose item disappeared.

The old queue test directly fabricated a queue-looking history row. It was adapted to
use `appendQueuedMessage`, because only registered pending projections may be edited;
canonical history must not become editable from a matching-looking ID.

## Explicitly discarded or already covered

- `4d3dae1` Markdown line links: discarded after semantic comparison. The base already
  contains a more complete implementation, including attachment/editor links, UNC and
  root-relative Windows paths, cleanup, and broader tests.
- `604eeef` / `b6aae1a`: discarded. They are explicitly marked experimental or
  investigation-only; the useful subscription work is superseded by `b324aa1`.
- `c9c6ebc`: discarded as superseded by the later running-worker optimistic projection
  fix already in the base.
- `7dc8bea` / `ea45c71`: discarded as practical-specific investigation history whose
  consistency goals are covered by the `591367a -> a6a2ed5` repair chain.
- Toast changes `0175268` / `bc805af`, attachment rendering `e1d11a9`, and frontend
  performance phase 1 `bd4ec14`: already present or patch-equivalent in the base.
- Codex quota branch `dd9ef95` / `466782c`: not included. It is a separate provider
  feature and does not belong in this consistency/performance integration batch.

## Regression results

- TypeScript: `tsc -b` — passed.
- Frontend Vitest: 73 files / 649 tests — passed.
- Queue-edit targeted suite: 3 files / 84 tests — passed.
- Reconcile protocol pytest: 3 passed.
- Message ordering probe: 9/9 passed.
- Event-loop offload pytest: 13 passed.
- Real 120k-row cold-history HTTP/WS E2E: passed on the single permitted retry. The
  first run reached the final assertions but the out-of-process heartbeat probe
  produced zero cold-window samples; no product/content/order assertion failed.
- Chromium CBC strict E2E on isolated port 8801: 8/8 passed after building the React
  dist. The first harness attempt received HTTP 503 because dist had not yet been
  built; it was an environment precondition, not a product assertion failure.
- Vite production build: passed. Existing chunk-size warnings remain.
- Full Python suite: 1 known baseline failure; all other tests passed. The failure is
  `test_codex_quota_api.py::test_http_and_mcp_quota_permission_boundaries_are_explicit`,
  which expects `account/rateLimits/read` in an MCP docstring. None of the selected
  changes touch the quota implementation or its documentation.

## Remaining acceptance gaps

- No real provider/model run.
- No production 8768 run.
- The cold-load E2E remains timing-sensitive at the external probe layer, although its
  retry passed and all deterministic offload tests pass.
- Steer still lacks a server-side stable request identity and receipt query protocol.
