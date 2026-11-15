# Interaction Units V3 stage candidate validation — 2026-11-09

This report preserves stage 79071ee9 evidence. Its direct-Session limitation is superseded by [the Session direct invoke follow-up](Interaction-Units-Session-Direct-20261109.md); the original 107/42/browser results are not recounted as new validation.

Base: `99890a0c32890977e31ca0523ea3262dc3828542` (main). Branch: `feature/interaction-units-20261109`.
Worktree: `D:/project/pan-worktrees/interaction-units-20261109`.
Candidate identity is the commit containing this report; final delivery reports its full SHA. No merge or MA acceptance is asserted.

## Delivered behavior

V3 units (1..20) share a single persisted format. Single/batch subset submission validates every submitted unit before a FULL SQLite transaction stores immutable answers, stable submission identity, existing outbox intent and selected-choice execution intent. Text must be nonblank; rejection requires no BTW. Choice/reject BTW goes to the source and never changes fixed executable arguments. Submitted units lock; pending units remain editable. Card pending/partial/processed and per-unit answer/reject are independent from execution delivery receipts. A failed execution authorization does not block other units.

New `unit_submissions` stores original batches (indexed by card); existing cards/question_events/outboxes/canonical operation receipts remain the authority. No Action/Jobs engine, scheduler, hidden Job, Action page, Jobs page or agentflow modification. Original V1 response.answer/choiceId consumers remain supported, old terminal omissions are preserved, and no legacy projection creates execution intents.

Same MCP `interaction_card` gains `operation="submission"`, `submission_id`. Same submit/respond HTTP accepts `unitAnswers` subsets; V3 also accepts `answers` alias. Producer read: `GET /api/interaction-cards/{id}/submissions/{submissionId}?ownerSessionId=...`, with existing owner/stdio self/managed scope. Batches contain only their own units/answers/execution IDs. Notifications for 1..3 units include full text, labels, answers and BTW; 4..20 include summary/title/count/rejectCount/submissionId/read command, without answers in either text or envelope. Unknown outbox claims query the original queue ID only.

Fixed ActionRef carries id/revision/digest and literal arguments, plus frozen source-agent canonical grants. It uses FinalService.invoke_action and original final_operations receipt, with exact actor/request digest/ref/source identity verification after lost receipts. Ordinary Session products and explicit `job.create`/`template.instantiate` use thin adapters over their original canonical providers/creation transactions; explicit creation creates the selected Job itself and never a hidden wrapper. Existing TriggerRef uses the canonical fixed Trigger→Job graph. Rejection creates neither type of intent. Accepted delivery is admission evidence, not an assertion of successful effects.

## Validation

- Backend: 107 tests, zero failures/errors/skips across test_interaction_units, test_interaction_cards, test_interaction_questions, test_interaction_canonical_binding, test_interaction_card_api, test_interaction_runtime, test_interaction_no_answer. Command: `python scripts/run_tests_isolated.py -- python -m pytest <the seven files above> -q --tb=short --junitxml=.tmp/interaction-backend-delivery.xml`.
- New unit coverage includes whole-batch rollback, empty text, independent locks, idempotency, request conflicts, concurrent CAS, notification thresholds 1/3/4/20, unknown execution lookup-only, lost notification lookup-only, actual command plus lost canonical receipt and cold reconciliation, no hidden Job, fixed Trigger effects once, independent failed/successful unit execution, old V1 identity and producer receipt retention after user deletion.
- Frontend: `pnpm --dir packages/web exec vitest run src/views/InteractionUnits.test.tsx src/views/LockerView.test.tsx --reporter=dot`: 42 passed. `pnpm --dir packages/web build`: exit 0; TypeScript and Vite production build, 90 compressed assets verified. Existing chunk-size/dynamic-import warnings remain.
- Real HTTP/stdio/production Chromium: `python scripts/run_tests_isolated.py -- python -m pytest tests/test_interaction_units_http.py -q --tb=short --basetemp=.tmp/units-http-frozen --junitxml=.tmp/units-http-frozen.xml`: owned ephemeral port/profile, real production assets without route interception, 1360px and 390px. Covers single/batch/reject without BTW, partial locking, V1/V2 interaction, multi-choice, real canonical Action command, directed Trigger admission, four-unit batch, submission-only MCP read, concurrent HTTP CAS and duplicate identity, cold server restart and no command replay. Fixture cleanup verifies owned process exit and listener release.
- Full backend regressions and production HTTP tests use owned profiles, fake CLI and ephemeral ports. No real provider/account execution or real-profile migration.

Raw JUnit, owned server logs, screenshots and per-run cleanup JSON are retained locally under `_e2e_tmp/interaction-units-20261109/` after delivery; credentials in disposable test profiles are excluded from Git. The committed report retains commands and statistics only.

## Preserved red evidence and corrections

Initial regression found two compatibility failures: an extra pre-read moved the old response writer barrier; globally treating legacy rejected delivery as terminal removed its existing explicit-retry behavior. Corrected by preserving the old transaction path and applying the new independent terminal behavior to unit submissions. A new V1 test found empty `answerInput:{}` incorrectly classified as display-only; it now checks field presence. Initial new UI test lacked the jsdom annotation and had a TypeScript optional-call assertion; both corrected. First HTTP attempt used an absent production build and timed out at login; retained separately, followed by successful production builds and fresh-browser runs. No assertion was weakened or failure skipped.

## Explicit limits and remaining acceptance

Direct ActionRef capability covers canonical process/process.command/process.shell/api.get, the five ordinary fixed Session products, and explicit `job.create`/`template.instantiate` when the complete fixed graph and current grants are provable. Runtime releases with `runtimeBeforeAcceptance`, legacy scheduler lifecycle and unprovable direct targets fail closed with precise reasons. This candidate reuses the existing Action identity, FinalService receipt and canonical Job/Template adapters; it does not add a second engine or wrapper Job.

Partial cards remain in capacity/creator-pending queries but no_answer_needed cannot erase a card after any recorded answer. UI drafts remain local; unknown submissions retain their exact request identity. No remote CI, Linux/macOS runs, full repository suite, real-provider execution, real migration, deployment or human acceptance is claimed. Main/practical and DS trees are outside this candidate; no push.
