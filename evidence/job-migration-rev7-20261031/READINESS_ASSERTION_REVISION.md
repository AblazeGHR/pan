# Rev7 readiness assertion revision — 2026-10-31

This is a tests/evidence successor to migration candidate
`423aa59c1f734621b87923f4d468686b084a7280`. The immutable tested source is
`7c0be3feaaa28258b9b8b82163ca9c974ef1b93e` on
`r6/job-migration-readiness-20261029`, in the original migration worktree.
No scripts or packages were changed by this revision. `packages/**` is also
byte-identical by Git diff to accepted product input
`cc3fc708f03f474c5a983258456b767e436b38cf`.

## Original red and exact successors

MA's combined source was `bde866a35f205ef090d7db19ddc1167b6148bffb`, Job
`job_895b9d1d9e61da9d8fbf7118`: reported 52 nodes, 14 failures, no error/skip.
The exact original log bytes are archived in
`ma-combination-original-red.log.gz` (decompressed SHA256
`4d56c91fb3cbd527f7c80136072fd308d6e791bd56c6612a4177080574edc193`).
The readable `.log` projection is subject to Git newline normalization; the
gzip archive retains the exact original bytes. All 14 FAILED node IDs were
extracted from that log, not inferred from a
later passing run. `revision-assertion-map.json` maps every original node to
its explicit successor and the retained/replaced contract assertions.

| Original group | Red nodes | Successor contract |
| --- | ---: | --- |
| inventory preserves instead of freezing literal | 5 | Five kinds each exercise default literal mapping and explicit opt-in requirements (10 nodes). Default produces no deliveryRuntime/runtimeMapping. |
| stale simple item guard | 1 | Actual pinned runtime release maps; stale ordinary release rejects before creating target directory. |
| partial bundle | 1 | Eligible process and runtime sources map; an unknown receipt remains non-executable with no target IDs, and all original bytes survive. |
| original state evidence | 4 | Opt-in missing target is eligible for backlog; pending known-no-attempt, unknown and partial remain preserved with original dispatch/receipt identity. |
| proposed input/schema | 1 | Existing Binding validates/resolves updated target/text; real mapper emits these bindings with owner, phase and query-original policy. |
| only-source CLI | 1 | Default and explicit --runtime-delivery report an eligible source; dry-run creates no bundle and activates nothing. |
| CSV optional columns | 1 | Real supported runtime requirements survive either row order; historical D3 execution prohibition remains. No fabricated gap fields. |

The unchanged 20-node runtime contract file additionally verifies the exact
`product_assign_runtime_v1`, `product_session_message_runtime_v1` and
`product_session_broadcast_runtime_v1` releases, missing-target shapes, QQ
without runtime declaration, old-release rejection and ordinary frozen graph
stability. Its owned synthetic runtime tests cover real import, missing-target
backlog, target/text CAS update, same Event acceptance once, unknown original
identity protection and cold reopen. QQ remains specifically preserved for
`runtime_binding_unavailable`; a supported runtime source is not classified as
a permanent gap. No unknown Binding keys were introduced.

No assertion was replaced by skip/xfail. Old mandatory
`runtime_binding_unavailable`/`runtimeBindingGap` assertions on every supported
source and simple=0/complex=1 CLI assertions contradicted the approved opt-in
contract; they were replaced with the corresponding positive and negative
behavior checks above. Original source/identity, unsupported-release and
historical execution safety checks remain explicit.

## Fresh results and reproducibility

Exact full commands, source binding, JUnit counts and artifact hashes are in
`revision-validation.json`.

* Fixed source `7c0be3fe`: readiness + runtime contract, **57 passed**, zero
  failures/errors/skips, **exit 0**, 43.64 seconds. Artifacts:
  `readiness-contract-final.xml`, `.log`, `.exit`. This is the original
  52-node scope plus five default/opt-in parameter cases. Every mapped
  successor was checked against the actual JUnit node names.
* The six original migration/apply/partial/readiness/authorization/review-packet
  suites: **106 passed, 1 skipped, 1 deselected, exit 0**, 57.56 seconds.
  Artifacts: `original-regression-revised.xml`, `.log`, `.exit`.
  This command finished before committing the revised readiness file; that
  file was outside its selected six files. All six selected Git blobs are
  verified identical between base `423aa59c` and tested source `7c0be3fe`.
  The existing Windows symlink privilege skip and `-k 'not all_51'` exclusion
  remain explicit; no new skip was added.
* Development evidence is retained, including `readiness-revised.xml` with
  three mistakes in the first test revision and its later successors. It is
  distinct from MA's original 14-red evidence. Earlier R3 and rev7 reds were
  neither removed nor rewritten.

Correction to the earlier rev7 delivery: the original regression selection
did not include this old R3 readiness module. The earlier passing selections
did not establish closure of its 14 stale assertions. The fresh 57-node
combined run above supplies that missing evidence.

## Boundaries and next action

This is a bounded source/test candidate for MA review and combination, not
whole-profile migration or original-manager retirement acceptance. Existing
unknown/partial/pending backlog records stay preserved with their original
identities; known-no-attempt is not upgraded to success or merged with unknown.
No real profile/account, real external provider delivery, remote CI, protected
ref advancement, push, deployment or existing service operation occurred.
Linux/macOS and actual user-data migration were not run in this revision.

MA can review the test-only source commit, combine this clean evidence
successor, and run the same two-file command on its fixed combination source.
Do not use preservation or this passing synthetic exercise as permission to
retire an original manager or to replay unresolved effects.

Source/docs whitespace checks passed. Full evidence diff whitespace checks
report the original pytest trailing blanks in red logs/XML; these raw failure
artifacts are intentionally retained rather than edited to satisfy formatting.
