# P6 follow-up on 267287c

The original candidate is retained. Work continues on the same branch and with
the same owner/fence/receipt and preservation boundaries.

## Slice 1: adopted flat MCP in all three projections

`test_P6_GROUP_05_old_to_upgraded_stdio` now executes the complete original old
JSON Group → adoption → specified Action v2 → explicit publication → old/future
pin chain in standalone, native and gateway projections. Each uses an actual
stdio subprocess and trusted IPC. The original flat schema, default/escaped/
UTF-8 business data, original release files, business requestKey, independent
transport key, changed-payload conflict, unknown operation identity, no replay,
revocation and absence of hidden Jobs are asserted in each projection.

Result: **3 passed**; evidence `evidence/action-unification-p6/flat-three-modes.xml`.
Command: `python scripts/run_tests_isolated.py -- python -m pytest tests/test_action_unification_p6.py -k old_to_upgraded_stdio -q --tb=short --junitxml=evidence/action-unification-p6/flat-three-modes.xml`.

This closes the previous adopted-flat native/gateway stdio evidence gap. It does
not change arbitrary existing-writer eligibility or claim real model Worker,
real-profile, browser, deployment or target-platform acceptance.

## Slice 2: ordinary command CRUD, lifecycle and formerly unmapped additions

The adopted source now has canonical compatibility bindings in the existing
`final_documents` store. They are projection/provenance metadata; the only
executable definitions are the existing versioned Actions and fixed Group refs.
No JSON dual write, second executor, receipt store or notification producer is
introduced. Current draft mappings must identify the actual canonical head;
historical releases continue to map their exact old snapshots independently.

Unreferenced ordinary Actions can be reviewed explicitly with
`{groupId:null, version:"draft", actionId, definitionDigest, actionRef}`. Missing
mappings still block the source-wide handoff. This makes a later old Group
`action_ids` addition resolve a committed binding, rather than guessing identity
from an alias/name or importing an arbitrary source while a writer is unknown.

The old HTTP Action create/update/delete/lifecycle and Action/Group draft routes
now forward through the same FinalService owner-fenced transaction and operations.
The old item shape, generated/stable legacy IDs, global registry revision CAS,
disabled draft behavior and output_format remain. Optional HTTP
`X-Pan-Request-Key` and MCP `action_drafts.request_key` expose the original outer
receipt identity. Existing calls without these optional fields continue to work.
New Groups are recorded as canonical creations, never as fake old-source adoption.

Internal private arguments to the existing draft/control primitives preserve
the original permission contract: enabled config edits need updateConfig;
pure enable/disable needs lifecycle; disabled draft creation needs create;
delete needs delete. These are not new wire fields or grants. Combined config/
lifecycle changes still need both original grants. Draft-only tools cannot
enable, build or invoke. The exact draft POST endpoints are restored to trusted
IPC; other legacy writes remain outside that bridge. The original two retirement
assertions and their failures are retained in
`draft-transport-original-red.xml`; replacement coverage checks exact methods,
default denial, actual stdio grants/schema, forced disabled state and receipts.

Legacy definition updates create new versions. Legacy disable/delete does not
rewrite an already built enabled snapshot. Delete records a non-executable
retirement provenance marker tied to its original operation, alias, source and
logical Action; it does not destroy historical releases. An immutable bound
legacy release may retain that historical snapshot, while an explicit revocation
of that exact version and an invoke-grant revocation still reject new effects.
Deleting one reviewed alias cannot retire another alias to the same Action.
Canonical edits advance every affected legacy projection CAS in the same writer
transaction; legacy edits do not silently overwrite a concurrent canonical edit.

The shared P2 command primitive has a named `argumentBinding=legacy-flat/v1`
adapter where required. Business cwd/commands/env/etc are only old whole-argv
values, never descriptor overrides. A real effect test proves that a business
cwd is not used as cwd and an untrusted business command list is not executed.
Simple fixed argv and a single-command descriptor can be projected without
changing the executable/argv/timeout semantics.

