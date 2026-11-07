# P6 per-rule misfire policy and grace candidate

Baseline: `adcd124a133fec4e0f3ecd957c34c2e623c47676`.
Branch: `implement/action-unification-p6-20261106`; isolated worktree. This
candidate is incremental and does not claim overall P6 completion.

## Canonical schema and migration

The generated canonical scheduler config schema now permits optional
`misfirePolicy` and `graceSeconds` on every time rule, while retaining the
Creator-level fields as defaults for older callers. The schema was regenerated
through `python -m packages.jobs.final_contracts --write`; only the generated
scheduler config artifact changed semantically.

`legacy_schedule_plan` freezes each old entry’s policy and grace into its rule.
The multi-entry handoff merges those rules into one existing time Creator while
retaining the old entry IDs, per-entry cursors, task-level earliest
`nextFireAt`, and ruleId dispatch keys. The Creator-level policy remains the
old task default; it is no longer used to overwrite an explicit rule override.

`TimeProvider.prepare` chooses `rule.misfirePolicy`/`rule.graceSeconds` when
present and falls back to Creator defaults otherwise. This keeps old entry
misfire and grace behavior through the existing CreatorRuntime and receipt
chain; no second scheduler or policy engine was introduced.

Per-entry update accepts `entryMisfirePolicy` and `entryGraceSec` along with
`entryId` and `expectedEntryRevision`. The operation is owner-fenced and
idempotent. A stale entry or task revision rejects before mutation; replay
returns the original operation. Pause/resume/delete continue to preserve
entry cursor and order. The heterogeneous policy test proves `skip/7` survives
adoption, changes to `fire_now/2` through one entry CAS, and rejects a stale
revision.

## Evidence

| Layer | Result | Evidence |
| --- | --- | --- |
| JSON receipts, scheduler, HTTP API, multi-entry and policy | **77 passed**, 0 failure/error/skip | `per-rule-final-combination.xml` |
| Heterogeneous policy migration and per-entry CAS | Included in the 77; focused test passed | `test_P6_CONFIG_08_multi_entry_heterogeneous_policy_rule_override_and_cas` |
| Notification timing/cold/no-resend | 4 passed in prior stable owned evidence | `notification-cold-selected.xml` |
| QQ accepted-unknown cold/no-resend | 2 passed in prior stable owned evidence | `qq-cold-existing.xml` |
| Cancellation/unknown capacity | 1 passed in prior stable owned evidence | `cancel-cold-selected.xml` |
| Source/static | AST, final contracts, generated schema and diff checks passed | command log |
| Production build/Chromium/remote CI/Linux/macOS/real data/deployment | Not run | unverified |

Unknown or missing durable receipt still preserves original bytes and blocks
adoption. No provider replay, second ledger or new execution engine was added.
No push, deployment, protected-port operation or real data/profile operation
occurred; `main` and `practical` are unchanged.

## Remaining gaps

Per-entry schedule lifecycle is covered for the safe fields above, but full
legacy multi-entry editing parity still needs broader HTTP/MCP field-matrix
coverage. Real provider/CLI/QQ Worker consumption, remote CI, cross-platform
acceptance, production build/browser, real-data migration, deployment and
manual MA acceptance remain unverified.
