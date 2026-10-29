# CP-X1 independent minimal cross-platform CI

## Scope and lineage

Base `9b4506c94ac67a39093961b218b2f01d387d1049`; CP-X3 input `ba821167c0ca936d3cdac885f656c6acbe166c2a`, merged in `03a4fc46`. CI-only commits are `1cdaefac`, `dd01bf63`, `e19a59a0`, and `fa5a551a`. No mixed Job-unification tree is an ancestor. Existing `.github/workflows/test.yml`, including both Windows full-suite jobs, is unchanged. This validation is for minimal platform compatibility and original Job/Scheduler contracts, not unified Job migration or release acceptance.

## Gates and artifacts

`.github/workflows/minimal-platform-manual.yml` has five job instances: Windows 3.12/3.14, Ubuntu 3.12, macOS 3.12, and Ubuntu 3.12 browser. Each platform installs the declared Python requirements, imports the real server, collects `tests/`, `packages/qq/`, and `packages/wechat/`, verifies required extracted test symbols and their inclusion in CP-X3 FAST, runs that FAST list, adds original path/secret-store checks, and runs 12 original legacy Job/Scheduler create/update/cancel/retry/API checks. All subprocesses use `scripts/run_tests_isolated.py`; no ignores, deselections or simulated OS. Any failed step remains failed; later phases still run when possible to retain evidence.

The browser job installs Python and Node dependencies in the ephemeral runner, builds `packages/web` fresh (`pnpm build` includes TypeScript), records source/lockfile/requirements/dist hashes and tool versions, runs five frontend capability test files, then two authenticated owned HTTP/Chromium tests; the original Locker test also uses real stdio MCP against the isolated synthetic profile. The CP-X3 browser fixture uses synthetic capability policy and inert CLI inventory: it does not establish actual provider consumption. The isolated wrapper chooses a transient port; neither 8767 nor 8768 is intentionally used. Explicit teardown/port proof exists in the respective fixture outputs when those stages reach cleanup; missing proof is not success.

Four per-platform `minimal-platform-<os>-py<version>` artifacts retain per-phase logs, JUnit, result JSON, and installed Python versions. One `minimal-ubuntu-browser-py3.12` artifact retains provenance, frontend and browser logs, JUnit, CP-X3 browser/runtime JSON and available owned cleanup/shutdown JSON. Raw browser profile/database and keys are excluded. Result schema for supplementary phases is `pan.minimal-ci.phase/1` with source SHA, argv, phase, exit code, elapsed time, Python and host; the CP-X3 runner has its own result format. Collection counts are in `collect.log`: pytest's collect-only JUnit reports zero executed tests and must not be read as the collected count. `-ra` and JUnit record actual skip counts/reasons; in particular POSIX DPAPI/Windows ACL-only tests explicitly skip with `DPAPI / Windows ACL only`. Unsupported native Terminal/rewind/lifecycle and POSIX ProcessOwner are not represented as supported.

## Local Windows evidence

Evidence is in `evidence/cp-x1-minimal-baseline/` and `evidence/cp-x1-minimal-combined/`; only synthetic tests and isolated wrapper were used. JUnit hostnames are redacted. Windows Python 3.14.5 used `D:/project/Pan-main/.venv/Scripts/python.exe`; that venv was not installed into or changed.

- Baseline source `9b4506c9`, plus the then-uncommitted CI helper tests, full three-root collection: 6,379 nodes in 416 files, exit 0. Original 12 legacy nodes: 12 passed, exit 0. Baseline owned HTTP/stdio MCP synthetic fixture: one passed; this is not a browser result.
- Combined source `dd01bf63`: CP-X3 FAST 186 passed, zero skips/failures, exit 0. Test-generated tracked artifacts were restored after the run.
- Combined source `e19a59a0`: real server import and full three-root collection exit 0, 6,410 nodes in 421 files; fail-closed manifest check exit 0, original 12 legacy nodes 12 passed, supplementary 52 passed and zero skipped. Four dedicated helper tests are included in the supplementary layer. The subsequent `fa5a551a` changes only the workflow frontend step; none of these results is falsely attributed to a later source SHA.
- `ba821167` is CP-X3 input; its source-bound local browser evidence and any earlier unsuccessful attempt are retained in its own evidence, not replaced with this report. Linux, macOS, Windows 3.12, fresh Ubuntu browser, and the final combined remote workflow are unverified. Existing full Windows suite was not rerun; no conclusion is made about its historical failures.

## MA integration and exact remote trigger

After review, MA may use this branch HEAD as the combined candidate or integrate the four CI-only commits onto the exact CP-X3 input. Check the resulting HEAD and source guard before any remote action. The workflow is deliberately limited to one diagnostic push branch, avoiding any broad feature-branch pattern. To perform the initial single remote run, MA can replace `<remote>` and `<combined-sha>` with the reviewed exact values and execute:

```text
git push <remote> <combined-sha>:refs/heads/diagnostics/minimal-platform-20261029
```

That push itself starts the five jobs. A GitHub `workflow_dispatch` for a newly added workflow requires it to be registered on the default branch; once registered, a later manual-only run can use `gh workflow run minimal-platform-manual.yml --ref diagnostics/minimal-platform-20261029`. Do not dispatch an additional run immediately after the triggering push. Estimate: five concurrent job instances; wall time and runner minutes remain unmeasured, with configured 35-minute platform and 25-minute browser limits. The two existing Windows full-suite jobs are separate and remain required in their normal workflow. MA must inspect the exact combined SHA, artifacts, skip reasons, and all statuses before any acceptance or release decision.
