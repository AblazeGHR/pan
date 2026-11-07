# P3 MA review follow-up: legacy coverage remains blocked

Reviewed candidate: `1ba0e8dec6f7c9410ea9aa83525acb8f251cc497`, on the original
`implement/action-unification-p3-20261106` branch. This follow-up does **not**
close “update this Action in all Groups” for a profile containing legacy Groups.
No successful old-Group adoption → upgrade end-to-end result is claimed.

## Design reconciliation and exact missing port

Approved §6 requires the same fixed ActionRef, trusted caller, original requestKey
and canonical operation receipt. §9.1 requires the complete scope, and explicitly
limits JSON Groups to preview until their unique writer moves into canonical
storage. §16/§19 require retained parameters, output and old entry points until a
named working successor exists. Together these require an actual writer handoff
and transport adapter; neither preview nor a P1 alias is migration completion.

Existing reusable ports were checked against the source:

| Existing port | Reusable component | Missing Group behavior |
| --- | --- | --- |
| `FinalService.transaction` / `OwnerFence` | Canonical owner fence, one SQLite transaction | Does not exclude the JSON registry writer or an old process using it |
| `Store.mutate` / `atomic` | One JSON replacement, revision CAS | `_lock` is process-local; no cross-store authority generation or Group handoff receipt |
| `runtime_activation.activate_bundle` | Reviewed hash/profile/plan checks; canonical transaction | Explicitly accepts Job, Trigger, Creator, Template records, not Group or Group releases |
| `SourceMigration` / `PendingPublicationAdoption` | Original-identity adopt/status/settle pattern and stopped-source proof | Scheduler/Event occurrence identity and source links; no registry Group writer or pinned MCP release semantics |
| P1 `register_action_alias` | Explicit legacy logical ID → fixed ActionRef | Does not prove equivalence of each old release snapshot, stop a writer, or adapt flat MCP/output contracts |
| P3 `Groups` / plugin lifecycle | Fixed-ref drafts/releases, original operations, coordinator/pins | No source-bound Group adoption operation or old API mutation routing |

Current `Store.put(groups)` checks canonical ownership, but legacy `build`,
`unbuild`, `delete`, generic JSON mutation and catalog projection do not implement
an adoption-generation gate. Creating a canonical row and leaving those paths
active would permit two authorities or duplicate plugin registration. Removing
the JSON row/file first would create a crash gap and change preserved source
bytes. Calling the existing scheduler adoption port with Group-shaped data would
invent false source identity. None is a safe implementation of the approved plan.

The source is therefore left intact and apply remains blocked. No new migration
saga, cross-file transaction or “offline=true means stopped” bypass was added.

## Fix in this follow-up

The original preview could omit a legacy Group when its Action IDs lacked P1
aliases. It now lists `legacy_action_identity_unresolved` and blocks the entire
unqualified all-Groups batch. A similar name or equal ID string does not prove a
mapping. Explicit aliases to other logical Actions prove an unrelated Group is
outside scope. Explicit `groupIds` is still a caller-selected bounded batch, not
an assertion that every Group in the profile was upgraded.

This fix is covered by `test_all_groups_unmapped_built_legacy_is_not_silently_excluded`:
the source is an actually built synthetic JSON Group with original snapshot and
capability files; both unresolved identity and subsequently registered alias
remain blocked, the canonical batch revision stays unchanged, and source/release
bytes are unchanged. This is **blocked-path evidence**, not a successful migration.

## P6 candidate whitelist and required handoff artifact

The following is an implementation handoff, not an enabled converter or runtime
migration claim. Only a validated ordinary legacy command Group is a candidate
for the first runnable migration whitelist:

1. Group has a valid stable ID/name, a bounded unique `action_ids` list, and no
   unknown fields or damaged links. Action definitions pass the existing validator;
   only ordinary non-system, non-high-risk `execution.kind=command` is eligible.
2. Every executable/cwd/whole-scalar argv placeholder, timeout, input schema,
   defaults, enabled state, effect, idempotency and output_format is represented
   without changing meaning. External path resources remain explicitly mutable;
   a fixed ActionRef does not assert fixed file bytes. Disabled definitions stay
   disabled. No “latest” inference at import is allowed.
3. Every referenced historical build/snapshot has explicit mapping
   `(groupId, legacyVersion, legacyActionId, snapshotDefinitionDigest)` →
   `{id,revision,digest}`. A current registry definition or logical alias alone is
   insufficient: the draft may differ from a running release. Validate each
   mapped canonical definition and provider release against that snapshot.
4. Exclude schema fields that would become P2 descriptor overrides (`argv`,
   `commands`, `cwd`, `env`, `command`, `executable`, `args`, `timeout`,
   `timeout_sec`, `hideWindow`, `externalScript`, `interpreter`) until a fixed
   argument adapter proves they retain the old substitution semantics. Do not
   feed arbitrary legacy business arguments into `prepare` as configuration.
5. Preserve Group ID, plugin ID, tool aliases, binding mode, release versions,
   old snapshot/capability files and loaded Worker pins. A new canonical release
   is an explicit publication; it does not overwrite the old version or pin.
6. Preserve the original direct-tool argument/result contract through a versioned
   compatibility adapter into the same canonical invocation. JSON output parsing,
   text content, failure shape and no-replay identity need named tests; returning
   only a new command receipt does not by itself preserve the old tool result.

