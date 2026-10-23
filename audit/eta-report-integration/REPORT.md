# Accepted ETA/Job/Locker + terminal-report integration candidate

TA combination preparation for independent MA review, 2026-10-23. This candidate is **not** a declaration that main can now be merged/deployed.

## Accepted inputs and topology

The user/MA has already independently accepted Locker `62c972415e1d44923bcea70dc0b29296c7b3958f` (source `55dfb200`, MA18 barriers +1 real HTTP/Chromium) and terminal-report `693287b1afcdf4ecb4d11db5de985210f13bd2dd` (MA39 pass/2 existing skips +1 HTTP). Those acceptances remain accepted; their historical TA report wording is not a reversal of MA acceptance. This batch checks coexistence, not reopens their completed scope.

- Fresh registered worktree: `D:/project/pan-worktrees/eta-job-report-integration-20261023`, branch `integration/eta-job-report-20261023`, initially clean at Locker candidate.
- At start, main/practical were both `140455976b7c60097fafdd4dc3f8e3b18a699ccc`; the two accepted histories shared that merge base.
- Merge `f9b1d1810be3705066e872d9e4041bb0c0df259b` has parents Locker `62c97241` and original report `693287b1`. Original full report history is retained, not cherry-picked into this tree.
- During this task MA advanced main to **`ca464c8d55e9c857aa1fa85652e3dcb3e1f7025e`**, practical to `2652ac8e6d0ff312a255666bc5e459890346e6e5`. Both have tree `593ee5d9d4a354fbc52b38553de864cf97cf273b`, byte-identical to original report `693287b1`; they are equivalent report cherry-picks, not new divergent product behavior.
- Merge `a87fbb31853f49c07cb3e2d72bd1e9fea3b5edfa` retains current main as second parent. No product tree difference from the preceding integration merge; this preserves MA's actual main evolution and report equivalence without reverting/reapplying its effects. Main, original report, and Locker are all ancestors (exit0).
- Exact frozen product/test/browser source used below: **`a7b3c47c76be408e31661193cf432cad11a46145`**. Final evidence commit only changes this audit directory (including verifier diff scope), not packages/tests/docs or browser source. No product fix was required beyond the merge.

`topology.json` records exact parents, trees and refs. Main/practical were only read; no ref move, push/release, original-tree cleanup, existing service restart, real provider/QQ/account/profile migration, OAuth or private exporter work was performed by this task.

## Conflict decisions and semantic audit

| Intersection | Decision / evidence |
| --- | --- |
| HTTP skill document | Both merges conflicted only in the insertion after YAML front matter. Kept Locker credential/recipient contract **and** report native diagnostic contract. No capability or authentication relaxation. Second merge keeps already combined text. |
| Worker terminal + RuntimeIssue | Auto-merge reviewed semantically: `_persist_terminal_state_locked` freezes reportDiagnostic before awaits, persists it on that terminal, and independently calls existing `_record_terminal_error_sample`; ETA map_runtime_issue/runtime epoch/correlation and observer remain in the latter path. Duplicate terminal guards remain before observation, so no duplicate report or RuntimeIssue. New Codex/CBC tests prove one each and preserve different adapter classification. |
| session_error_log | Shared cli_error_fields, sanitize and provenance retained from report fix; ETA `runtime_issue` parameter, runtimeIssue JSONL field and post-writer observer retained. Report is a bounded native allowlist; it does not dump RuntimeIssue/raw or let assistant result overwrite status/error. Parser classification remains independent of MA policy. |
| Trusted Locker | Existing creation/approval/admission/attempt/effect binding checks retained. No conversion to generic Bus broadcast or unbound dynamic Action. Approved card executes only original explicit Session operation with current revision/digest/source scope checks. Accepted dynamic Job Action reference contract remains separate. |
| Reports / notifications / Queue | Batch, Force and Steer native diagnostic fields and old delivery keys preserved. Queue/report read metadata does not disclose pending body; new original Queue IDs survive cold restart and are consumed once after Pause release. Locker response JSON contains actual approved answer/choice and bounded summary per accepted contract; body/attachments are not sent. Agent Inbox shared consume remains distinct from user read/unread. |
| Unknown / cleanup / authority | No new retry/replay/reset/controller. Existing unknown receipt lookup-only barriers, disable/edit failures, Job snapshot terminal cleanup, template reference and managed/Pause/Illegal/lease/generation guards are exercised by selected original tests. No attempt to infer missing historical identity or approve old unbound choices. |

