# CP-X2 POSIX ProcessOwner / public Job successors — revised candidate

2026-10-27. Supersedes the rejected `43765dbb` capability conclusions; that
candidate and its evidence are retained. Integration-only, not MA acceptance.
No changes to CAPABILITIES, the main platform matrix, CI, schema or data structure.

| Boundary | Linux candidate | macOS candidate | Evidence this revision |
|---|---|---|---|
| Strong birth | `/proc` starttime + boot ID | Native libproc uniqueid / idversion / start timeval + boot session UUID | Windows seam/ABI controls only; both native platforms NOT RUN |
| Weak ps lstart | Diagnostic only, never a fallback | Diagnostic only, never a fallback | Negative controls |
| Normal leader exit | Retained Popen exit + group/member absence permits read-only success | Same | Product-executor controls; real HTTP success successors runnable, NOT RUN |
| Idempotent terminate/kill_tree | Recheck absence without another signal | Same | Local controls; unchanged shared contract runnable, NOT RUN on target |
| Same-process original-record recovery | Reuse original retained Popen only for the exact birth and pgid, reap it normally | Same | Local retained-object negative/positive controls |
| Lost leader / live subtree / denied probes / zombies / observed escape | Preserve unknown/resource identity; no dead-leader group signal | Same | Local controls; live-subtree HTTP successor runnable, NOT RUN |
| Arbitrary concurrent fork / escape containment | Not established by group snapshots | Not established by strong identity or group snapshots | Explicit remaining limitation |
| Terminal / Rewind / lifecycle / secrets | Outside scope | Outside scope | Unchanged |

## Read-only proof and destructive authority

`terminate` and `kill_tree` first inspect retained root exit and known members.
A dead leader with a proven empty group and no surviving observed members can
return a confirmed read-only result. A group/member that still exists, a zombie,
permission/query failure or an earlier uncertain member scan prevents clearance.
No read-only result authorizes a signal. Signal paths still require exact birth
and current pgid before every destructive signal, including escalation.

`wait` observes descendants before bounded waits and retains their identities.
Clearing a numeric group does not erase an observed member that escaped it.
This improves resource accounting but is not kernel containment and cannot prove
that every adversarial concurrent fork/escape was observed. A new process owner
in the same Python process can reuse an actual retained Popen only by its exact
identity key and matching pgid; it never manufactures a wait handle from a PID.
Cross-process recovery without that object remains conservative, particularly
when root zombies or a lost leader prevent a complete resource proof.

Normal product wiring is unchanged: canonical public Run admission ->
IndependentExecutor -> original independent runner -> TypedExecutor._process ->
ProcessOwner.wait -> kill_tree -> original receipt. Previously, a normal root
exit made kill_tree throw before any read-only proof and TypedExecutor converted
all such executions to unknown. The revised read-only branch permits a succeeded
receipt only when root exit, identity/retained wait and group/member absence all
hold. Failed clearance still yields unknown. Public cancel additionally waits
for the original supervisor's exit; epoch/generation/lease fences are unchanged.

## Darwin native ABI and one-generation capture

Apple sources were read from official `apple-oss-distributions/xnu` and pinned to
commit `f6217f891ac0bb64f3d375211650a4c1ff8ca1ea`. URLs, hashes and exact relevant
line locations are retained in `artifacts/cp-x2/revision/apple-sources.json`.
No Apple runtime or SDK was executed on this Windows host.

- The function signature and base availability annotation (`proc_pidinfo` since
  macOS 10.5) come from [Apple libproc.h](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/libsyscall/wrappers/libproc/libproc.h).
  The annotation does NOT establish availability of flavor 18 on every such OS.
- Flavor 18 and the combined structure come from [Apple proc_info_private.h](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/sys/proc_info_private.h).
  This is a platform-private interface: this candidate supports 64-bit Darwin
  only when the symbol, flavor, expected copyout ABI and boot UUID all work.
  No historical minimum macOS release or broad OS support is certified here.
- BSD field ordering is from [Apple proc_info.h](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/sys/proc_info.h);
  MAXCOMLEN=16 and 32-bit uid/gid definitions are separately pinned in the source
  manifest (`param.h`, `_uid_t.h`, `_gid_t.h`, `_types.h`).
