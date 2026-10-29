# CP-X3 revision6 independent minimal release candidate

## Exact source / boundary

Product+tests/docs commit `d210ad06f1dea5e6c4eb1588e73b69605c436eeb` is rooted directly at
main `9b4506c94ac67a39093961b218b2f01d387d1049`. Worktree/branch:
`D:/project/pan-worktrees/xplat-minimal-release-20261029`, `xplat/minimal-release-20261029`.
Original review tree/history is preserved clean at `cd8b6d682136c98e7294b864f6e2d715544d06b8`.
Its independent verdict is not to release4a86 as a platform-only upgrade preserving Jobs.

The seven product paths are exactly those authorized by revision5, applied using
`git diff <source>^ <source> -- <paths> | git apply`, not whole mixed commits/files.
Hunk sources c8c8c4eb340a9db1285596e161f6a7f54bb032bc and
cf7510f5152655992e376092d946962812950137. No source symbol dependency required Job
cutover extraction. Actual file SHA256s and protected refs are in source-guard.json.
Guard exits0: packages/jobs, scheduler, MCP, background_jobs, main_lifecycle, launcher,
cli, main.py, dependency manifest and platform registry have **empty diff** from9b4506.
server diff is only two native projections and pre-workdir native creation admission.
Host startup still invokes the original scheduler loop; no OwnerFence/FinalService binding.

MA revision6 separately authorized tests/test_terminal_secret_store.py's native ACL
declaration guard from233c18b948d6013a0ba7934c71b1e2c79ff2e85c. All existing function,
class/helper ASTs remain exactly equal; original `DPAPI / Windows ACL only` reason stays.
No module skip or Windows assertion removed. See acl-ast-guard.json.

## Local results (Windows Python3.14.5 only)

Python uses designated D:/project/Pan-main/.venv/Scripts/python.exe and the isolated
runner, no shared data/profile/8767/8768. All logs and JUnit are adjacent. Unique tests
are reported per suite; overlapping suites are not added as a misleading total.

| Layer | Command/files | Exit / result |
|---|---|---|
| Capability/native/IPC gates | test_worker_platform_admission, test_native_platform_projection, test_native_control_api, test_plugin_ipc_endpoint, test_platform_api, test_platform_layer | 0;69pass (`gates.xml`) |
| Existing Job/Scheduler/API and owned compatibility | test_minimal_platform_release, test_background_job_api, test_scheduler_store, test_scheduler_api, original two background start/external-cwd nodes | Initial84pass/1new-probe-fail of85; corrected single probe1pass, exit0 (`compatibility-corrected.xml`) |
| Original Worker/plugin regression | test_plugin_event_lifecycle, test_plugin_events, test_worker_states, branch, watchdog, output_mode, system_prompt_spawn | 0;94pass/9existing skips of103 (`worker-plugin.xml`) |
| Import/policy source checks | test_platform_import_chain, test_platform_layer, test_platform_api | 0;28pass; simulated import remains labelled, not target OS proof (`import.xml`) |
| ACL original Windows test module | test_terminal_secret_store | 0;40pass (`acl.xml`) |
| Full repository collection | tests/, packages/qq/, packages/wechat/ | 0;6406nodes, zero collection errors (`collect-final.log/xml`) |
| React focused | NewSessionModal, platformStore, viewEntries.capability, TerminalPanel.capability, AppSettingsModal | 0;112pass/1existing skip (`vitest.log`) |
| Type/build | pnpm exec tsc -b --pretty false; pnpm build | 0; fresh production build,90compressed assets (`tsc-build.log`, `build-corrected.log`) |
| Production HTTP/Chromium | test_minimal_platform_browser | 0;1pass (`browser-second.xml`); auth cookie, explicitfalse/actual/unknown gates, ordinarySession create, port released |
| Proposed CI import entry | scripts/validate_minimal_platform.py --phase import --output .../proposed-ci | 0; real server import (`proposed-ci/import-result.json`) |

Synthetic old-format Session, completed process Job and disabled Scheduler record are
created through original main APIs, cold-loaded by a separate real server process,
queried over real Scheduler HTTP, then updated/retried after stop. Original Job remains
failed when retry creates a new Job (the main contract). No canonical store/cutover DB.
Proof: preexisting-profile.json. Ordinary pipe spawns an owned Python fake CLI with
explicit UTF8, literal Unicode/space/& cwd and stdin/stdout, then original stop kills
only its own child. Proof: pipe-worker.json. No real provider/account consumption.

The compatibility probe first incorrectly expected retry to reuse JobID; fixed to
baseline's new JobID semantics without changing product/old assertions. First browser
probe's exact label lookup did not match browser label computation; replaced by the
actual form select, no UI product change. Initial build used read-only integration
dependencies missing main's remark-math; retained build.log failure, then changed only
this tree's owned junction to existing D:/project/Pan-main/packages/web/node_modules.
No install/update to shared dependencies. A later real tsc-b and full build pass are
the acceptance results; earlier noEmit is not substituted for build.

Fresh production index/entry hashes and source-tree hash are in validation-summary.json.
Browser served this worktree's freshly built dist, not a Vite harness or old4a86 bundle.
Test-only inventory is inert; policy override tests denial on Windows without claiming
that Windows is POSIX. Unknown is a failed browser platform probe; no backend API is
intercepted for ordinary Session create. browser-runtime.json confirms owned port release.

## Freeze verdict and next action

This is a **locally validated bounded candidate preserving main Jobs**, ready for MA's
same-source Windows3.12/3.14, Ubuntu/macOS import/collection/fast plus fresh authenticated
Ubuntu production browser. It is not yet a three-platform accepted release. POSIX
processOwner=false and terminal/detach/rewind/service/DPAPI unsupported remain explicit.
No full ProcessOwner, Job unification, migration or all-phase completion is claimed.

Original Windows3.14 plugin recurrence nodes are included unchanged in the locally
passing full plugin suites. Recurrence risk from CI40881289687 remains open pending
same-source remote validation/diagnosis; no wait was increased and no timeout skipped.
No existing full-suite/retired assertions were weakened or silently removed.

Exact CI commands/dependencies: CI_PROPOSAL.md; scripts/validate_minimal_platform.py.
Full exact node list: exact-nodes.txt, with baseline platform skip contracts preserved.
For source-guard in a disposable checkout, fetch baseline9b4506 as well (fetch-depth0
or exact base) so missing Git history cannot masquerade as an empty negative diff.
Fresh dependency install and all target-platform execution remain remote layers, not
inherited from4a86 CI40873872539. Main/practical/release refs, accounts, production
services/data and JobTA tree are untouched. No push/remote dispatch/deployment.
