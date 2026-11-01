# Job final migration inventory

Read-only inventory: no migration was executed, no target store was written, and no service or process was started.

- Root: `D:\project\pan-worktrees\job-migration-readiness-20261029\.validation\job-migration-runtime-bindings-20261031\source-f0a9b8dd\synthetic-source`
- Real profile: no (markers: none)
- `--allow-real-profile`: no
- Mapping version: 1
- Schema whitelist: v1-known-20261025
- Inventory digest: `-`
- Plan digest: `-`
- Target writes: 0

## Summary

| Total | Simple | Complex | Unknown | Scan errors |
|---:|---:|---:|---:|---:|
| 2 | 1 | 1 | 0 | 0 |

| Source type | Total | Simple | Complex | Unknown |
|---|---:|---:|---:|---:|
| MIG-BG | 1 | 1 | 0 | 0 |
| MIG-SCHED | 1 | 0 | 1 | 0 |

## Instances

### mig1_c45460a93ac8927e0e883dd8 — job_bbbbbbbbbbbbbbbbbbbbbbbb

- Type: MIG-BG (`background-process`)
- Classification: simple-script-migratable (simple)
- Owner: session / sess_creator
- Authorization evidence: creatorSessionId
- Source file: `background_jobs/jobs/job_process.json`
- SHA-256: `40ac29601f549fe6bfe181efb16a8da63f92ceb953b29f4d9ccb02de10023974`
- Schema fingerprint: `sha256:4cffd4e9e836bdbb65fd4c8198fe2e1c7ea4afd47aeb891b5c26b8f8f9144854`
- Suggested action: plan-only
- Rejection reasons: none
- Target: `{"kind": "session", "sessionId": "sess_target"}`
- Cursor: `{"lastFireAt": null, "maxRuns": null, "nextFireAt": null, "runCount": null}`
- Field map:
  - `jobId` → `provenance.originalJobId`
  - `argv` → `process.exec.arguments.argv`
  - `cwd` → `process.exec.arguments.cwd`
  - `hideWindow` → `process.exec.arguments.hideWindow`
  - `name` → `job.metadata.name`
  - `description` → `job.metadata.description`
  - `createdAt` → `provenance.createdAt`
  - `updatedAt` → `provenance.updatedAt`
  - `exitCode` → `historicalEvidence.exitCode`
  - `logPath` → `historicalEvidence.logPath`
  - `terminalEventId` → `historicalEvidence.terminalEventId`
- Expected semantic diff:
  - old exit/log/resource receipts are historical evidence only
  - no new Run is fabricated from the old runCount/status

### mig1_bcfc53327ffbc10bebe35cd8 — sch_fixture0000000001

- Type: MIG-SCHED (`scheduled-task`)
- Classification: incompatible-preserved (complex)
- Owner: system / system
- Authorization evidence: system_owned
- Source file: `background_jobs/jobs/job_runtime.json`
- SHA-256: `a5c0cafa9ab966365b8bcc68ac34122be8c634b2d4a62eb280324cd8b2ab95a7`
- Schema fingerprint: `sha256:ecfec4a8045091326709a25fc1628853d278945b777b7c2fe2fa8ab36cca776b`
- Suggested action: retain-source-manager-await-runtime-contract
- Rejection reasons:
  - runtime_binding_unavailable: input binding exists; manager revision/reservation/receipt and Action declaration are not wired by this mapper; retain source target/text and manager lifecycle, do not freeze as literal
- Target: `{"kind": "session", "sessionId": "sess_target"}`
- Cursor: `{"lastFireAt": null, "maxRuns": null, "nextFireAt": "2030-01-01T00:00:00", "runCount": 0}`
- Runtime owner: Creator/Delivery Manager; stage: event-published-before-queue-or-external-acceptance
- Runtime mapping: preserved; approved manager implementation required, no new user decision.
  - `targetSessionId` → proposed existing input binding `delivery.sessionId`; no executable binding generated
  - `text` → proposed existing input binding `delivery.text`; no executable binding generated
- Expected semantic diff:
  - none; original record is preserved byte-for-byte

## Plan (not executed)

- Executable: no
- Target ID allocation: deferred-until-apply
- Planned simple items: 1
- Preserved items: 1

### Planned simple items

| Migration ID | Original ID | Type | Source file | SHA-256 |
|---|---|---|---|---|
| mig1_c45460a93ac8927e0e883dd8 | job_bbbbbbbbbbbbbbbbbbbbbbbb | MIG-BG | `background_jobs/jobs/job_process.json` | `40ac29601f549fe6bfe181efb16a8da63f92ceb953b29f4d9ccb02de10023974` |

### Preserved items

| Migration ID | Original ID | Type | Reason codes |
|---|---|---|---|
| mig1_bcfc53327ffbc10bebe35cd8 | sch_fixture0000000001 | MIG-SCHED | runtime_binding_unavailable |

## Scan errors

None.