No accepted implementation was reverted to satisfy an older test. No new product design or Job waiting/continue protocol was introduced. New source files are two integration tests and a local Vite verification config; existing product files are the accepted merges.

## Original MA audit evidence preserved

Original Locker tree contained six tracked audit changes; original report tree contained two tracked audit changes from MA verification. We did **not** reset, clean, overwrite, commit or rerun tests in those trees. `preserved-inputs.json` records original HEAD/status and SHA256/size for every changed audit file; `ma-input-audit-snapshot.zip` preserves a byte copy under separate source labels. Final verifier compares original status and every hash and records `originalSourceAuditsUnchanged=true`. Main's uncommitted MA skill was not edited; its hash is recorded in topology.json. These snapshots do not replace the originals or claim their dirty state is a defect.

## Verification commands / exit codes

From integration root: `E:/software/miniforge/python.exe audit/eta-report-integration/verify.py`. Exact expanded argv, cwd, exits and frozen SHA are in `commands.json`; Python runs through run_tests_isolated.py. No dependency install/upgrade: existing main node_modules junction is reused, Vite cache redirected into this worktree and Vitest cache disabled. No global UTF8 setting; new text I/O/decode explicitly UTF-8.

| Check | Final result | Evidence |
| --- | --- | --- |
| Isolated pytest: eta_report_coexistence, terminal_report_diagnostic, runtime_issues, session_error_log_worker, report_error_context, report_reads, force_steer, locker_independent_review, locker_trusted_binding, job_inbox_migration_drill, job_inbox_boundaries, job_action_templates, job_system_actions, job_completion_recovery, agent_inbox, automation_retry_parallel | **exit0, 286 passed /2 existing skips** | python.log/xml |
| `pnpm exec vitest run --config e2e/integration.vite.config.mts --no-cache` with LockerView, lockerModel.auth, UnifiedJobForm, JobTemplateEditor selectors | **exit0, 25 passed** | ui.log/xml |
| `pnpm exec tsc -b` | **exit0** | typecheck.log |
| `pnpm exec vite build --config e2e/integration.vite.config.mts` | **exit0** | build.log |
| `node e2e/verify-precompressed-assets.mjs` | **exit0, 92 assets** | assets.log |
| Isolated pytest `tests/test_eta_report_integration_http.py -o addopts= -q --junitxml=.../http.xml` | **exit0, 1 passed** | http.log/xml, http/ |
| compileall of merged Worker/log and new integration tests | **exit0** | compile.log |
| Full `git diff ca464c8d HEAD --check` | **exit2**, inherited evidence whitespace only, not a functional pass | inherited-evidence-whitespace.log, whitespace-paths.json |
| Same check scoped to `packages docs tests audit/eta-report-integration/verify.py` | **exit0** | source-diff.log |

The initial driver therefore exited1 solely for its final full-diff check. The 49 reported paths are all inherited audit/artifacts/evidence files containing raw failure output; they are deliberately not rewritten. Original full commands and exit2 remain in commands-before-diff-scope.json. The committed repeatable verifier now scopes the whitespace gate to implementation/docs/tests/tool source; this is not suppression of a product failure. All functional verification was already green and was not rerun merely to change a report label.