- The [Apple copyout implementation](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/proc_info.c)
  holds a referenced process while filling the combined BSD/unique info and
  returns its complete structure size. Argument 1 includes zombie lookup.
  Birth/group/status therefore do not come from separate PID queries.
- The read-only boot session domain comes from [Apple kern_sysctl.c](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/kern_sysctl.c).
  The UUID is strictly parsed before and after the single process query and must
  agree. Current observed UUID and the full original birth string must match
  the persisted record during subsequent checks.

The implementation uses native alignment (no packed layout), fixed-width integer
types and these checked 64-bit ABI offsets:

| Structure | Size / alignment | Relevant offsets (bytes) |
|---|---|---|
| proc_bsdinfo | 136 / 8 | status 4; pid 12; pgid 100; start seconds 120; microseconds 128 |
| proc_uniqidentifierinfo | 56 / 8 | executable UUID 0; uniqueid 16; parent uniqueid 24; idversion 32; original parent version 36; reserved 40,48 |
| combined BSD + unique | 192 / 8 | BSD 0; unique structure 136; uniqueid 152; idversion 168 |

Runtime code checks these dimensions/offsets, exact 192-byte return, matching
PID, valid pgid/status/timeval and nonzero kernel uniqueid. Missing symbol/flavor,
ABI mismatch, failed/short/oversized copyout, absent/invalid/changed boot UUID,
malformed fields or parsing failures produce unknown, never weak-ps authority.
The birth string is `uniqueid:idversion:start_sec:start_usec`; boot UUID is the
existing boot_id field. Executable UUID is excluded because exec is not birth.

Apple's [process identity validation](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/kern_proc.c)
separately handles exec versions and lifetime unique IDs. If our observed
uniqueid/boot domain still identifies the original process but the exact birth
string/version changed, check returns unknown, not "PID reused/original exited".
This prevents a live supervisor exec from becoming a false exit proof. Strong
identity still does not provide subtree containment or an atomic group-signal
handle; those limits remain visible.

## Shared contract authorization

MA explicitly authorized this TA to add `proc_uniqueid` only to
`ProcessOwnerContract.test_spawn_binds_exact_identity`'s kind whitelist and to
update ProcessIdentity explanatory text. All other original shared assertions,
nodes and skip conditions are unchanged. No registry removal of DARWIN and no
schema/data-field change were made. The original complete shared suite remains
a target-platform gate; runtime compatibility is not claimed without running it.

## CP-X1 / MA executable gates

Fast controls (all platforms):

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_platform_process_posix_unit.py -q -o addopts=
```

Original shared ProcessOwner suite + real public successors (Linux/macOS):

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_platform_process_contract.py tests/test_job_posix_safety.py -q -o addopts= --tb=short
```

`tests/test_job_posix_safety.py` now has these real nodes, with a dedicated owned
fixture, temporary roots and random non8767/8768 HTTP ports:

1. `test_public_unknown_query_explicit_recheck_and_lost_response_identity`
2. `test_public_cancel_waits_for_original_supervisor_exit`
3. `test_public_queued_cancel_does_not_create_attempt_or_process`
4. `test_original_uncertain_reconcile_rejects_old_epoch_generation_and_lease`
5. `test_late_success_terminal_does_not_rebuild_retired_parent`
6. `test_macos_public_native_identity_and_original_success` (macOS-specific)
7. `test_product_default_detached_executor_keeps_original_success_receipt`
8. `test_public_leader_exit_keeps_live_subtree_unknown_and_queryable`
9. `test_public_success_cold_reopen_preserves_original_attempt_once`

The common eight run on both Linux and macOS; the ninth selection on Linux is
the explicit macOS skip. Public cancellations use canonical and independent HTTP
and reject stale fences over HTTP. Ledger negatives supplement those checks.
Normal and late successful receipts require actual absence evidence, original
Attempt preservation and no replacement effect. Unknown/lost leader cases stay
queryable. Fixture instrumentation records actual nonzero group signals in
kills.jsonl and successful/unsuccessful cleanup observations in cleanups.jsonl;
a read-only cleanup is not mislabeled as a destructive signal. Success/cancel
assertions require one effect and one cleanup observation where applicable.

This host has no running WSL distribution; none was started or installed.
Linux/macOS native import, shared suite and public real-process probes are
runnable but NOT RUN here. Windows controls/real Windows P23, target-platform
runtime, remote CI, deployment, migration and human acceptance remain separate.
