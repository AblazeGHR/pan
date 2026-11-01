# Session time-based message successor evidence

Original source/full red JUnits are retained in ../full-terminal-5a7. FinalModel E02 explicitly maps Session-message/broadcast creation to message/fanout Action Job composition; D4 maps time to Creator/Trigger and keeps Event identities. No legacy writer/runner was restored.

session-time-v2.xml:14 original nodes,13pass1fail0skip. The unmodified original default-name node remains failed at retired start_message; automatic-name capability is not marked resolved. Initial fixture red preserved, including duplicate enable requestKey reused across different object kinds; successors now use distinct original request keys and original canonical receipts.

| Original capability | Current evidence |
|---|---|
| once delay/absolute time/source/text/description | Public Job+Creator+Trigger retains exact computed absolute time and creator record, real Session durable queue exact source/body, one Run/Attempt/effect, caller queue empty, repeated tick no replay |
| schedule edit | Public Creator CAS same identity/revision+1 replaces once by interval, old due time no Run, new due creates one; Job description CAS persisted |
| four invalid schedule forms | Public Creator rejects400/422 before any Creator or Job write; exact original parameter labels retained |
| interval/restart/cancel future | Two original Events and two Run/Attempt/effects across cold FinalService reopen, exactly two durable queue rows per target, repeated tick no burst; explicit Creator disable stops future Events without deleting original history |
| weekly | Public Creator persists exact weekday, wall time, explicit UTC zone/DST policy, next-fire cursor |
| stale running | Existing strong real HTTP lost Worker ACK successor: original unknown is queried and reconciled from durable queue after cold reopen, only one invocation. Old stale flag alone is not evidence permitting resend |
| message not process retry | Completed original Session effect cannot Retry;409 and entire aggregate unchanged, queue remains one |
| broadcast dedup/partialfailure | Three unique targets in original order, one rejected target doesn't suppress later accepted target; real accepted queues, exact caller source, partial receipt, no repeat on next scan |
| broadcast edit/cancel | Public Job CAS target edit persists duplicate input; effective provider dedup covered above. Public Creator replacement then disable prevents every future target send |

Specific unresolved name conflict: test_message_job_default_name_min_vacant requires omitted name => smallest vacant job-N, explicit trimmed name, absent description empty. Canonical final_contracts.validate_write and final_api._write require explicit valid definition.name; final_service.write validates before its transaction; CanonicalJobsView.tsx supplies editor.name and marks input required. background_jobs.default_job_name scans the old registry and cannot safely be attached to the canonical store as-is. Restoring old start_message is prohibited. Minimal options are a canonical creation convenience/auto-name operation before strict validation with owner-fenced canonical name scan, or a canonical client-side prefilled suggestion using complete canonical inventory (API keeps explicit-name rejection). This requires a concrete model/API decision; no skip or generic unsupported has been substituted, and unrelated work continues.

The original scheduler_dependency_cutover startup/reopen node now also passes unchanged with audit denying external network/process, legacy host imports blocked, one Event/zero pins/ownerEpoch2. This is separate from migration or full-profile acceptance.
