# JOB-RETIRE revision15 — configured cmd raw-tail repair

The unchanged revision14 quoted-shell positive now completes through the production Template/form, canonical Run and real owned process, writes its original file result and remains queryable under the original effect identity after cold reopen. The repair is limited to Windows command-line serialization; it does not alter the stored command, Action publication or process ownership algorithm.

Baseline: `a710d2c9859a8f1b89339f6fe9ac97f270ac5192`. Implementation commit: `8752e0524ebea7e171aa020987dcba02dd9853e3`; final tested source SHA and exact final commands are in `evidence/job-retirement-rev15-shell-20261029/commands.json` (the subsequent source commit only declares the Windows-only diagnostic's non-Windows platform boundary). Same worktree `D:/project/pan-worktrees/job-retirement-closure-20261027`, branch `r6/retirement-parity-acceptance-20261029-a`. Authority: MA revision15 addition to `D:/project/Pan/.workflow/job-unification-review-20261028/job-r14-shell-scope.json`.

## Exact product change

Only `packages/core/platform/process_creation_win.py` changes: private `_owned_command_line(args, env)` (18 lines) and replacement of the serialization expression in `OwnedPopen._execute_child`. Existing `create_owned_process`, flags, restricted token, handle inheritance, private desktop, suspended creation/membership proof, resume and cleanup remain byte-for-byte unchanged. Public ProcessOwner, helper/normalizer, providers, Worker, scheduler, contracts, publications, frozen plans and retry/ledger methods are unchanged.

The adapter applies only to exactly five argv elements: `[configured_interpreter, '/d', '/s', '/c', original_raw_command]`. It CRT-serializes the first four and appends one outer quote pair around the original command, without replacing its bytes. All other argv call the original `subprocess.list2cmdline(args)`.

Effective environment rules are explicit:

| Input | Treatment |
|---|---|
| `env is None` | Read inherited parent environment |
| Explicit child `env`, including `{}` | Use that environment alone; never fill COMSPEC from the parent |
| One case-insensitively named COMSPEC key, with absolute value, and absolute argv[0] matching by Windows normalized path/case | Recognize only the exact five-element shell envelope |
| Missing/empty COMSPEC, relative or drive-relative COMSPEC, relative argv[0], mismatching path, same basename elsewhere, ambiguous case-duplicate COMSPEC keys | No shell adaptation; retain original CRT serialization and execution/error behavior |
| Different flag case/order, omitted `/s`, extra argv | No shell adaptation; retain CRT serialization |

No cwd/PATH lookup, environment expansion, basename guessing or executable substitution occurs. A custom explicitly configured absolute interpreter is supported by exact identity; a renamed copy of real cmd is exercised below. This is configured interpreter identity, not binary attestation. Missing/unproven identity deliberately does not acquire the raw-tail behavior; no additional success claim is made for those configurations.

## Red-to-green and precise mapping

Historical artifacts remain at `evidence/job-retirement-rev14-20261029/product.xml` and `evidence/job-retirement-rev14-shell-diagnostic-20261029/focused.xml`/`native-lines.json`. The exact positive `tests/test_retirement_rev14_templates.py::test_production_shell_form_real_process_exit_and_original_lookup` is unmodified. Previously it observed exit1/failed Run/missing file; it now observes completed Run, original `owned` file result, exactly one effect, cold original-effect lookup and no duplicate effect on dispatch.

The owned comparison test `test_same_raw_command_legacy_shell_vs_current_crt_serialization` changes its prior bug observation from `[old=0,current=1,without-s=1,ordinary=0]` to `[0,0,1,0]`. Its current branch now asserts the exact raw-tail command line and successful file effect; the nonmatching `/d /c` branch still asserts CRT bytes and real failure. These assertions became stronger success requirements following the authorized repair; the original old observations remain immutable in the prior report/log/XML. The test has an explicit non-Windows native-capability skip for future collection; **this Windows run has zero skipped tests**.

New file `tests/test_retirement_rev15_shell_serializer.py` covers 22 cases:

- Fifteen exact identity/envelope cases: inherited or explicit child env, case normalization, custom identity, missing child/parent values, empty/relative/drive-relative values, relative executable, nonmatching path, same basename, duplicate keys and flag/arity nonmatches. Unknown identities retain byte-for-byte CRT output.
- Real custom renamed cmd with spaces in its absolute path, with parent COMSPEC deliberately pointing elsewhere, both hidden and nonhidden. The child executes the unchanged quoted Python command, produces the original file and closes the retained owned resources.
- Real ordinary Python argv with spaces, embedded quote, trailing backslash, quote-adjacent backslashes, Unicode, empty argument and shell-like flag strings, hidden and nonhidden. The child writes the exact original argument array; this detects serialization loss beyond a helper's predicted string.
- Three real missing/relative/nonmatching child-COMSPEC cases. They retain CRT native bytes and the original rejection/no marker, rather than gaining raw-tail handling from parent COMSPEC or a matching basename.

M07's Windows quoted-command gap is closed for the exact configured envelope. `parity-matrix.json` binds these successors and limits. Original scheduled-shell node `test_jobs_api.py::test_post_creates_scheduled_shell_action_without_target_or_runner` remains mapped to the r14 passive production caller/no-early-execution proof plus the now-green quoted execution successor and prior scheduled Creator evidence. This does not restore the old scheduled API or unknown generic fallback. M01/M05 retain their prior bounded status; no aggregate 43-node/remaining-nine acceptance is asserted.

## Focused verification and evidence preservation

Reproduce with the designated interpreter and wrapper:

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe evidence/job-retirement-rev15-shell-20261029/run.py
```

The shell group contains new serializer tests, the real comparison, all 14 r14 Template tests (including fresh production-bundle browser + real HTTP/queue/process) and the eight r11 mapping tests. The ownership group runs existing `test_platform_process_windows_hidden.py`, `test_hidden_job_token.py`, `test_cpx1_creation_edges.py`: creation-time membership before resume, hidden token and stdio/file IPC, no fallback after token/native failures, before/after-create cleanup, tree/desktop termination, hard runner death, base and owned temporary venv edges, and nonhidden behavior. Those assertions were not edited.

Final tested source **`87aa0f455fed0d486986821a3c2df21d18acc428`**, Python **3.14.5**, Chromium **153.0.8010.12**.

| Group | Passed | Failed | Skipped | Exit | Seconds |
|---|---:|---:|---:|---:|---:|
| shell | 45 | 0 | 0 | 0 | 17.141 |
| ownership | 26 | 0 | 0 | 0 | 4.19 |

Final runner exit **0**, tracked-source and historical-proof invariant passed. Counts are 71 total, not added to historical or initial runs. No dependency installation or frontend source change. The embedded browser test rebuilds its owned production harness; no separate large React/type/build matrix is repeated.

The initial focused run passed both groups but its final all-tree invariant returned exit1 because existing platform tests rewrote fixed-path tracked proof files. No product source changed. Initial commands/logs/XML and changed proof outputs are preserved under `first-run/`, including `orchestration.json`. The runner now snapshots historical tracked proof bytes, archives this run's rewritten versions under `native-proof/`, then restores the historical bytes before the final invariant. A successful run before the platform declaration is preserved under `pre-platform-declaration/`; final results remain at the evidence root. This is evidence handling, not a change to test outcomes or shared fixtures.

The existing proof helper also produces new synthetic `kill-tree-*-direct/ancestor.json` files in `evidence/cp-x2-windows-hidden-20261028/proof`; they are preserved with this candidate. Historical tracked proof content is restored, and prior r6–r14 red files are unchanged. `cleanup.json` records evidence hashes and the final browser port observation. Browser/HTTP/cold-service tests close in `finally`; comparison processes close original owned handles, guards and desktops; ownership tests assert disappearance of their own trees/desktops. The earlier ignored dependency junction remains untouched, with its prior blocked cleanup documented in r14.

## Platform capability and acceptance limits

| Platform/configuration | This candidate's evidence |
|---|---|
| Windows 11, designated Python3.14.5, exact absolute configured cmd envelope | Real quoted process, ordinary argv, hidden/nonhidden, HTTP/production-bundle Template and native ownership tests |
| Windows missing/relative/nonmatching configured identity | Existing CRT path retained and real negative behavior checked; no guessed shell adaptation |
| POSIX | Product source unchanged; execution not rerun in this Windows-only follow-up |
| Other Python versions / remote platforms | Not run; no remote CI claim |

This is the independent matrix for this narrowly scoped platform behavior, not an update to the separately frozen platform release. No source was cherry-picked into its tree; no main/practical, push, remote CI, deployment, existing service, real account/profile/data or 51/60 collection was touched. Full application browser navigation, real migration, monitoring/stable-reset, unknown fallback/clear-target and unrelated migration capabilities remain outside this repair. Candidate completion remains subject to MA review/integration.
