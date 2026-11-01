# JOB-MIGRATION-READINESS rev7 runtime mapping

Baseline product input: `cc3fc708f03f474c5a983258456b767e436b38cf`, normally
merged into this branch as combined source `f7cc9ef385603c66b73c8f1a568bbfa0468dfdd5`.
The accepted product contract was captured from source `4ba377cae65a6327219340092f68bbc4eb6265ae`
at `evidence/job-retirement-runtime-20261031/contract-handoff.json`.
This candidate is migration-script/source-only work on top of that accepted
input; it does not claim MA acceptance of full migration, real profiles,
remote CI, or deployment.

## Mapping behavior

Runtime mapping is an explicit reviewed opt-in:

```json
{"version": 1, "mode": "before-reservation"}
```

The public CLI exposes `--runtime-delivery`, and the reviewed context must carry
the same opt-in. Default callers retain the pre-rev7 literal mapping. A context
containing the opt-in without the CLI flag is rejected before publication;
runtime stale plan digests return the CLI parameter error code 2 while legacy
inventory errors retain their existing code 1.

The mapper consumes only the three immutable releases from the accepted handoff:

- `product_assign_runtime_v1` → `session.assign`;
- `product_session_message_runtime_v1` → `session.send`;
- `product_session_broadcast_runtime_v1` → `session.send-many`.

For these releases it verifies the exact Action id, system flag, provider
generation/release digest and `runtimeBeforeAcceptance` declaration. It emits
the existing `input` Binding shape (`delivery.sessionId`, `delivery.sessionIds`,
`delivery.text`), Job `deliveryRuntime` owner/phase/CAS/audit/unknown policy,
and a bundle `runtimeMapping` record. No new Binding kind or product schema is
invented. Ordinary v2/frozen mappings are unchanged. QQ target/bot/text has no
accepted runtime release and remains `runtime_binding_unavailable` with target
IDs empty and original bytes preserved.

Scheduled runtime creators/triggers use the accepted `scheduler.time.v2` Event
type. Event backlog is not reimplemented or reported missing. A missing target
is represented in the runtime input and can remain pending; it is not rejected
as an unsupported feature under the explicit runtime path. Accepted, success,
unknown and partial child identities remain fenced by the product runtime; a
later target/text edit cannot resend an accepted or unknown child. Known-no-
attempt remains distinct.

## Verification

All Python commands used `D:/project/Pan-main/.venv/Scripts/python.exe` through
`scripts/run_tests_isolated.py --`, in this owned worktree, with synthetic roots.

| Command/result | Evidence |
| --- | --- |
| `tests/test_job_migration_runtime_contract.py`: **20 passed, exit 0** | `contract-final6.xml/log`; covers three runtime releases, legacy/current scheduled/message/broadcast, stale/opt-in CLI, QQ preservation, ordinary mapping unchanged, null target backlog, accepted/unknown children, cold reopen/no resend |
| Original migration/readiness/auth/review suites: **106 passed, 1 skipped, 1 deselected, exit 0** | `original-regression-final.xml/log`; sequential run after default-path and CLI compatibility fixes |
| Default legacy mapping spot check: **5 passed, exit 0** | `default-mapping-final3.xml/log` |
| Legacy public CLI synthetic chain: **1 passed, exit 0** | `legacy-cli-final.xml/log` |
| Earlier guard regression before product input: **17 fresh reds** | preserved in prior `runtime-binding-*` evidence and `runtime-binding-red-inventory.json`; no assertion was deleted or skipped |

The one skip is the unchanged Windows link privilege boundary. The one
deselected case is the explicitly prohibited old 51/60 collection. Existing
runtime/notification/Trigger/monitoring/stable-reset product acceptance is
represented by the merged `cc3fc708` input; this candidate does not rerun that
product matrix.

## Synthetic lifecycle proof

The six parameterized runtime cases execute the existing canonical importer on
owned synthetic source/profile roots. They prove:

- missing target remains Event backlog input before reservation;
- a later same-Event target/text update selects the current target/text once;
- accepted child receipt preserves original effect identity and blocks later
  target/text resend;
- unknown child receipt remains original-key unknown and is not resent;
- cold service reopen preserves Event identity, Run/Attempt/Effect fence and
  queue counts;
- source bytes/hashes and ordinary frozen plan data remain unchanged.

No model/provider/account/real data or 8767/8768 service was used. The test
executor is owned and synthetic; results do not prove real user migration or
provider delivery.

## Scope and remaining boundaries

Only `scripts/job_final_migration/**`, the dedicated runtime contract test and
rev7 evidence changed after the accepted product input. No additional
`packages/**` product change was made by this candidate. The explicit runtime
path preserves source hashes, owner/phase/receipt metadata and original
identities; it does not retire the old manager or claim whole-profile
migration.

Still unverified: real profile/source-owner migration, actual provider receipt
semantics, remote three-platform CI, deployment, human acceptance, notification
manager runtime payload changes outside the accepted product input, and QQ
runtime mapping (no release was supplied). These are boundaries, not reasons to
silently map QQ as frozen or delete preserved source data.
