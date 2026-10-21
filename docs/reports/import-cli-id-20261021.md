# Import CLI session ID search — candidate acceptance

Worktree: `D:/project/pan-worktrees/import-cli-id-20261021`  
Branch: `feature/import-cli-id-20261021`  
Baseline: `0a2e5f04d13917cdcf9ff8175f96e97e1411bfbf`

## Behavior

All four adapters currently exposed by ImportModal (CBC, Kimi, OpenCode, Codex) share the same search input and matching helper. Search accepts full or partial **native CLI session IDs**, trims leading/trailing whitespace, and matches without case sensitivity. Names and each adapter's existing path field also match. Empty or whitespace-only search restores the loaded list. Switching adapters unmounts the section and resets search. Filtering retains the original item object; row keys and import arguments remain the original native ID and source project/workDir. There is no new preview or import protocol and no background polling.

## Discovery boundary

This is explicitly a **loaded-list search**, not global native-history search. Search typing does not fetch or broaden discovery.

- CBC: `/api/cbc/projects` uses configured `import_recent_days` (default 30) and `min_resume_bytes` (default 200). `/api/cbc/sessions?project_dir=…` then applies existing exclusion/min-message rules and `max_sessions_shown` (default 30). The search UI explicitly states configured import filters and list limits still apply. The existing `all=1` bypass is intentionally not enabled: doing so would change the existing discovery/filter contract.
- Kimi: `/api/kimi/workspaces` then `/api/kimi/sessions?cwd=…`; only valid indexed resumable entries in that workspace are returned, without pagination in this path.
- OpenCode: `/api/opencode/sessions?cwd=…`; SQLite-native session IDs and optional directory equality filter; no pagination in this path. Clear directory and Load to broaden discovery.
- Codex: `/api/adapters/codex/sessions?cwd=…` delegates to the provider reading `state_5.sqlite`, selecting non-archived threads and preserving existing cwd matching. No list pagination. Clear directory to broaden discovery (existing directory refresh/Load behavior retained).
- Claude has a provider but is not a currently supported ImportModal tab; this change does not add a new import adapter.

## Validation

No dependency installation or shared dependency mutation. `tools/prepare_validation_env.ps1 -Worktree <worktree> -Check` then Prepare verified the canonical Python environment and created the owned frontend node_modules junction.

Commands (frontend commands run from `packages/web`):

- Before test enhancement, `pnpm exec vitest run src/components/session/ImportModal.test.tsx`: 14 passed.
- After enhancement, same command: 14 passed, including all four adapters' trimmed full ID, uppercase partial ID, title/path, no match, whitespace reset, and original native ID/path import payload assertions.
- `pnpm exec tsc -b`: exit 0.
- `pnpm exec eslint src/components/session/ImportModal.tsx src/components/session/ImportModal.test.tsx e2e/import-cli-id.e2e.mjs`: exit 0; one pre-existing `react-hooks/exhaustive-deps` warning for `codexRequest.current` cleanup (present at baseline).
- `pnpm build`: exit 0; 74 compressed production assets verified. Existing dynamic/static router import and >500 kB chunk warnings remain.
- `D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_session_import.py tests/test_cbc_import_guard.py -o addopts= -q`: 34 passed; one existing Pydantic incomplete-definition warning.
- Same isolated entry with `tests/test_codex_adapter.py -k sessions_provider_e2e -o addopts= -q`: 1 passed, 71 deselected.
- `node e2e/import-cli-id.e2e.mjs`: real headless Chromium, production ImportModal and CSS, synthetic intercepted API metadata. Covers all four adapters with two distinct native sessions: padded full ID and uppercase partial ID select only target, clearing restores both, unrelated Pan ID gives no match, existing path matches, switching resets search. Zero page errors, 8 intercepted GET requests, no POST/import calls. `finally` closes Chromium and owned Vite server. Early harness runs failed on its entry route/strict label locator; the corrected harness passed.

Browser evidence is generated under `packages/web/test-results/import-cli-id/`: `result.json`, and `{cbc,kimi,opencode,codex}-id-match.png`. Result records owned port and PID. Screenshots were visually inspected (Codex: full styled modal, target row and scope visible). This proves browser behavior with fixtures, not real native discovery, real imports, or production service integration. The import payload assertions are mocked unit tests only.

No real user Session import/rewrite, credentials inspection, paid model call, full suite, shared service request, main/practical movement, push, release or service restart. MA independent acceptance and any later fast-forward remain outstanding.
