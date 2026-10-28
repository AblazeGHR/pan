# CP-X2 real remote repair — candidate, not target-platform acceptance

## Frozen scope and provenance

- Same Session / worktree / branch: `ses_ccbec39f8478974f`,
  `D:/project/pan-worktrees/xplat-job-posix-safety-20261027`,
  `xplat/job-posix-safety-20261027`.
- Started from clean `80cbf35c`; verified ancestry, then authorized fast-forward
  to exact clean integration `036fe85f23b689195207d6794db0a70d78d87fc2`.
- Real failed remote run: **40637381532**, completed/failure, exact tested code
  `5adbf3662b8e06e8eae13bf4aa5320a336bfa7a7`.
- Frozen repair source: `b4740d27da4ef610fabd22e695af4ca52f4a0424`.
  Source hashes in `source-manifest.json`; final evidence-only commit is given
  in the final delivery message. Existing evidence and prior commits retained.
- Brief read before action:
  `D:/project/Pan/.workflow/job-unification-review-20261028/brief-cp-x2-remote-repair.md`.
  D1-D6, migration v2 and optimization register were read and preserved.

## Real red evidence and diagnosis

Original input lives under
`D:/project/Pan/.workflow/job-unification-review-20261028/remote-ci-40637381532/`.
All source inputs remain unchanged. This candidate retains raw copies of
run.json plus macOS/Ubuntu fast.xml/results.json in
`original-remote/original-artifacts.zip`, full owned-node tracebacks in
`original-remote/owned-failure-inventory.json`, and hashes/sizes of originals,
including failed-steps.log and complete inventory, in `original-remote/manifest.json`.

The owned failure inventory has macOS **14 records / 12 unique nodes** (11 test
failures + 3 teardown errors) and Ubuntu **1 failure record**. The brief's 15
macOS total includes a different owner's failure; no equivalence was assumed.
Windows fast.xml has no failures in these owned process/log nodes. Collection,
quota/system-field/server startup and secret-store failures belong to other
owners and were not patched here.

**macOS observed:** shared wait-timeout returns UNKNOWN while its child still
runs; kill-tree refuses leader authority; multiple public safety tests fail the
exact birth equality between spawn receipt and later native read; cancellation
and log cases have related teardown errors. The downloaded platform-fast
artifacts contain only results.json / collect.xml / fast.xml, not per-execution
SQLite records or complete before/after native field pairs. Thus native-copyout
and timing details cannot be retroactively certified from those artifacts.

**Code/source diagnosis:** the birth string included idversion, which can change
on exec without ending the kernel process lifetime. Official CPython 3.12.10
Mac/Tools/pythonw.c uses POSIX_SPAWN_SETEXEC for the framework launcher. The
implementation can bind its pre-final-exec identity, then misclassify the same
owned lifetime as UNKNOWN after the launcher transition. A local copyout
negative reproduces this exact semantic error; this is not a claim that every
remote failure already has an observed before/after pair. The next remote run
must validate closure and reveal any remaining native/timing failure.

**Ubuntu/macOS log observed:** the large combined-log success fixture's parent
exits after .3s while leaving a child sleeping 30s. It then expects completed and
full cleanup. POSIX intentionally cannot signal a stale pgid after leader loss;
the actual uncertain result is consistent with that safety boundary. MA approved
correcting the success fixture to wait for its child naturally and adding a
separate original-identity leader-loss negative, preserving original assertions.

## Narrow repair

1. `read_darwin_identity` now emits explicit
   `lifetime-v1:kernel_uniqueid:start_sec:start_usec`, still under exact native
   boot session UUID. The existing single flavor18 copyout, full ABI checks,
   status, PID, pgid and pre-signal revalidation remain. idversion/executable UUID
   are not lifetime birth; no executable-pin or handshake redesign was added.
