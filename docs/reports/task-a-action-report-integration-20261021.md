# A8 + accepted Action integration candidate

Worktree `D:/project/pan-worktrees/task-a-history-read-20261021`, branch `feature/task-a-history-read-20261021`. Initial HEAD c5a9140de62dbe0ac4ae36accb7da3b41368f3b2 verified clean. Accepted target baseline 69ca7574912f57f4b8cc63e2f57a119962ab7a0a was verified on main/practical refs. It contains accepted Action 0c4d0793 plus A8 read baseline 1e74c374. The two branches were divergent. `git merge --no-commit 69ca7574` auto-merged without textual conflicts; semantic integration was then completed before committing. No main/practical ref or checkout was modified.

## Source findings and integration

Action protects ordinary `_queue_handoff` with `session_claim`, loaded-pin validation and the existing final queue gate. It also decorates native admission and startup. The merged queue Steer transaction, including A8 batching, still wrote controls without that Action reservation. A conflict-free textual merge alone was insufficient.

Queue Steer now enters the SAME lifecycle coordinator's Session/Group reservation. It follows existing startup's spawn-lock then Session-claim order to avoid lock inversion. Loaded Action pins are rechecked after the wait; detached pins request delayed restart and reject admission before any control write. `AsyncExitStack` keeps the reservation across the actual write/drain/receipt owner, releasing spawn/queue locks before awaiting provider I/O. Existing await_persistence semantics prevent cancellation from releasing the claim ahead of that owner. Ordinary report handoff keeps Action's merged `_queue_handoff` unchanged.

Original per-report IDs, source/task/envelopes and one-row batch history/individual ledger receipts are preserved from c5a9140. Unknown still keeps all original members blocked; reload/migration and later Force do not regroup or replay it. Auto Force skips frozen batch members, and newly enqueued reports stay outside the in-flight unit. Child-agent report bodies remain absent from self_queue_read; non-report behavior remains unchanged.

The requested read-only item_id check exposed another difference in c5a9140: accepted receipt lookup called `_confirmed`, which could save Session state. It now reads the existing durable ledger off-loop and verifies all original members without saving or writing CLI. In-memory acceptance without durable proof returns unconfirmed; only the existing persistence owner repairs the failure. Explicit-ID queries neither reroute nor change membership. The persistence-failure test now explicitly simulates that owner's successful save before expecting accepted proof.

Repository tool description, Pan skill and HTTP reference preserve accepted Action guidance and document the shared reservation and fully read-only receipt lookup. User-level skill remains for MA synchronization.

## Concentrated verification

1. Report paths and actual Steer coexistence (exit 0):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_report_reads.py tests/test_report_action_coexistence.py tests/test_force_steer.py tests/test_cbc_steer.py -o addopts= -q --junitxml=artifacts/task-a-action-report-coexistence-junit.xml -o junit_family=legacy
```

**95 passed, 0 skipped, 0 failures/errors**, 5.01s console. Includes 42 report cases, five new Action/report coexistence cases, 17 Force cases and 31 CBC Steer fixture cases. CBC tests use fixture I/O, no paid provider.

2. Action lifecycle scope impacted by the merge (exit 0):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_action_groups.py -k 'admission or handoff or startup or unbuild' -o addopts= -q --junitxml=artifacts/task-a-action-admission-junit.xml -o junit_family=legacy
```

**19 passed, 44 deselected, 0 skipped, 0 failures/errors**, 1.82s console. Deselected cases were deliberately outside this integration validation scope. No full suite was run. Total this round: **114 passed, 0 skipped, 44 deselected**, with no overlapping test identities between the two commands. Earlier c5a9140's 243 passed/2 retired-oneshot skips are historical evidence, not rerun/combined current counts.

Five new coexistence cases exercise real Action unbuild HTTP transactions and real Worker batch transactions with provider fixture I/O: unbuild first fences a waiting actual Steer write; Steer first keeps admission through drain and makes later unbuild reject running use; request cancellation retains the server owner; ordinary report handoff uses the same Action reservation; Action-bound unknown admission survives cold reload and rejects Force replay. During drain, a new report remains separate. History preserves individual source identities, and explicit-ID queries are guarded against both `_save_body` and `_save_receipt`, also checking unchanged disk bytes and exactly one control write.

The existing real HTTP/MCP fixture was rerun in the 95-case command: owned dynamic loopback port **64374**, PID **38252**, temporary pytest stores, application lifespan disabled. Actual MCP SDK initialize/list_tools/call_tool goes through the real urllib HTTP wrapper; fixture stream routes admission through actual queue logic. It confirms removed report_next/report_ack, metadata-only read/consume responses and same-ID query with one control write. Server thread/socket closure and connection refusal are asserted. Action coexistence HTTP uses ASGITransport, separately from this socket protocol evidence.

Evidence: [coexistence JUnit](../../artifacts/task-a-action-report-coexistence-junit.xml), [coexistence output](../../artifacts/task-a-action-report-coexistence-tests.txt), [Action admission JUnit](../../artifacts/task-a-action-admission-junit.xml), [Action admission output](../../artifacts/task-a-action-admission-tests.txt), [new tests](../../tests/test_report_action_coexistence.py).

Changed Python compileall, manifest JSON parse, unmerged-path check and git diff --check passed. The first commit attempt triggered the required frontend pre-commit hook for imported Action files and failed only because this worktree lacked vite/client and node dependency types. package.json/lockfile match 69ca7574. The repository prepare_validation_env.ps1 safely created the owned node_modules junction to existing canonical dependencies, without installing or changing the canonical target; [environment log](../../artifacts/task-a-action-validation-env.txt). The final commit hook enforces npx tsc -b --noEmit with that environment. The targeted commands report only existing pytest-timeout configuration and Pydantic lifespan warnings.

## Scope and remaining acceptance boundary

The final net diff against 69ca7574 is A8 report consumption/privacy plus this shared-admission/query correction, tests, docs and evidence. Imported Action source, frontend and audit files are already part of 69ca7574, not new A8 work. This is a candidate containing the accepted baseline as an ancestor, enabling MA's fast-forward policy after independent acceptance.

No real provider/model, external production stdio/SSE deployment, physical adapter process restart, OS crash, frontend build/browser test or full suite was run. No frontend product delta exists against 69ca7574. Cold-restart evidence is persisted Session reload and actual queue migration, not a killed live provider. Existing c5a9140 limitations remain: full own history can hydrate during consume; metadata-only responses; unsupported native report parts are skipped; existing receipt retention may expire proof; locked/reordered frozen units are not split/unlocked. Unknown states require existing user investigation rather than automated replay.

No 8767/8768 access, existing-service restart, paid model, subagent, main/practical movement, push or release. MA must independently review/accept and sync user-level skill guidance. No other task was undertaken.
