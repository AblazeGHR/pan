# evidence/ — raw evidence index (baseline 591367a)

All commands were run from the isolated worktree root
`D:/project/pan-worktrees/frontend-reaudit-protocol-ds-20260921`
at HEAD `591367a65f88e9d5270e8e99920ef5448d1d68c9` (detached).
Python is `E:/software/miniforge/python.exe` (3.12.12). No product file was modified.

## Probes (new investigation scripts)

| File | What it drives | Real code under test |
|---|---|---|
| `probe_protocol_provider.py` → `probe_protocol_provider.out.json` | real worker stdout consumer over a protocol-shaped fake process; adapter projection functions directly | `packages/core/worker.py::_read_stdout`, `_persist_terminal_state`; `adapters/codex/{adapter,sessions}.py`; `adapters/cbc/{adapter,sessions}.py` |
| `probe_server_ws.py` → `probe_server_ws.out.json` (+ `.log.txt`) | real server broadcast/enqueue/coalesce/snapshot path with a fake WebSocket | `packages/web/server.py::broadcast`, `_OutboundClient`, `_resync_snapshot`, `_send_ws` |
| `probe_hist_persisted.py` → `probe_hist_persisted.out.json` | real JSONL persistence writer against a temporary `SESSION_DIR` | `packages/core/session.py::_save_body`, `_hist_persisted`, `append_history`; `worker.py::_persist_terminal_state` + `_flush_history_now` |
| `probe_frontend_ws.cjs` → `probe_frontend_ws.out.json` | real `ws.ts` cursor logic + real `appendEventToMessages`/`extractBlocks` (esbuild bundle, in-memory only) | `packages/web/src/services/ws.ts`, `packages/web/src/hooks/useWebSocket.ts`, `packages/web/src/utils/messageIdentity.ts` |
| `probe_frontend_reconcile.cjs` → `probe_frontend_reconcile.out.json` | real zustand `sessionStore.reconcileWorkerResult` reducer (incl. the turn-reorder / id-less duplication cases) | `packages/web/src/stores/sessionStore.ts` |
| `probe_real_cbc_schema.py` → `probe_real_cbc_schema.out.json` | schema scan of **103 real CodeBuddy transcripts** under `~/.codebuddy/projects` (keys/value-types/id-presence only, never message text) | real cbc on-disk format vs `packages/core/adapters/cbc/*` + `server.py::_api_history` |

`probe_frontend_*.cjs` borrow the sibling worktree's esbuild
(`D:/project/pan-worktrees/frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/esbuild@0.21.5`)
read-only; the compiled bundle is written next to the probe at runtime and deleted
afterwards, so these two `.cjs` files require that sibling path to still exist.

## Commands and exit codes (as executed)

```
E:/software/miniforge/python.exe evidence/probe_protocol_provider.py > evidence/probe_protocol_provider.out.json   # exit 0
E:/software/miniforge/python.exe evidence/probe_server_ws.py evidence/probe_server_ws.out.json                   # exit 0
E:/software/miniforge/python.exe evidence/probe_hist_persisted.py evidence/probe_hist_persisted.out.json        # exit 0
E:/software/miniforge/python.exe evidence/probe_real_cbc_schema.py                                              # exit 0  (103 real transcripts)
node evidence/probe_frontend_ws.cjs > evidence/probe_frontend_ws.out.json                                        # exit 0
node evidence/probe_frontend_reconcile.cjs > evidence/probe_frontend_reconcile.out.json                          # exit 0

E:/software/miniforge/python.exe -m pytest tests/test_reaudit_protocol_provider.py -q                            # exit 0  (9 passed, 10 xfailed)
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_protocol_provider.py --runxfail --tb=line          # exit 1  (10 failed — the reproductions)
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_protocol_provider.py -v -rxX -p no:randomly        # exit 0
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_frontend_reconcile_order.py -q                     # exit 0  (1 passed, 2 xfailed)
E:/software/miniforge/python.exe -m pytest tests/test_reaudit_frontend_reconcile_order.py --runxfail -q           # exit 1  (2 failed — turn reorder / duplication)
```

Existing-suite baseline for the touched modules (all exit 0; `baseline_test_*.txt`):

```
tests/test_codex_adapter.py            exit 0
tests/test_codex_worker_integration.py exit 0
tests/test_cbc_models.py               exit 0
tests/test_cbc_oneshot_args.py         exit 0
tests/test_cbc_import_guard.py         exit 0
tests/test_delivery_semantics.py       exit 0
tests/test_terminal_broadcast.py       exit 0
tests/test_ws_backpressure.py          exit 0
tests/test_replay_resync.py            exit 0
tests/test_worker_history.py           exit 0
tests/test_steer_history_consistency.py exit 0
```

## Git identity

```
git rev-parse --show-toplevel  -> D:/project/pan-worktrees/frontend-reaudit-protocol-ds-20260921
git rev-parse HEAD             -> 591367a65f88e9d5270e8e99920ef5448d1d68c9
git rev-parse --git-common-dir -> D:/project/Pan/.git      (registered isolated worktree)
git status --porcelain         -> (clean tree at audit start)
```
