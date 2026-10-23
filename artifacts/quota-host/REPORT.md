# Quota host candidate: partial production binding, fail-closed effects

Base: `f23b50a09542625c51a9a422677871b1d54b4d9d`.
Exact tested source: `ae4068b0` (full SHA in verification.json).
Branch: `feature/event-trigger-core-20261022`; original core worktree only.
The evidence-only commit following that source does not change executable files.
This is a candidate for MA review, not MA or human acceptance.

## Executable coverage / remaining matrix

| Requirement | Implemented behavior / evidence | Remaining production boundary |
|---|---|---|
| Default off / migration | Old ledger gets host enabled=false, empty sessionRoles; manual default unchanged. Host API user Bearer + revision; epoch fences old plans. final-host-http.xml tests disable/restart/persistence/expired old token. | No real profile opt-in applied. |
| Existing cc-switch read interface | ReadSource calls existing Bridge auth status --json with existing executable contract/timeout; whitelist managed default/IDs only. Owned CLI trace contains only auth status, never a mutation. | Actual GUI CLI not invoked in this batch. Its contract lacks native account binding and per-managed-account windows. |
| Native quota / provenance | Existing profile resolver and CodexQuotaStore format, latest raw snapshot only; fiveHour/week by actual duration, reset, local observed/received times, source, explicit missing providerUpdatedAt. No store lock/cache writes. | Cache is read-only; no active WHAM/provider refresh. Unknown all-account policy data prevents selection. |
| Unknown and sensitive data | Both timestamps fresh <=300s, future/stale/partial/missing reset/profile change/CLI failure remain unknown; payload bounded2MiB/windows32. No login/token/raw stderr projected. | Managed ID cannot be inferred from native ID or labels. Native sample status describes its observation, not a live account assertion; callers retain timestamps. |
| Single runtime owner | Existing legal_offline.SERVICE_ID, existing registered Worker map and cached Session objects; trusted RuntimeIssue metadata now includes same epoch. | No second owner, Worker registry or poller. |
| Native live process | Retained owned process handle only, existing raw FILETIME probe; PID-only/oneshot/unknown fails closed. Real Windows owned Python child confirms birth token then exit. | Does not attach to, scan or restart user processes. |
| Explicit role and unfinished execution | User-configured roles only; Worker task/sequence + Session active task + outstanding same service/worker/token/generation/execution; terminal result rejects. | Existing restart clears this evidence. Production cannot manufacture a continuation context from the frozen task. |
| Actual effect admission CAS | Trusted fixed-operation port calls single-use same-thread ticket synchronously at acceptance. Latest committed ledger command/epoch/config/disabled accounts, account observation, native worker/task/role and Pause/Illegal/readonly/lease/queue gates rechecked. Global write contention rejects without waiting or disk I/O on loop. | A port must accept synchronously; scheduling an unguarded later effect is forbidden. This local seam does not claim cross-process GUI CAS. |
| Isolation / races | Deterministic barriers change generation/PID/birth/owner/task/execution/account/role/gates/manual/close/host config. Unrelated Session queue progresses. Completed frozen task before new inventory also rejected. | Tests inject effects only; no real business mutation invoked. |
| Switch / model business effects | Fixed trusted ports and unavailable diagnostic; production ports are empty and cannot be enabled by API. | Existing auth default/provider switch has no remote identity+policy CAS. CBC model patch has no equivalent task admission. Need guarded seams in existing owners, not a new controller. |
| Restart / wake / idle successor | Injected chain switch -> restart -> idle successor -> exactly-once wake; latest samples invalidated after effect; accepted receipt proof and task identity required. Existing policy/idle tests rerun. | Legacy restart may spawn offline, awaits before stop/spawn, clears task; ordinary send may spawn offline or later consume queued work. Both remain unbound pending actual lifecycle admission + recovery proof. |
| MA ordering / persistent Jobs | Existing generic query Jobs and policy ordering preserved; compatibility HTTP and policy/idle regression pass. | MA resume admission and MA-last restart remain unbound with the full production chain. Do not claim end-to-end production recovery. |
| User disabled accounts | Independent durable disabledAccountIds defaults empty; revision CAS rechecked by ticket and original policy regression. | No user account hardcoded; no deployment input applied to any real profile. |

## Frozen validation

Command: `D:/project/Pan-main/.venv/Scripts/python.exe scripts/verify_quota_host.py`.
This script records exact argv, SHA, interpreter, per-command exit codes, JUnit
counts and logs in `verification.json`. All checks exit0:

- final-policy:186 passed (host43 plus existing policy/idle/safety143).
- final-compat:5 passed (Group/high-risk, query recovery, cancellation, close, plugin generation).
- final-http:1 passed; final-host-http:1 passed; final-native-compat:22 passed.
- Total215, failures0/errors0/skips0; diff-check and compile exit0.
- Windows utf8Mode0, stdout gbk, locale cp936, no PYTHONUTF8/PYTHONIOENCODING.
  Evidence/log/JSON/fixture text IO is explicit UTF-8. No dependency installs.

## Preserved failures and corrections

These exploratory JUnits used a dirty source and are not the frozen acceptance run:

| Evidence | Exit / failure | Correction |
|---|---|---|
| first.xml | exit1; one source-fixture assertion | Integer test clock avoids ISO microsecond roundtrip appearing slightly future; strict production freshness unchanged. |
| host-http.xml | exit1; own server startup assertion | Production factory received Job root. Now uses actual existing Session data root; owned launcher asserts exact root. http-fixed.xml exit0. |
| host-extended.xml | exit1; switch/restart chain stopped after switch | Invalidate pre-effect sample after accepted effect, including unknown. host-extended-fixed.xml exit0. |
| frozen-task-before-fix.xml | exit1 at source852afd65 plus new test; an injected switch accepted after original task completed | Admission now compares fresh inventory role/task to command's frozen role/task. frozen-task-after-fix.xml exit0, final sourceae4068b0 includes fix. |

Earlier regression/pre-freeze JUnits are retained as exploratory evidence and are
not added to the final215 count. Test assertions were preserved.

## Cleanup / untouched boundaries

`http-evidence.json` and `compat-http-evidence.json` record own port, temporary
profile, owned server PID(s), portReleased=true, ownedServerStopped=true. The
native retained-handle child is terminated/waited in finally and its post-exit
probe is asserted unknown. Owned fake CLI subprocesses complete via bounded run.
No actual account/provider call, credential write, user Worker restart/wake,
existing service operation, protected port8767/8768, shared dependency write,
React change, other-tree change, main/practical/integration move, push or release.

Next authorized production wiring must add canonical managed/native binding and
all-account query in the existing cc-switch owner, then guarded seams in existing
switch/model/restart/send/Job lifecycles and persisted successor execution proof.
No production effects can currently be enabled through this batch's API.
Actual GUI/provider/account effects and overall human acceptance remain untested.
