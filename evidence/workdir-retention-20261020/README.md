# Workdir retention validation

- Checkout: `D:/project/pan-worktrees/workdir-retention-20261020`
- Branch: `feature/workdir-retention-20261020`; baseline `ccc1ca5b65c4c896dd16934a132a6a169c7b16ce`.
- Implementation: independent default-root orphan workdir scanner; recursive latest mtime; all Session/Workspace overlapping references protected; independent configuration; existing retention runner; no Session-expiry workdir deletion.
- Concurrency: process-local reference lock covers Session workdir assignment, Session persistence, Workspace dirs assignment/persistence and collection. A write that wins protects its directory; a write that loses rejects a removed path. Windows GENERIC_READ directory handles without share-delete prevent directory replacement; POSIX uses no-follow directory descriptors. Preview has no deletion authority. Partial failures are reported individually.
- Limits: 2,000 root entries; 100,000 traversal entries across a request; 2,048 directories per tree; 20-second traversal/deletion deadline; reference stores capped at 10,000 entries, 8 MiB per record and 32 MiB total. Safety failures retain data and expose errors/skip reasons.

## Tests

`D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_workdir_retention.py tests/test_data_retention.py tests/test_data_catalog.py tests/test_workspaces_backend.py tests/test_eventloop_history_offload.py -q --tb=short --junitxml=evidence/workdir-retention-20261020/pytest.xml`

Result: **93 passed, 0 failed, 0 skipped**, exit 0. JUnit: `pytest.xml`. All generated Sessions, workdirs, Workspace records, links and delete targets use pytest temporary directories. API handlers are called in-process; the background-thread test verifies they do not scan on the event loop.

`npx vitest run src/components/layout/WorkdirCleanup.test.tsx src/components/layout/AppSettingsModal.test.tsx --reporter=default --reporter=junit --outputFile.junit=../../../evidence/workdir-retention-20261020/vitest.xml`

Result: **60 passed**, exit 0. JUnit: `vitest.xml`. Covers warning/disabled browsing, candidate fields, select all, selected-name deletion, individual failure, successful deletion, empty state, refresh/retry, automatic default-off and settings persistence. Existing Data/Jobs settings regressions pass.

`npx tsc -b --pretty false`: exit 0. `npm run build` (includes `tsc -b`, Vite and compressed asset verification): exit 0, **72 compressed assets verified**. Log: `build.log`. Existing bundle-size and mixed static/dynamic router import warnings remain.

Targeted ESLint for all changed frontend source/test files: exit 0 (`lint.log`). Full `npm run lint`: exit 1, 140 errors and 19 warnings (`lint-full.log`); every diagnostic is in an unchanged file (checked against the task diff). Existing E2E globals/unused-variable errors and unrelated source warnings remain outside this task. `git diff --check`: exit 0.

## Environment and boundaries

The prepared canonical dependency junction lacked `@xterm/xterm` and `@xterm/addon-fit`. Validation used a temporary isolated node_modules overlay linking canonical dependencies and the already installed practical xterm packages. No package installation, lockfile change, or canonical/practical dependency mutation occurred. The worktree junction is restored to its prepared canonical target after validation.

No request was made to 8767/8768. No live service/browser instance was started; no real workdir was deleted. Browser/mobile rendering, HTTP transport integration, POSIX runtime behavior and multiple processes independently writing the same metadata store remain unverified. Reference serialization covers one Pan process; direct external metadata writers are outside that lock. Every validation command completed within three minutes; no full 4,000-test run or new polling loop was used.

Only this isolated branch is committed. MA review and main/practical fast-forward remain pending; no push, merge, release or service restart.
