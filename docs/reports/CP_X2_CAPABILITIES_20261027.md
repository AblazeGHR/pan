# CP-X2 POSIX Job ownership boundaries — candidate, not acceptance

Date: 2026-10-27. Baseline: `20955b3c8959c881797d84bcd03302158ddf08c3`.
Owned branch: `xplat/job-posix-safety-20261027`.

This independent matrix supplements the cross-platform plan. It does not change
or claim acceptance of the shared capability registry or the main matrix.

| Boundary | Windows | Linux candidate | macOS candidate |
|---|---|---|---|
| Birth identity for destructive ownership | Existing FILETIME/Job Object, regression tested | `/proc` starttime plus nonempty boot ID; target execution unverified | `ps lstart` remains diagnostic; check returns unknown, spawn explicitly unsupported before Popen |
| Leader missing/zombie before group signal | Existing implementation unchanged | Refuse signal, preserve unknown; persisted recovery retains pgid in remaining | Weak identity never authorizes group signal |
| Live leader changes group | Existing implementation unchanged | Recheck recorded/current pgid before every destructive signal, including escalation | Unsupported |
| Members/zombies/permission denial | Existing implementation unchanged | Snapshot current descendants; refuse observed escape/zombie; verify original snapshot members after signal; unresolved resources remain unknown | Unsupported |
| Canonical public cancellation | Existing P23 regression tested locally | Seven runnable HTTP/real-process successor nodes, target NOT RUN | One runnable public fail-closed successor, target NOT RUN |
| Arbitrary full-tree containment / adversarial concurrent escape | Existing Job Object semantics unchanged | NOT ESTABLISHED: process groups and descendant snapshots are not a kernel containment handle | Unsupported |
| Terminal / Rewind / lifecycle / secretStore | Unchanged | Outside scope | Outside scope |

## Audited production wiring

Canonical `POST /api/job-runs/{id}/operations` cancellation calls the original
IndependentExecutor target. Its HTTP cleanup request is fenced by original
Attempt, lease, epoch and generation. The original independent runner's
RecordingOwner verifies the ProcessOwner record, retains the original handle on
unknown, and performs cleanup only after an explicit request/recheck. Canonical
completion additionally requires ownedTree, identityVerified, treeTerminated and
supervisorExitProof. No final_service/API/ledger/coordinator/worker/P23 test or
shared schema changes are part of this candidate.

An exited POSIX leader cannot authorize cleanup of its old numeric pgid, even
with a retained Popen object. Normal process exit therefore does not establish a
successful whole-tree receipt under this implementation. The successor tests
explicitly require unknown/uncertain in that case; they do not treat it as
successful cancellation or successful execution. Destructive confirmation is
only tested as a candidate for a still-live, verified leader with observed group
members, followed by group and member absence and supervisor exit. Linux/macOS
runtime proof is still missing.

## Integration blocks for MA (not silently patched here)

1. `packages/core/platform/__init__.py::CAPABILITIES["processOwner"].supported_on`
   currently includes DARWIN. This contradicts the new explicit unsupported
   spawn boundary. MA must coordinate removal of DARWIN (or supply a platform
   private reliable birth identity and target evidence) and update its owned
   capability tests / main matrix before integration. No shared `process.py` or
   schema symbol change is required.
2. `tests/test_platform_process_contract.py::TestPosixProcessOwnerContract`
   currently expects macOS to perform real spawn. It will be red under the new
   fail-closed boundary; this is an expected target gate, NOT an existing skip
   modified by this TA. MA/owner must add an explicit unsupported contract for
   macOS rather than silently dropping coverage. The original shared suite also
   contains post-root-exit cleanup expectations that require review on Linux;
   do not relax them to claim full-tree parity.
3. Atomic arbitrary subtree containment, recovery after leader loss, and safe
   group-signal TOCTOU elimination remain unresolved. A pre-signal birth/group
   check and a member snapshot do not solve the check-to-signal race or discover
   every concurrent fork/escape. Do not promote Linux process-group cleanup to
   Windows Job Object equivalence. Failures must remain unknown and queryable.
4. Public independent `ExecutionStore.request_cleanup` refuses a terminal
   unknown result when the original supervisor/handle is unavailable. Canonical
   must keep the original operation/Attempt and unknown visible; this candidate
   does not add a new destructive recovery authority or terminal replay.

## CP-X1 runnable entries (do not merge this branch into CP-X1)

Fast cross-platform negative controls:

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_platform_process_posix_unit.py -q -o addopts=
```

Linux public slow layer (seven nodes selected, macOS node explicitly skipped):

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_posix_safety.py -q -o addopts= --tb=short
```

macOS public unsupported layer (seven Linux nodes explicitly skipped):

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_posix_safety.py::test_macos_public_execution_refuses_weak_birth_before_effect tests/test_platform_process_posix_unit.py -q -o addopts= --tb=short
```

The seven Linux nodes under `tests/test_job_posix_safety.py` are:

- `test_public_unknown_query_explicit_recheck_and_lost_response_identity`: real
  canonical cancellation; independent public HTTP stale epoch/generation/lease/
  Attempt rejection; denial-only identity fault; original-operation query and
  idempotent replay; cold reopen; explicit recheck; effect and cleanup exactly
  once; supervisor and child absence.
- `test_public_cancel_waits_for_original_supervisor_exit`: real tree cleanup
  cannot complete canonical cancellation while original supervisor is gated.
- `test_public_queued_cancel_does_not_create_attempt_or_process`: public queued
  cancellation creates zero effects, Attempts or supervisors.
- `test_original_uncertain_reconcile_rejects_old_epoch_generation_and_lease`:
  original uncertain Attempt/frozen release retained; ledger fence negatives
  supplement (do not replace) the public HTTP fence negatives above. Normal
  leader exit remains unknown and cannot be upgraded to success.
- `test_late_unknown_terminal_does_not_rebuild_retired_parent`: public retirement
  before delayed original unknown receipt; no replacement parent/Attempt/effect.
- `test_product_default_detached_executor_keeps_original_unknown_receipt`:
  production detached bootstrap/runner (no custom launcher) and retained
  bootstrap-exit receipt, original Attempt, no replay or second lifecycle.
- `test_public_leader_exit_keeps_live_subtree_unknown_and_queryable`: real leader
  exits first while child survives; public cancel/query remains unknown with
  original resource identity; no signal to stale pgid; fixture cooperatively
  releases its own orphan after assertions.

Fixture: `tests/job_posix_fixture.py`, dedicated copy of the owned P23 HTTP
composition with POSIX births, isolated roots and random ports. Public tests
have real HTTP clients and production runner/store/adapter paths. Only the first
identity probe may deny; no successful cleanup or terminal receipt is injected.
Every fixture closes its owned HTTP listeners, reaps its retained supervisors,
and checks captured process absence. Child release is test-only cooperative
cleanup, never product cleanup authority or a confirmed product receipt.

## Verification scope

Local host: Windows. Read-only `wsl --list --quiet` listed Ubuntu and
docker-desktop; `wsl --list --running --quiet` returned no running distributions.
No distro was started and no POSIX environment was installed. Linux/macOS
public nodes, native syscalls, and shared target-platform contract/import gates
are NOT RUN. Source/unit/collection evidence is not target-platform acceptance.
No remote CI, main/practical movement, push, deployment, protected service, real
data, or ports 8767/8768 were used. Only candidate branch commits are delivered.
