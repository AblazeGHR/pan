# P6 legacy HTTP/MCP multi-entry compatibility candidate

Baseline: `f18a7eab3872cd99cc348d96b516524c2b9c7491`.
Branch: `implement/action-unification-p6-20261106`.
Worktree: `D:/project/pan-worktrees/action-unification-p6-20261106`.
Candidate identity is the commit containing this report (`git log -1` after commit).
This is an incremental candidate for MA review; P6 remains incomplete.

## Original gaps and changes

`P6_CONFIG_05B` originally reached real isolated HTTP but lost per-entry
`graceSec` and policy in `_validate_schedule`. The retained original red
(`http-mcp-policy-original-red.xml`) fails with missing `graceSec`. The older
`http-mcp-multi-focused.xml` passed only count/pause assertions and did not
establish heterogeneous policy parity. It is retained with that limitation.

HTTP now accepts 1..64 schedule objects and preserves each spec's policy,
grace and enabled fields. Invalid nested/empty schedules, invalid policy,
non-boolean enabled and non-finite/out-of-range grace reject without creation.
New creation retains the old rule: client-supplied identity/cursor fields do
not supply execution identity; server-generated IDs represent new work, not
legacy adoption. Existing offline handoff still requires original proof.

MCP `scheduler_create` adds `schedule_entries` while retaining the old required
`kind` and flat arguments. It uses the same authenticated legacy HTTP adapter,
FinalService owner/operation receipt and existing canonical graph.

Extended HTTP testing found cursor restoration using the immutable adoption
snapshot, with noncanonical timestamp representation. That intermediate red
is retained. Entry edits now snapshot live canonical cursors before changes
and restore both `nextFireAt` and `nextOccurrence` through existing mapping.
Entry CAS remains active after deleting down to one rule. Lifecycle changes
require the existing eventCreator lifecycle grant. Query adds entry `revision`
and `paused` without removing old task fields. TimeProvider drive reason uses
the rule's own grace, consistently with its existing misfire outcome decision.

## Field/entry matrix

| Legacy HTTP | MCP | Canonical successor / evidence |
| --- | --- | --- |
| create `schedule` object | original flat kind/at/interval_sec/cron/timezone | Existing single-rule path unchanged; CONFIG_01/05 |
| create `schedule` list | additive `schedule_entries` | One existing Job/Creator/Trigger graph, ordered rules; CONFIG_05/05B |
| entry `misfirePolicy`, `graceSec`, `enabled` | fields inside schedule_entries | Rule overrides; CONFIG_05/05B/05C/08; no silent Creator fallback |
| `entryId`, `expectedEntryRevision` | `entry_id`, `expected_entry_revision` | Same binding identity, entry CAS and operation receipt; CONFIG_05B/07/08 |
| `entryPaused`, `entryEnabled` | `entry_paused`, `entry_enabled` | Existing Creator lifecycle grant/fence, retained cursor; CONFIG_05/05B/07/09 (enabled path not fresh real-stdio exercised) |
| `entryMisfirePolicy`, `entryGraceSec` | `entry_misfire_policy`, `entry_grace_sec` | Same Creator rule update; HTTP CONFIG_05B, direct CONFIG_08 |
| `deleteEntry` | `delete_entry` | Existing rule removal, remaining IDs/order preserved; HTTP CONFIG_05B, direct CONFIG_07 |
| task `revision`, `generation` | unchanged returned fields | Existing owner/receipt/CAS; CONFIG_01/05B and JSON suite |
| rule cursor/revision/paused | unchanged schedule list plus additive revision/paused | Live Creator cursor projection, cold CONFIG_05B |
| `interval_sec` / `intervalSec` | flat interval_sec | Normalize every returned rule to intervalSec; original object shape retained |
| nextRunAt/nextFireAt, lastRunAt/lastFireAt | unchanged flat task envelope | Existing aliases retained; no new ledger |
| Run/Attempt/effect/history | scheduler_runs / run_now | Existing frozen identities/receipt chain; combined JSON receipt suite and CONFIG_05 |
| missing identity/receipt, unknown lease/writer | unchanged preserve/blocked result | Original bytes retained; JSON_02/05/11, receipt suite; no synthetic adoption proof |

