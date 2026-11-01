# R1 WP05b candidate delivery

Source SHA: `f059feacf8dbd85161f64e43e44481897b1e4f03`. Baseline: `ee0af90fc4cab507c92f9b99691d68398c5c7618`. Branch: `r1/wp05b-independent-20261025`.

Canonical independent execution preserves caller Job/Run/Step/Attempt/effect identities; intent,
lease/epoch/generation, supervisor claim, external receipt, process/tree proof and outbox are durable.
Protected service.restart/exit records phases and closes cancellation before stop. Restart needs
new PID/birth/root/port/HTTP-ready evidence; exit needs affirmative offline proof. The worker waits
for a retained-handle bootstrap exit receipt before claiming the effect.

| Validation | Result | Exit |
|---|---|---|
| Affected contracts/regressions + import gates | 375 passed, 20 skipped (50 new contracts) | 0 |
| Extra candidate deletion combination | 1 passed, 1 failed | 1 |
| Original baseline deletion control | same 1 passed, 1 failed | 1 |
| Full tests/ collection, candidate and baseline | same 4 errors: missing playwright | 2 each |

Skipped tests: 18 real POSIX contract variants, 1 POSIX-only bridge check, 1 opt-in external
Task Scheduler host acceptance. No new skip/xfail or assertion weakening. The existing deletion
failure is `tests/test_job_delete_main_combination.py::test_authorized_action_delete_preserves_active_snapshot_and_legacy_guard` (`KeyError: deleted` after uncertain finalization).
Raw red/green JUnit and collection logs remain locally under `.validation/wp05b/`; committed
[manifest.json](manifest.json) contains only sanitized nodes, counts, commands, SHA and digests.

Physical legacy callsites deleted: **0**, per the explicit R4 retention boundary. New canonical
paths bypass JobStore.create/retry, system_actions.prepare/finish and the old lifecycle writers.
Legacy /jobs and old main POST/CLI still use their existing implementations; all live-resource
ownership/query/cancellation and pending outboxes are retained. This is not an R4 retirement claim.

No main/practical advancement, push, existing service start/stop, 8767/8768 operation or real profile
access. Main lifecycle effects were mocked. Real Pan restart/exit, external host acceptance,
Session/QQ delivery, full backend execution, POSIX runtime/remote CI, migration, browser/build,
deployment and human acceptance remain unverified. WP06 owns product transport wiring.

Implementation/usage and lifecycle contract: [WP05b execution](../../docs/design/job-unification/WP05B_EXECUTION_20261026.md).
