# Session dynamic resume revision 5

Baseline: `1553e47b2e6adab22c2261eaacfbcceba6800ee0`. Candidate branch remains the original `r6/job-source-receipt-20261029`.

## Mapping

The old legal-running business path was extracted from the retired scheduled helper into `packages/actions/session_resume.py`. It keeps `legal_offline.is_illegal`, current `worker.find_alive_worker_by_session`, sorted Session IDs, and the existing `worker.wake_session` boundary. Wake therefore retains configured wake text, Illegal lock handling, queue pause and no automatic worker spawn. Per-fire identity is `job-resume:` plus the canonical digest of the child effect key.

`product_resume_legal_running_v1` is an additive immutable FinalService publication. `_drive_run` uses the existing Trigger/Job authority, Attempt and effect admission, then stores one `resumeSelections[stepId]` in the mutable Run execution metadata before the first provider await. Missing selection and selected `[]` are distinct. The frozen definition/input and generic schema remain unchanged. Every later attempt or cold reopen uses the saved IDs; it never scans again or replaces them.

`TypedExecutor` adds a narrow `wake_session` provider method and a `resume-legal-running.v1` operation. Empty selection returns a completed parent receipt with `matchedCount: 0` and `results: []`; no Wake, queue item or Worker is touched. Partial or unknown child outcomes remain partial/unknown and are never replayed. `recover_session_receipts` reads only saved IDs and original queue idempotency identities. It does not compare current configured text, rescan candidates or synthesize a receipt.

`FinalRuntime.query_original` retains raw effect-key lookup and existing `targets` handling. Only a proven `product_resume_legal_running_v1` parent may use its `results` children; each child must match the parent Run/step/operation and a saved selected target. The query adds the actual `job-resume:` identity through the shared helper. Foreign business results, changed keys, unrelated targets and arbitrary result payloads cannot create a parent match.

## Verification

Commands used the owned isolation wrapper and temporary Sessions/random HTTP ports.

| Evidence | Result |
| --- | --- |
| `fifth.xml` | **20 passed** focused resume suite before the final extra negative/authority cases |
| `owned-regression.xml` | **79 passed, 0 failed, 0 skipped**: final 24 resume tests, Session product HTTP/queue closure, canonical Card/source suite and four historical Card parameters |
| `combined-final.xml` | **86 passed, 12 failed, 0 skipped** across the same groups plus scheduler template tests; all 12 failures are pre-existing scheduler environment/fixture issues (`provider_unavailable` or missing `tzlocal`), outside owned scope. The four historical Card parameters and canonical groups pass in this report. |
| `source-scope.json` | Source AST scope check: only approved `FinalService._install_product_actions`, `_drive_run`, `recover_session_receipts`, `FinalRuntime.query_original`, typed provider/executor additions and owned helper/tests changed. Original `tests/test_jobs_api.py` is unchanged. |

The final owned suite records before-effect disk proof and effect-key/client-message identity in JUnit properties. It covers fresh fire selection, live queue persistence, per-fire new identity, empty success/no backlog, configured wake text, authority revocation, missing/unreadable selection, partial results, host death before/after the effect boundary, cold reopen without rescan/resend, immutable frozen plans/publications, live/cold/archived `query_original` by client ID and queue item, and foreign business-result rejection.

The original five `tests/test_jobs_api.py` resume nodes remain unchanged and their exact red evidence is in `original-red.xml`/`.log`. The current public legacy endpoint still reports those historical failures; this candidate proves the canonical successor through owned authenticated HTTP and the existing scheduler composition boundary. `tests/test_scheduler_time_templates.py` was run only as a related regression and its environment failures are preserved in `combined-final.xml`; no scheduler code or assertion was changed.

No second selector, ledger, retry protocol, worker/server/coordinator redesign, schema change, frontend change, real profile/provider consumption, 8767/8768 service, push, main/practical ref or deployment operation was used. Template wiring is passive through the existing time Template composition mechanism; a dedicated product UI form remains outside this candidate.
