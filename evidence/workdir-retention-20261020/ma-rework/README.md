# MA review rework: nonblocking orphan workdir coordination

Base: `702b549b9deaad564d4599edfc222b968d4c9f50`. Only this isolated worktree is modified.

## Admission and deletion ordering

1. A reference transaction registers an active-writer count and advances its generation under the coordinator mutex. It then resolves/validates paths and executes its entire assignment or persistence operation **outside** that mutex. A save reads its live reference paths only after registration. Completion/error retires the count and advances the generation under the short mutex.
2. The collector reads one bounded reference snapshot off-loop, outside the mutex, and verifies that no transaction was active and the generation stayed stable. All candidate preflight tree traversals and reference comparisons use that snapshot without holding the mutex. A snapshot error or changed generation fails closed.
3. A candidate claim is admitted only if the reference generation still matches and no writer is active. Acquisition never waits for a writer; conflicts retain the directory with an observable reason. At most 32 claims can exist. Overlapping claims cannot coexist.
4. With the claim registered and **no coordinator mutex held**, the collector reloads final references, rechecks the complete tree/time/identity, pins directories and deletes only validated entries. New overlapping references (including ancestor/descendant and resolved aliases) are rejected immediately. Unrelated assignments and saves run normally. A write that started first prevents acquisition; a write admitted after acquisition cannot point into the claimed tree.
5. `finally` releases every claim. Only a completely successful deletion is added to a 256-entry completion cache. Failed scans/pinning/deletion do not leave a poisoned retirement marker. The old unbounded `_retired` set is removed. A bounded registry of 32 configured cleanup roots rejects missing managed paths even after completion-cache eviction; excess scopes fail closed rather than evict a safety scope. Explicit recreation of a real directory permits a new reference.

All disk work, path resolution, directory pinning, tree deletion, store functions and ticket waits occur outside the coordinator mutex. Its critical sections contain only counters, generation checks, bounded tuple snapshots and pure path comparisons (32 claims/scopes, 256 completion entries). The deadline is checked during reference metadata loading/path normalization as well as tree traversal/deletion; preflight no longer reloads every record for every candidate. An individual OS filesystem syscall cannot be forcibly interrupted by this cooperative deadline.

## Session lock-order audit

The existing global Session ordering is `_STORE_LOCK` → per-Session ticket reservation/wait; `_run_persistence_ticket` calls `state.begin()`, executes the operation, then `state.finish()`. The state condition mutex is released before the operation executes. `_save_body` keeps its reference-transaction registration so in-flight persistence cannot be missed, but it no longer owns the coordinator mutex while waiting for disk, using a summary lock or publishing cache state.

The coordinator never acquires `_STORE_LOCK`, a Session state/summary lock, or a persistence ticket while its mutex is held; neither collector nor reference admission has a wait-for-writer path. Therefore no coordinator → store/state reverse edge exists. This leaves the existing same-Session ticket ordering intact and avoids creating a new cross-Session serialization point. A test blocks a real Session metadata replacement, performs another Session's synchronous save and a name/index create, and then proves writer completion while `_STORE_LOCK` is held by the event-loop thread.

## Workspace mutation audit

Repository search found no product caller mutating `Workspace.dirs` via append/extend/remove/clear/insert/pop, index assignment or augmented assignment. Existing callers read `dirs` or use `workspace.update(..., dirs=...)`. The backing value is now an immutable tuple; the getter and `to_dict()` return new lists. Input values are staged before validation. Mutating a returned list cannot publish or persist a reference; setter/update transactions must be used. Tests verify append, index assignment and clear on returned values leave the original unchanged.

## Validation

`D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_workdir_retention.py tests/test_data_retention.py tests/test_data_catalog.py tests/test_workspaces_backend.py tests/test_eventloop_history_offload.py -q --tb=short --junitxml=evidence/workdir-retention-20261020/ma-rework/pytest.xml`

Result: **106 passed, 0 failed, 0 skipped**, exit 0. See `pytest.xml`. All mutable data and deletion targets are temporary.

New evidence includes slow background tree traversal, reference loading and deletion, with real synchronous unrelated Session assignment/save on the event loop and five heartbeat ticks completing in under 0.5 seconds while the background phase remains paused. It checks a new reference winning before claim preserves the directory, a late reference/creation during deletion rejects immediately, slow metadata persistence does not serialize unrelated saves/index creates, stable snapshot loading once per preview plus a final load per claimed delete, final-reference rejection, deadline failure, claim release, alias protection, completion-cache eviction safety, root-registry bounds and copy-only Workspace dirs.

No frontend source changed in this rework; the preceding 60-test Vitest/typecheck/build evidence is not re-reported as newly run. `git diff --check` passes. No real server/browser, protected-port requests, real workdir deletion, push, main/practical movement or service restart occurred. POSIX runtime and external processes bypassing Pan's metadata writer remain unverified, as before.
