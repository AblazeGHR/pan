# Unified interaction units — 2026-11-09

Baseline: main 99890a0c32890977e31ca0523ea3262dc3828542. Candidate only; no protected ref movement, push, real profile, or DS/agentflow edits.

## Contract

New definitions use schemaVersion 3 and units[1..20], stable id/prompt/type(text/single_choice/multi_choice). Choice IDs are unit scoped. text answers must contain non-whitespace text; every answer/rejection may carry optional btw (<=10000 characters). Reject is {rejected:true,btw?}, never executes. Choice actions are immutable triggerRef compatibility or actionRef{id,revision,digest} plus fixed arguments; mutually exclusive. BTW is delivered to the source but never substituted into fixed executable arguments and never suppresses an explicitly selected action.

Submit uses existing user respond/submit routes with requestId, expectedRevision and unitAnswers keyed by unit ID. Any nonempty subset is legal. Validate the complete batch before one SQLite transaction writes immutable answers, stable submissionId, response outbox and per-choice execution intents. Revision CAS serializes competing submissions; an identical requestId/content retry returns the original batch; edits to a committed unit conflict. Card unitStatus is pending/partial/processed, unit response state pending/answered/rejected; execution delivery/receipt is separate. Native V3 answers is an alias for unitAnswers; legacy answers retains its original whole-card contract.

1..3 newly submitted units notify full prompts, choice labels, answers and BTW; 4..20 notify title/count/rejectCount/submissionId and exact interaction_card(operation="submission",card_id=...,submission_id=...) command. Neither envelope nor text includes old answers. Producer submission reads are owner scoped and return only that batch, including independently queried execution receipts.

Reuse question_events claims, lookup-only unknown/in_flight reconciliation and existing outbox queue IDs. ActionRef calls FinalService.invoke_action with a stable event-derived requestKey and the existing source Session actor/permissions; lookup reads original final_operations only. No Job is synthesized. TriggerRef continues the existing directed canonical graph adapter. Persistence precedes execution; failures remain visible and do not revert user answers.

## Compatibility matrix

| Input/history | Unified projection | Writes/execution |
|---|---|---|
| V3 new | units, per-unit states | partial or batch, immutable per submission |
| V2 unanswered | questions projected as units | new unitAnswers partial; old answers whole-card unchanged |
| V1 choices | one single_choice unit, answer projected as BTW | new unitAnswers; old choiceId/answer unchanged |
| V1 text | one text unit | new unitAnswers; old answer unchanged |
| V1 display-only | no fabricated actionable question | remains read-only |
| V1/V2 terminal | original response and execution IDs retained, projected read-only | no migration/replay/rebinding |
| Unknown existing receipt | original identity and uncertainty | lookup only |
| no_answer_needed/deleted | existing terminal/tombstone | existing restrictions |
| reminding_card | reverse user→Session flow untouched | original independent semantics |

## Validation and boundaries

Focused backend tests cover atomic failure, empty text, rejection without BTW, subset locking, request identity, concurrency, cold unknown, notification threshold and fixed references. Existing interactions tests remain regression gates. Frontend tests/build and owned HTTP production Chromium are separate evidence layers. No source/unit/build result claims live provider, remote CI, real migration, deployment or human acceptance.

## Verified implementation choices

The initial 79071ee9 limitation was corrected after inspection of the existing identity-neutral TypedExecutor provider port. Ordinary fixed Session products now use a thin canonical invoke adapter with CommandContext, existing Worker providers and operation receipts; no Job/Run/Attempt is synthesized. Explicit `job.create` and `template.instantiate` now use the original canonical creation/composition transaction when the complete fixed graph is proven; the selected Job itself is created, never a hidden wrapper. Definition-time capabilities and the exact runtime-release incompatibility matrix are in [Session direct invoke supplement](Interaction-Units-Session-Direct-20261109.md). This follow-up touches only canonical adapters/forwarders and corresponding Card paths; Action/Jobs UI and agentflow remain unchanged.

The card status wire extension is partial; existing open/responded/rejected/no_answer_needed values remain. unitStatus explicitly exposes pending/partial/processed. Legacy whole-card terminal omissions project as processed without fabricating an answer. All-reject legacy outcomes project each unit as rejected. Partial cards count toward capacity and creator pending results, but cannot be closed with no_answer_needed after any answer has been recorded.