Two skips are the existing retired Oneshot cases; no new skip/xfail. Warnings: existing Pydantic forward-reference and pytest record_property/xunit2 notice; build's existing dynamic-import/chunk-size warnings retained. No whole-repository run or repetition of all prior accepted suites. Initial integration fixture errors used `id` instead of existing `issueId`, and a draft Vite wrapper resolved the wrong module boundary; both were corrected in test/tool code before frozen verification, without product changes.

## Cross-line real product chain

Same owned instance/profile, random port **50370**, empty QQ gateway manifest and deterministic local session_error_fake_cli only:

1. Real production Locker login with the profile's existing key/Bearer; server card binding for an explicit pause_everything Action. Chromium153.0.8010.12 inspects real operation/target/digest and submits answer + selected choice. Production view checked at1280/390/320px, no pageerror or horizontal overflow;320px screenshot visually inspected, retains accepted narrow two-column layout.
2. Directed Event creates exactly one successful Run, existing Pause business state confirmed on target. One source Session response notice is durably queued, full Card body absent from forwarded text.
3. Real stdio MCP report_subscribe + agent_assign to another owned Session produces native capacity-shaped terminal error with assistant result. Its error report and ETA runtimeIssue coexist. The fixture worker is **CBC**, so native facts stay visible but classification correctly remains unknown; the independent actual Codex adapter test maps the identical structured signal to codex_capacity. No account policy effect is invoked.
4. Manager Pause holds both original Queue IDs. No report body reaches its CLI before release. Stop owned host, move its own JSONL aside, cold-start same temporary profile: no JSONL lookup is available; stored reportDiagnostic and trusted Locker Event/Run survive. Neither Card nor report generates new IDs or repeats effects.
5. Unpause manager: its actual fakeCLI stdin receives one Locker response notice and error report (batching permitted). History and queue_delivery_ledger contain both original IDs, pending empty; actual text includes separate errorText/code/source and assistant result. Exactly one Pause Run remains.

`http/evidence.json`, `http/delivered.txt`, `http/browser.json` and three screenshots preserve the result. Retained PID/birth cleanup across both owned service generations: `ownedProcessesRemaining=[]`, `portReleased=true`, independently rechecked after validation. No user Worker or protected port used.

## Temporary-copy migration and remaining boundary matrix

| Item | State / next boundary |
| --- | --- |
| Accepted Locker and report features | Remain accepted; this candidate proves coexistence and retains full histories/current main. Independent integration review still required. |
| Legacy Job kind / Action / Template / Queue | Temporary source bytes and unknown-field projections retained; old kind projects disabled/no recovery, template revision copy fixed; runtime snapshot hidden from public projection. Selected snapshot full-terminal cleanup/unknown/no-replay and policy tests pass. No production data conversion. |
| Agent Inbox / old QQ | Temporary JSON source + physical-copy import retains unknown envelopes and source digest; shared consumption and permission tests pass. No real QQ transport or gateway. |
| Old User Locker | Accepted trusted-binding temp-copy test upgrades bindings={} without inventing approval; old row/source bytes retained, unknown recipient not reassigned. No user's profile opened for migration. |
| Product blockers reproduced in this combination | None. The full-diff whitespace result above is a preserved historical-evidence formatting boundary, not silently called pass. |
| Main merge eligibility / release / human acceptance | Not decided here. Candidate is clean and reviewable; MA must review broad ETA/Job ancestry and integration evidence before authorizing main/practical change. |
| Real providers, quota effects, account restart/wake, OAuth/exporter, live QQ, existing services | Not exercised or connected. No expansion beyond task scope. |
| Future upstream main advance | Current main ca464c8d is retained; recheck at integration time if it advances again. Practical is tree-equivalent but its ref is not moved or merged as a separate product change. |

Stop after candidate submission; no autonomous integration/deployment.

New text evidence readable copies normalize trailing whitespace only; raw-integration-evidence.zip preserves byte-exact originals. Accepted ancestor evidence and MA dirty source audits remain untouched.