This table distinguishes implemented mappings from full transport acceptance.
The stdio test uses a real standalone process and owned IPC Bridge, with
synthetic Sessions and explicit fixture grants. It proves transport/adaptation,
not a real external CLI/QQ Worker consuming the request.

## Verification and provenance

Evidence root: `evidence/action-unification-p6/`.
`http-mcp-evidence-index.json` records source working-byte SHA256, JUnit hashes
and exact result counts. Python invocations use `scripts/run_tests_isolated.py`
with Python 3.14 and the existing tzdata path; no real profile/data.

| Layer | Result |
| --- | --- |
| task_config + json_receipts + json_scheduler + scheduler_api | 83 passed, zero failure/error/skip; http-mcp-final-combination.xml |
| per-rule grace scheduled/skip outcome | 1 passed; http-mcp-rule-grace.xml; added after combination collection |
| lifecycle grant and mixed-field rollback guards | 2 passed; http-mcp-entry-guards.xml; added after combination collection |
| permission-only intermediate run | 1 passed; http-mcp-entry-permission.xml; overlaps guard suite, do not add counts |
| permission test harness experiment | Preserved red: invalid error-code name in test; replaced with real grant denial, not product failure |
| frontend SchedulerInstantInput | 6 passed; pnpm exec vitest run src/components/automation/SchedulerInstantInput.test.tsx |
| production build | Same-task pre-stop pnpm build passed (tsc/Vite, 2590 modules, 90 compressed assets); no frontend source changes since |
| real Chromium full chain | FAILED visual-order-round: two terminal role mappings appeared unknown; red summary retained |
| static | git diff --check and python -m packages.jobs.final_contracts --check passed |
| remote CI / Linux / macOS | Not run |
| real provider / actual data migration / deployment / human acceptance | Not performed |

Chromium used disposable server/profile on 8765; raw ignored artifacts remain
under `packages/web/test-results/full-1794002676345`. Committed red summary
retains failure, stage labels, original evidence hash and cleanup portFree=true.
On continuation the owned server PID was absent and no 8765 listener remained.
The failure has not been established baseline-equivalent. No frontend fix or
browser pass is claimed. No 8767/8768 operation occurred.

## Still open and acceptance checklist

- This slice does not close full multi-entry editing parity. Bulk replacement,
  mixed task+entry atomic edits, new entry insertion and final-entry deletion
  need explicit successors and tests. Mixed edits now reject atomically instead
  of silently ignoring task fields; this guard is not a completed successor.
- Schedule replacement with a changed temporal rule needs separate cursor and
  policy-retention review. This slice does not prove its complete old semantics.
- Fresh real stdio covers multi-create/pause and old flat create/edit/run/history;
  it does not exhaustively exercise every enable/delete/policy alias through all
  native/gateway transports. Concurrent per-entry HTTP requests, stale lease
  races and notification-once across those edits need dedicated follow-ups;
  combined existing owner/CAS/receipt tests are not substitutes for all of them.
- Retained pure old logs without canonical Run/Attempt/effect identity or durable
  receipt remain unknown/preserve/blocked. Pending/active source handoff still
  requires trusted original evidence; no identities were fabricated here.
- Resolve the Chromium red independently, then run a focused multi-entry GUI
  acceptance path with a fresh production bundle. Current browser run is a chat
  full-chain harness, not a multi-entry editor acceptance.
- Before integration: MA source/evidence review, remote three-platform CI and
  platform-specific HTTP/MCP checks. Before real migration: reviewed dry-run,
  unique writer/lease and byte/identity reconciliation, then explicitly approved
  apply and manual acceptance. No real migration or deployment is authorized by
  this candidate.

Normal repository pre-commit hook is used. No push, deployment, protected ref
movement, real profile/data or protected-port interaction is part of delivery.