2. `process_posix` parses versioned lifetimes strictly. Legacy four-number
   records, unknown versions, malformed/missing data stay UNKNOWN, no signal,
   no silent adopt, no false supervisor exit. Different valid uniqueid/boot is
   rejected; changed start timeval within the same uniqueid stays UNKNOWN;
   changed pgid refuses destructive authority. Original Attempt/lease/epoch/
   generation fences are unchanged. Weak ps never grants authority.
3. Shared ProcessOwner test assertions/nodes/skips stay intact. The two forged
   PID-reuse inputs now create a valid DIFFERENT uniqueid in the versioned
   format, so identity mismatch still requires ALIVE/ours=False and
   refused_identity; malformed-record UNKNOWN behavior has separate negatives.
4. Large-log success retains 7000 exact Unicode/stdout and stderr blocks,
   child output, original Attempt/log reference, cold reopen, archive, no rerun
   and child absence assertions. Its parent waits for child natural exit.
5. New public POSIX large-log leader-first negative keeps the original child
   live, expects unknown cleanup/cancellation, queries the original resource/log
   and one effect, then cooperatively releases only that owned child. The
   original `test_public_leader_exit_keeps_live_subtree_unknown_and_queryable`
   was not changed to green.
6. Fixture teardown accepts a genuine terminal receipt racing its cleanup
   request instead of waiting forever for a cleanupRequest status that will
   never settle. Product assertions are not relaxed. Log teardown now requires
   positive supervisor-exit proof; `not supervisor_alive` is insufficient.
7. Bounded diagnostic helper saves sanitized proofs BEFORE cleanup can fail,
   and after cleanup succeeds, under `evidence/cp-x1-ci/cp-x2-proof/`: max 32KiB
   per file, four executions, sixteen probes, sixty-four member PIDs. It stores
   original fence/identity/outcome and cleanup/supervisor proof, omits argv/env,
   tokens, credentials and log bytes. Sanitization/bounds have a local negative.
   CP-X1 explicitly confirmed its existing directory upload includes this path;
   no CI/helper/AF_UNIX/quota edits were made.

No shared execution_host.py, schema, registry, main matrix, final service,
ledger, coordinator, worker or plugin product changes. Strong identity still
is not arbitrary fork/escape containment; lost leader, observed escaped member,
permission refusal, zombies and uncertain membership remain explicit unknown.

## Primary sources

`primary-sources.json` retains exact official URLs/version/line ranges, including
Apple XNU commit `f6217f891ac0bb64f3d375211650a4c1ff8ca1ea` and prior pinned header
hashes/layout references. CPython tag **v3.12.10**, Mac/Tools/pythonw.c lines
133–140 (SETEXEC), 188–198 (framework/venv launcher context), 212–221 (spawn/exec)
was read through the web tool. Apple kern_proc.c lines 478–490 separate the
exec-version predicate from the always-checked unique ID. Remote macOS
results.json identifies Python3.12.10; no Apple/CPython environment was installed.
The baseline native ABI layout remains unchanged; no new claim of native ABI
acceptance or OS-version coverage is made by Windows seam controls.

## Validation commands and evidence

All local commands use the specified
`M = D:/project/Pan-main/.venv/Scripts/python.exe`, observed Python **3.14.5**, and
`scripts/run_tests_isolated.py`. No P23-private venv is used for this repair.
Two independent runtime suites are separate because prior observed runtimes are
about two minutes each; neither command is expected to exceed three minutes.

Frozen-source Windows process / original P23 / executor regression:

```text
M scripts/run_tests_isolated.py -- M -m pytest tests/test_platform_process_contract.py tests/test_platform_process_posix_unit.py tests/test_job_posix_safety.py tests/test_job_retained_safety_protocol.py tests/test_job_retained_safety_owned.py tests/test_job_retained_safety_canonical.py tests/test_job_retained_safety_public.py tests/test_job_final_executors.py -q -o addopts= --tb=short --junitxml=artifacts/cp-x2/remote-repair/windows-process-final.xml
```

