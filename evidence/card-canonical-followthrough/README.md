# Canonical fixed-target Card candidate

Baseline: `ea0b71a888c9180401b2214ee517eca683ed98ac`. Branch: `r6/job-source-receipt-20261029`.

## Source mapping

- `packages/interactions/canonical_read.py`: private read of the actual enabled Trigger, Job configRevision/definition, immutable known product Session Actions, literal target scope and current authority. Dynamic targets, custom Actions and asynchronous Trigger conditions fail closed.
- `trusted.py`: additive canonical binding create/reverify; original Session scope restrictions remain authoritative. Existing v1 Cards remain readable and cannot authorize a canonical graph.
- `schema.json`: disjoint v1/v2 private binding. Original v1 object unchanged; v2 contains server-derived graph proofs and bounded references. No global contract change.
- `runtime.py`: existing directed `admit_event`, original event-operation/admission lookup. Final proof/scope check and admission use existing Session store lock, host joined transaction and authority lock, without an intervening await. No Event Bus broadcast, new table, ledger or retry protocol.
- `lockerModel.ts`, `TrustedChoice.tsx`, `CardQuestions.tsx`: necessary union consumers; v1 omitted-version fixtures retain v1 meaning. Browser displays server facts and does not certify proofs.
- `tests/support/interaction_card_http_server.py`: native canonical create/enable and production directed adapter, retaining original HTTP/browser assertions and recording transport. Only test Trigger ID allocation is deterministic.

`accepted` denotes persisted admission, not execution completion. Missing original admission remains unknown; recovery does not resubmit. Definition changes after a persisted admission do not invalidate lookup of its original fact/Trigger identity.

## Validation

Backend commands use `D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest ...`.

| Evidence | Result | Scope |
| --- | --- | --- |
| `canonical-final-3.xml` / `.log` | 21 passed | Final canonical suite: send/assign/broadcast through real paused Session queue; current authority/scope; graph change and concurrent admission barriers; unknown/cold lookup; v1 rejection for canonical admission; independent v2 questions, large HTTP response, stale proof |
| `native-http-browser-final.xml` | 22 passed | Then-current 20 canonical cases plus both original `test_interaction_card_http.py` nodes, using fresh production bundle, owned authenticated HTTP/MCP/stdio/browser fixtures |
| `combined-final.xml` | 84 passed, 3 failed | Broader affected interaction/question/no-answer/API/runtime/legacy-binding plus original HTTP tests; original legacy failures retained below |
| `frontend-final-2.xml` | 43 passed | `pnpm exec vitest run src/views/TrustedChoice.test.tsx src/views/LockerView.test.tsx` |
| Normal `pnpm build` | passed | TypeScript, Vite production build and precompression, completed before final HTTP/browser run; build output observed in session, not a saved build log |
| `baseline-preservation.json` | passed | Original HTTP/question/legacy helper tests and retired service setup unchanged from baseline; original private v1 schema object unchanged |
| `browser/` | retained | Desktop/mobile screenshots, original browser assertions (zero page errors), sanitized cold restart summary, both isolated service/port cleanup proofs |

The three unchanged legacy setup failures are:

- `test_interaction_questions.py::test_trusted_question_scopes_and_independent_unknown_reconciliation`
- `test_interaction_questions.py::test_large_v2_runtime_admission_and_scope_recheck`
- `test_interaction_questions.py::test_question_event_survives_deletion_without_resend`

All fail in unchanged `test_locker_trusted_binding.setup` -> `Service.start`: `DependencyUnavailable('Canonical Job owner required; legacy Automation host is retired')`, before binding resolution. The new canonical suite supplies corresponding independent-question reconciliation, large HTTP/scope/stale proof and detached deletion/recovery successors. Original tests and red assertions were not edited. Baseline source equality was checked; this is not a claim that a separate complete baseline test run was performed.

Earlier raw red iterations are retained (`first`, `security-first`, `security-final`, `focused-final`). `canonical-final-2.xml` records an interrupted pytest run caused by terminal `OSError: [Errno 22] Invalid argument`; final-3 redirected output to a file and completed normally. Superseded green intermediate reports remain separately named. These reports are not additive test counts.

Source diff whitespace checks pass. Raw pytest failure XML retains pytest's whitespace-only `E` lines, which are intentionally preserved as original evidence.

## Boundaries

Windows isolated source/tests/build/HTTP/browser candidate evidence only. Paused Session queue persistence is proved; external supplier consumption is not. No Linux/macOS or remote CI run, deployment, real-account/provider consumption, quota repair, shared host/server/worker changes or real data migration is claimed. No protected branch, push, existing service or port 8767/8768 operation.
