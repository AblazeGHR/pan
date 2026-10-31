# Rev9 QQ migration mapping candidate — 2026-10-31

Implementation source: `b4b16d6b0701d8cd684b2c83d1f06e6eb91d4ccf`.
Product input: `dc8b1ff805956a2290b91ee65d0d48f0949470ec`, normally fast-forwarded
from clean `03ed5cbc8db9ebe318b78005ef8074da1752d2bd` in the original
`r6/job-migration-readiness-20261029` worktree. No reset, replacement tree or
product edit occurred. The input remains subject to MA's combination acceptance;
this mapper candidate does not independently accept the product/browser layer.

The task's rev9 message and actual rev25 contract-handoff supplied the QQ scope.
The local `job-migration-readiness-brief.md` and `migration-r2-brief.md` were read;
their on-disk text still ends at the earlier revisions, with no rev9 appendix.
No extra authority was inferred from that missing appendix. This implementation
stays within the explicit message's script/test/evidence limits.

## Thin mapping and unchanged paths

Only existing `runtime_fields.runtime_definition`, its declaration requirements
and inventory's explicit opt-in pass were extended. No new framework, ledger,
dispatcher, schema, runtime or activation protocol was introduced. Product paths
are unchanged relative to the exact input; `mapping.py`, default CLI/apply and
all original import/owner-fence implementations are reused.

QQ `--runtime-delivery` requires context opt-in and exact Action
`product_channel_runtime_v1` revision 1, digest
`82674431dfd366b74e7278c682a51d0ad966239ed98c683ddffe6173a703a255`,
provider `pan.typed` generation 2 / digest of `pan.typed.qq-runtime.v1`, and the
exact Action declaration for targetType/targetId/botUin/text. Its owner is
delivery-manager, phase before-provider-acceptance, fence
per-target-attempt-effect, unknownPolicy query-original. The Job retains
before-reservation and existing `delivery.<field>` input paths.

Null/omitted bot becomes explicit null runtime input, meaning original default
bot selection. Empty/invalid bot is preserved rather than silently defaulted.
The old non-opt-in literal channel path and its persisted-bot guard remain.
`probe_default_parity.py` executes exact baseline inventory/runtime helper
sources against the same owned roots as the new code: **20 full default
inventory dictionaries equal**, with source bytes unchanged and root removed.
The probe's JSON output is in `default-parity.log`; exit is recorded separately.

The existing 57-node acceptance scope is retained. The one formerly
QQ-without-release node now verifies missing/old runtime release rejection by
the mapper before output directory creation, with original source unchanged.
Inventory can describe requirements without certifying that the release exists.
That is the only changed assertion group in the old contract file; no skip or
xfail was added and no node was removed.

## Red → source-bound successor

`red.xml` / `red.log` / `red.exit`: first new QQ probe on input `dc8b1ff8`,
**6 failed, 9 passed, exit 1**. The failure nodes are retained in the machine
validation index. The failures include unavailable QQ mapping, missing/null bot
semantics, missing contract fields during negative setup and invalid-bot
classification. `development.xml` retains the subsequent two failures caused
by a reason-key typo in the new branch; the key was corrected without weakening
assertions. Subsequent development evidence is retained separately.

`validation.json` binds the final source, exact command, exits, JUnit groups,
original failed node IDs, final successors, changed paths and product diff guard.
`final.xml` and `final.log` are the complete final nine-file regression, including
the original 57-node scope, all 21 QQ nodes and six original migration suites.
The existing Windows link-creation privilege skip and original `not all_51`
deselection remain visible. No baseline full-suite claim is made.

Final result on implementation source `b4b16d6b`: **184 passed, 1 original
skip, 1 original deselected, 0 failures/errors, exit 0**, 114.39 seconds.
The JUnit groups are QQ 21 pass, prior runtime contract/readiness 20+37 pass,
and original migration suites 106 pass plus the existing Windows link privilege
skip. The authorization suite retains the original `not all_51` exclusion.
Default-parity and approved-unknown-API source probes each exited 0. Only
documentation/evidence changed after this final source-bound test run.

The dedicated QQ tests exercise exact pins/provider/declaration, old-release,
phase/fields/provider/pin negatives before writes, null/default semantics,
default literal stability, CLI explicit apply/idempotent replay/flag mismatch/
stale rejection, and original delivery states. Actual owned FinalService import
uses a real TypedExecutor and QQChannelProvider with an in-process synthetic
sender; no QQ transport or account is involved. It proves pre-admission CAS
target/text changes, one Event/Run/Attempt/effect, accepted or unknown receipt,
and cold reopen without retarget/resend. Unknown remains uncertain under the
original identity. This does not prove real QQ delivery or reconnection.

## Concrete preserved compatibility boundary

Legacy pending notes are not resumed under a falsely adopted identity. The
current importer cannot carry an old pending dispatch into the same original
Run/Step/Attempt/effect lineage. Pending, accepted, unknown and partial fixtures
stay incompatible-preserved with no target IDs and cannot enter Mapper.map.
A mixed offline bundle maps an independent ordinary process record and retains
the original QQ record as non-executable; byte backups preserve dispatchKey,
fireAt, text, error, outcome and original receipt identity exactly.

This is an existing-instance migration boundary requiring a real same-identity
import handoff before that class can migrate or its original manager can retire.
It is not a new user model conflict, permanent D6 deletion permission, or an
excuse to label all newly eligible QQ records preserved. Newly mapped unstarted
records have new canonical identity, with acceptanceAdopted=false and
sourceEffectReplayed=false. Unknown ordinal9 Action names remain preserved;
there is no automatic assign fallback. The latest approved user decision
`job-unknown-api-reject-approved-20261031`, recorded in
`.workflow/job-unification-review-20261028/UNKNOWN_API_POLICY_AMENDMENT_20261031.md`,
requires unknown `action.api` rejection. The existing negative control is enough;
no new dispatch mechanism or product change was needed. `unknown-api.json`
records the precise `action.api` field/value, existing classification/reason,
Mapper rejection and zero generated canonical objects. Known API/command
capabilities are not removed by this decision.

## Scope and next action

No packages, global fixtures, original runtime implementation, protected refs,
remote CI, push, deployment, real profile/data/account or existing service was
changed. No QQ server, port or provider process was started. Test profile roots
are owned pytest/tmp roots; the source-only parity probe removes its own root.
All earlier red artifacts, the accepted 57-node history and original evidence
remain in ancestry unchanged.

MA should inspect the source commit and final validation index, combine the
clean candidate with its accepted product combination, and replay the recorded
focused command on that exact source. Remaining unverified layers are real
provider receipt/reconnection behavior, real-data/active-owner migration,
same-identity legacy pending import, target-platform CI, deployment and human
acceptance. Passing synthetic import cannot authorize original-manager retirement.