## Named notification, cancellation and history mapping

| Original behavior / identity | Successor / evidence |
| --- | --- |
| Direct Group command has MCP result, no automatic Job terminal notice | Same canonical invocation and MCP projection; real stdio draft/invoke cases assert no hidden Job and empty Job outbox |
| Client request timeout is not proof of command cancellation | `P6-COMPAT-02`: real standalone stdio timeout; original effect finishes once; original operation query/repeated key returns the same identity |
| Executor/host cancellation owns resource cleanup | `P6-COMPAT-01`: cancel the Group invocation task through the existing P2 executor; confirmed tree cleanup, cancelled receipt, same-key no replay and cold query |
| Group build asks enabled Sessions for delayed restart | `P6-COMPAT-03`: existing coordinator/restart path; response replay creates neither a new release nor another restart request |
| Old build history and pinned release | Original version, files, schemas, aliases and refs stay immutable; all three real stdio projections retain old pin and switch only the future pin |
| New direct receipt provenance | Fixed `source.groupRelease` is persisted in the same original operation before effect; cold cancellation/history query retains it |
| Historical receipts lacking source fields | Left unchanged; no reconstruction from today's release |
| Legacy Job/Scheduler/channel notification/query/cancel IDs | Existing hosts and read/cancel routes retained; this slice does not migrate their records or assert a new full-family acceptance |

P4 also accepts structured Group provenance that is not a bound release token.
An initial combination caught an invalid assumption that all such provenance
was a string lookup key. `followup-combination-red.xml` preserves that regression.
The corrected admission guard only recognizes an actual stored immutable release
and its exact ActionRef for snapshot retention; provenance never grants Job
creation or invocation permission. The original P4 negative test remains intact.

## Explicit remaining scope and real model conflicts

- Unknown historical writers/leases, damaged links, unknown fields and protected
  source records still preserve/block. Fresh-source enrollment cannot attest an
  existing directory. No actual profile was inspected or granted eligibility.
- General **non-command** legacy Action conversion is not delivered by this
  slice. API GET, system/session-operation, template and channel definitions
  retain their original source/hosts and remain outside this command handoff.
  Their canonical adapters and per-family old entry acceptance remain work;
  this report does not rename that missing coverage as completion.
- A genuine representation conflict is now named and tested: multiple command
  outputs have no old single JSON/text merge contract. Nonempty environment,
  visible-window policy, shell/external-script descriptors and unsupported old
  timeout/schema contracts also cannot be silently dropped from a legacy
  single-command projection. Affected canonical definitions remain intact,
  `preservedActions` explains the reason, and flat Group preview/apply/publication
  blocks before effects. `P6-COMPAT-04` proves an ordinary argv projection works
  and a multi-command upgrade leaves the entire Group/release unchanged.
- A 267287c source can backfill compatibility metadata only from its original
  source hash and committed fixed mappings. If the mapped canonical revision or
  lifecycle values no longer match before that index exists, they are not
  overwritten: reconciliation from original
  receipts/mapping remains a separate explicit step. No current-head guess is
  accepted as the original source identity.
- Production GUI/browser, real model Workers, real-data migration, broad legacy
  Job/Scheduler/channel acceptance and Linux/macOS/remote CI remain unverified.

All tests use the isolated runner. Final commands/counts, source hashes and
unchanged protected refs are recorded in `followup-validation.json`. No React
source changed; there is no required frontend build for these backend slices.
Normal hooks are required on each candidate commit. No push, main/practical
advance, real data/profile operation, service restart or 8767/8768 use is authorized
or performed.

Final frozen-source verification: **188 passed** in the Action/Group/P1–P4/P34/
legacy regression combination, plus **42 passed** in plugin/configuration/
authorization. There are no failed/error/skipped cases in either final run.
The 11 new successor cases and the three adopted-flat stdio modes are included.
Source hashes matched after both runs; server import and diff check passed.