API GET Actions in the existing three-route whitelist can be inventoried and
exported read-only, but are **not runnable members** of this whitelist: P2's direct
adapter currently supports process.command/process.shell, not a canonical GET
adapter. System/session/template/QQ/protected effects, complex or damaged records,
unmapped versions, secrets embedded in definitions and unknown leases/writers
remain excluded and byte-preserved with an explicit reason. Exclusion is not
retirement, conversion or successful all-Groups apply.

The source-bound handoff artifact must carry explicit profile/source roots, raw
registry SHA-256 and revision, original Group/build IDs, per-file snapshot and
capability hashes, original bindings/pins inventory, reviewed fixed-ref mapping,
authorization evidence, stopped-writer PID/birth/tree proof, canonical owner
epoch, expected authority generation, requestKey and plan digest. Use reviewed
existing source-proof and owner-fence primitives. Do not trust a caller boolean
or assume a released SQLite owner stopped every legacy writer.

P6 must choose one authoritative commit point in the canonical transaction for
the Group, imported version mappings, original adoption operation and writer
generation. All legacy reads/writes/build/unbuild/delete/catalog admissions must
obey that generation and forward compatible operations to the same Group after
handoff. Old JSON bytes can remain immutable audit evidence; they must no longer
be a second live configuration authority. Any unavoidable pre/post-commit file
steps need explicit fail-closed status/reconciliation of the original operation,
with one writer at every point; no cross-file atomicity claim is allowed.

## P6 required named acceptance gates (not implemented/passed here)

| Gate | Required observable evidence |
| --- | --- |
| `P6-GROUP-01-whitelist-export` | Explicit dry-run roots; valid command Group maps every draft/release ref; unknown/complex/disabled bytes and states retained |
| `P6-GROUP-02-owner-writer-handoff` | Real stopped-writer proof and canonical owner epoch; same Group/plugin identity; every old mutation and catalog path obeys the new authority generation; no dual writer |
| `P6-GROUP-03-cutover-crash` | Faults before/after authority commit, publication and response; query original requestKey after cold recovery; old or new unique writer, never half-write or blind replay |
| `P6-GROUP-04-release-pin-preservation` | Old snapshot bytes/schema/version/tool alias and loaded Worker pin unchanged; explicit new build changes only future pin; retained old release remains readable |
| `P6-GROUP-05-old-to-upgraded-stdio` | Build old JSON Group and call actual flat direct MCP; adopt same identity; publish Action v2; preview/apply all Groups; explicit build; actual standalone/native/gateway calls; original Worker stays v1, new pin uses v2 |
| `P6-GROUP-06-compatibility-identity` | Nonempty/default/escaped parameters, business requestKey collision, old JSON/text/error output, lost response, original operation query, invoke revocation; one effect and no hidden Job |
| `P6-GROUP-07-all-scope-atomic` | Mixture of adopted, still-legacy and unrelated mapped Groups; unqualified all scope never shrinks; CAS/retired/schema/permission/storage conflict leaves the entire selected batch unchanged |
| `P6-GROUP-08-legacy-entry-successors` | Old HTTP/MCP/GUI CRUD/build/unbuild/bind/delete and dynamic provider paths have named compatible canonical successors; only then retire proved-unused leaves |

P6-GROUP-05 is the missing successful synthetic old-Group → upgrade proof requested
by MA. It cannot currently run without inventing an unimplemented writer handoff
and compatibility port. The all-Groups acceptance item remains blocked until that
gate and its prerequisites pass; passing current P3 tests is not a substitute.

## Actual direct MCP schema verification in this follow-up

`test_legacy_direct_mcp_keeps_original_flat_schema_and_parameters` runs a real
standalone stdio subprocess and original legacy command provider. `tools/list`
advertises the original flat schema; `{value:...}` executes and returns the
original parsed JSON object. Sending the canonical envelope to that old tool is
rejected. No release file is changed to conceal the difference.

`test_direct_canonical_mcp_advertised_schema_and_request_key_namespaces` runs a
real standalone stdio subprocess through trusted IPC and P2. Its actual listed
schema equals the fixed release capability document. Required business fields
are inside `arguments`; the required outer requestKey names the operation. Both
the business schema and envelope deliberately contain a field named requestKey:
the command receives the business value, while idempotency/query use the outer
transport key. Equal retries keep operationId; changed parameters conflict; flat,
missing outer-key and missing business-field calls are rejected before effects.

Thus canonical direct mode works with its **advertised envelope**, and old direct
mode retains its **flat contract**. They are not wire-compatible shapes for an
in-place migrated release. P6 must preserve/version/adapt the original shape;
this follow-up does not declare direct schema compatibility across adoption.

Evidence: [ma-review.xml](../../evidence/action-unification-p3/ma-review.xml),
[ma-review-regression.xml](../../evidence/action-unification-p3/ma-review-regression.xml).
Review tests: **4 passed**. Combined regression: **114 passed**, including the
original P3 17, P1/P2 and old Group tests. Both runs exit 0 with no
failed/error/skipped tests. Commands, each prefixed by
`python scripts/run_tests_isolated.py --`:

```text
python -m pytest tests/test_action_unification_p3_review.py -q --tb=short --junitxml=evidence/action-unification-p3/ma-review.xml
python -m pytest tests/test_action_unification_p3.py tests/test_action_unification_p3_review.py tests/test_action_groups.py tests/test_action_unification_p1.py tests/test_action_unification_p2.py -q --tb=short --junitxml=evidence/action-unification-p3/ma-review-regression.xml
```

All commands use the isolated runner. No real profile/data, main/practical,
8767/8768, push, other worktree or deployed service was changed. The follow-up
changes Group preview logic, tests and documentation; no shared final_service,
final_api or P4 source was changed.
