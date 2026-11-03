# CP-X5 service lifecycle review candidate

Implementation branch: `feat/cp-x5-posix-20261103`.
Validation branch: `validation/x5-service-20261103`.
Baseline: `c9dc621a01ccb568878c8a37297c034cbaec18de`.
Code candidate: `e6749bde978c8befd05ac0ce61b34e3f3ce21884`.
Final target CI: [41574960466](https://github.com/AblazeGHR/pan/actions/runs/41574960466),
exact code candidate above, completed **success** on Windows/Ubuntu/macOS.
Only evidence/docs are added after this source SHA; they are not pushed again to
trigger redundant CI. The commit containing this report is the local evidence
supplement, not a different tested source claim.

| Target (Python 3.12, hosted runner) | Contract subset | Native main/CLI/HTTP |
|---|---|---|
| windows-latest | 92 passed, 5 skipped | 1 passed, 0 skipped |
| ubuntu-latest | 95 passed, 2 skipped | 1 passed, 0 skipped |
| macos-latest | 95 passed, 2 skipped | 1 passed, 0 skipped |

All rows have zero failures/errors. POSIX's two skips preserve the production
capability=false boundary for old detached-supervisor tests; the separate native
candidate trial runs without skips. Windows skips three POSIX-only native group
tests and two unsupported-host-only tests; explicit injected unsupported controls
still run. Exact identities/reasons are in r9/ci41574960466-summary.json.
Original full JUnit/JSON/command artifacts are preserved byte-for-byte in
r9/ci41574960466.zip with per-file SHA256 manifest. All three native files were
checked for exact source SHA and true original-request/Worker-surrogate/CLI/lock/
port-release facts. The terminal waiter Job job_9519cac1d8d5060ff305ac5d was verified
completed/exit0, notice delivered, processTreeEnded=true and snapshotCleanup=cleaned.

## Behavior and ownership

This candidate retains the fixed `system_main_lifecycle` Action, trusted host
authorization, original ExecutionStore/Attempt/claim/receipt, Worker preparation,
running strategy, cancellation boundary and no-replay semantics. No second engine,
ledger, scheduler or budget counter was introduced.

| Source | Change |
|---|---|
| `packages/core/platform/service_process.py` | Thin Windows Popen/POSIX ProcessOwner spawn boundary; original retained POSIX handles remain strongly referenced until confirmed cleanup or launcher exit; this is an in-process handle table, not persistent execution state |
| `packages/core/platform/service_process_posix.py` | Original strong leader/boot/group snapshot; graceful snapshot merges previously observed members; read-only original group/member absence proof; missing/weak/changed evidence and residual members never authorize stale PGID signals |
| `packages/core/platform/service_network.py` | Darwin same-user per-process listener owner probes and explicit loopback availability evidence; query failures/busy inaccessible listeners remain unknown, without sudo |
| `packages/core/platform/execution_host.py` | Original offline proof includes Darwin network evidence rather than treating root-only global connection enumeration as absence |
| `packages/core/launcher.py` | Existing launcher policy calls the platform backend; initial snapshot persists with main record; startup cleanup uncertainty retains original state; a new start cannot overwrite original unknown tree; POSIX interpreter listener handoff is explicitly unsupported; no-PID/unknown listener cannot claim offline |
| `packages/core/main_lifecycle.py` | Original intent query remains available when capability is false, with Job/plan/Attempt fencing and no new admission; canonical restart uses the existing CLI start lock; offline receipt includes POSIX tree evidence |
| `packages/core/cli.py` | POSIX unresolved tree and Darwin unknown/busy listener remain status=unknown; Windows status path is preserved |

Windows normal spawn, venv listener adoption and native executor are preserved.
Startup cleanup uncertainty now retains evidence instead of clearing it. POSIX
listener-child adoption cannot silently discard original group identity: failure
reports `unsupported_interpreter_handoff` and cleanup uses only the original owner.

## API and persisted fields

No new public endpoint, Action definition, request field or database migration.
The original HTTP lifecycle route derives its requestId from
root/port/operation/PID/birth; it does not accept a caller requestKey field. A caller
must retain original requestId/effectId and query that receipt, not submit a new key.
Capability=false blocks new effects but cannot mask an existing unknown transaction.
Authorization and original Job/plan/Attempt checks remain mandatory for lookup.

Additive fields in the existing checkout-local process.json:

- `main.processIdentity`: existing `pan.process-identity/1` native identity.
- `main.processTreeSnapshot`: original leader plus observed native member identities.

Additive proof fields: canonical `offlineProof.serviceTreeProof` on POSIX and
`offlineProof.listenerProof` for the network observation (null on the historical
non-Darwin path). Lifecycle phases/receipts still reside in the original execution
store. Cancellation closes transactionally before stopping; interrupted stop/start
and startup/lock uncertainty do not permit an automatic retry.

## Verification scope

The native harness archives a committed source into an owned temporary checkout,
creates a fresh synthetic profile, disables QQ/WeChat/remote/memory and uses a
dynamic non8767/8768 port. It invokes real Pan main.py, CLI, HTTP Action transport,
independent bootstrap/supervisor, original receipt store and OS ownership backend.
No provider CLI, account or existing managed Session is invoked.

Only that disposable marker-fenced copy injects service.lifecycle for candidate
execution. Product capabilities remain unchanged: POSIX service.lifecycle=false,
hiddenDesktop=false. This is native candidate evidence, not product rollout.

Native assertions include:

- Startup Worker OS-process surrogate calls CLI start; both exit before the
  independent observer confirms main identity and HTTP liveness.
- HTTP stale birth and ask-policy rejection occur without effect admission.
- HTTP canonical restart proves original main offline and a new verified
  PID/birth/root/listener/HTTP ready; POSIX receipt includes original group proof.
- Original requestId/effectId reconstructs and queries the original receipt without
  a new execution. CLI preserve restart, offline exit and running/stopped status work.
- A different OS process holds the original start_lock. CLI start is refused;
  canonical restart stops the old main then records unknown at lock refusal.
  Querying after lock release cannot replay start.
- Original supervisor exit and owned port release are confirmed before cleanup.

Shared/native negative controls cover missing snapshot, old boot/birth/identity,
permission/query error, original leader exit with a live observed child, no stale
group signal, post-spawn strong identity failure cleanup, startup timeout, missing
executable, unknown startup record retention, unsupported interpreter handoff,
new-start exclusion against original unknown tree, original receipt fences and
pre/post-effect cancellation windows. Some failures are injected at explicit seams;
they are not evidence of every real multi-user permission configuration.

## Evidence and original failures

Earlier failed CI and original fixtures remain preserved; assertions were not
deleted or changed into success. Original launcher policy mocks gained their new
explicit tree-proof prerequisite; Windows venv policy is explicitly exercised as
that policy on all test hosts. Native group/absence/permission controls remain
separate. Policy units do not bind their legacy 8767/8768 mock ports.

| Run | Source | Result and evidence |
|---|---|---|
| 41570220736 | bbf20833 | Windows passed, POSIX contract failures; original full JUnit/fixtures in r3/ |
| 41571112431 | 44ca027f | Original capability/fixture failures resolved; exposed native POSIX handle cleanup and Darwin readiness; archived in r3/ |
| 41571818736 | d23193f5 | Three native chains passed; Darwin synthetic HTTP fixture readiness failed; archived in r4/ |
| 41572265781 | 5533b845 | Three-platform contracts/native all passed; archived in r5/ |
| 41573775275 | 7b447d91 | Three-platform contracts/native all passed including Worker-surrogate exit and startup ownership safeguards; archived in r8/ |
| 41574960466 | e6749bde | Final three-platform contracts/native all passed, including Darwin unknown-listener status/offline controls; archived in r9/ |

The DNS-free fixture preserves the same HTTP/identity/cleanup assertions. Original
Darwin evidence showed a live strong identity and occupied-but-not-listening socket.
CPython HTTPServer calls getfqdn after bind and before listen, which is consistent
with a DNS stall; the original run has no stack sample, so the exact historical
stack is not asserted. The fixture now explicitly forbids getfqdn.

## Capability, equivalence and acceptance boundaries

The concrete service start/exit/restart/status path is a candidate for parity in
the tested same-user, direct-Python-interpreter scope. POSIX groups are not a
containment boundary against arbitrary unobserved escaped descendants. The proof
scope is original group + observed members, and uncertainty remains explicit.
This does not establish equivalence to Windows desktop/token/Job mechanisms.

debug remains foreground with weak PID/time identity: no retroactive native group
kill permission is inferred. Old POSIX records lacking the original snapshot may
require explicit reconciliation rather than implicit adoption. POSIX interpreter
wrappers which move the listener to another PID are not supported by this candidate.

MA scoped independent survival to startup terminal/Worker exit. Logout persistence,
boot registration and systemd/launchd installation are later work; no real system
service was registered. PTY, detach, Rewind, secretStore, installation and hiddenDesktop
were not broadly implemented. Their feature/safety/OS-specific parity matrix,
official sources and proposed file ownership are in
`docs/plans&overviews/CP_X5_PARITY_20261103.md`.

No full repository suite, browser/UI, real provider/Worker Session draining,
cross-platform package installation, real-data migration, deployment or human
acceptance is claimed. No frontend source changed. The target workflow installs,
imports through the focused suite, collects and runs the service contract subset;
it is not the full main CI.

Protected refs observed before final report: origin/main
`c9dc621a01ccb568878c8a37297c034cbaec18de`, origin/practical
`4af89c694e37fd0d2c1e378e9ad51e5c9edaafdd`. Only the validation branch was pushed,
non-force. Final acceptance and capability rollout remain with MA.