Frozen-source Windows logging regression:

```text
M scripts/run_tests_isolated.py -- M -m pytest tests/test_job_process_logs.py -q -o addopts= --tb=short --junitxml=artifacts/cp-x2/remote-repair/windows-logs-final.xml
```

Final process/executor suite: **168 passed / 29 skipped**, exit0, 121.94s.
Final log suite: **20 passed / 1 skipped / 2 existing dependency deprecation warnings**,
exit0, 107.79s. Exact XML totals are retained in `validation.json`.

Frozen-source collection:

```text
M scripts/run_tests_isolated.py -- M -m pytest tests/test_platform_process_contract.py tests/test_platform_process_posix_unit.py tests/test_job_posix_safety.py tests/test_job_process_logs.py --collect-only -q -o addopts= --junitxml=artifacts/cp-x2/remote-repair/collection-final.xml
```

Exit0, **164 nodes collected**, .64s; collection is not execution. Isolated
`import packages.web.server`, process_posix and process_darwin also exit0.

Local baseline semantic red reproduction:

```text
M scripts/run_tests_isolated.py -- M -X utf8 artifacts/cp-x2/remote-repair/run_lifetime_baseline_red.py
```

Exit1, **2 failed / 90 deselected**, .40s. It loads only baseline Darwin source
in memory; does not modify checkout or target processes. Same lifetime with only
idversion changed no longer matches, demonstrating the regression. Real remote
red remains separate from this simulated copyout evidence.

Pre-freeze evidence (not substituted for final frozen-source checks):
process-pre-freeze.xml **114 passed / 29 skipped**, exit0, 2.20s;
logs-first.xml **112 passed / 1 skipped / 2 warnings**, exit0, 107.99s.

## Next authorized target gate

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_platform_process_contract.py tests/test_platform_process_posix_unit.py tests/test_job_posix_safety.py tests/test_job_process_logs.py -q -ra --tb=short
```

Existing fast gate files already include all these nodes. Newly relevant native
node: `tests/test_job_posix_safety.py::test_macos_framework_launcher_exec_keeps_owned_lifetime`.
It deliberately uses sys.executable's real framework launcher, waits for owned
readiness, explicitly execs again through a gate, and checks original lifetime,
wait timeout, normal finish and repeated no-op cleanup; no sleep-based birth
stabilization. New negative:
`tests/test_job_process_logs.py::test_large_unicode_log_leader_loss_remains_unknown_and_original_queryable`.
It is POSIX-specific; the original leader-loss public negative remains intact.

This candidate has NOT run a new Linux/macOS CI job. The prior target failures
are real; corrected native green requires MA's separately authorized rerun.
No claim of baseline equivalence for unrelated remote failures is made.

## Resource and Git boundary

Final runtime-output cleanup checks and original evidence restoration are in
`validation.json`; generated outputs are archived under this report directory,
not mixed with historical P23 evidence. Initial tree was clean, so only our
identified generated files are restored/removed after their bytes are saved.
No other worktree, service, data/profile/account or protected listener is touched.
No new TA, main/practical movement, push, remote dispatch, deployment, WSL startup,
installation, registry/matrix edits or duplicate manual completion send.
The two manual Pan messages were non-report coordination: lifetime semantics to
MA before change, proof-upload path to CP-X1. Final delivery uses automatic reporting.

Final resource accounting: 13 original P23 owned-instance documents, 14 logging
owned-instance documents, 28 bounded sanitized proof files. All fixture ports released;
P23 captured processes and logging supervisors have no remaining entries. Original
evidence bytes restored and exact generated untracked files removed only after archive.
Source hash manifest unchanged after validation.

The logging JSON statistics were tracked and byte-identical to baseline, so
they did not appear in git diff. They are explicitly copied after the final run;
28 newly generated before/after sanitized proof files independently identify
current-run process records. No historical log result is substituted for the
final XML execution evidence.
