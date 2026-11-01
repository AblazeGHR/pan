# WP07 canonical React candidate

- Baseline: `be3e06720e1092f01f3dcbe32ba4c126fc4710ad`.
- Branch: `r1/wp07-ui-20261025`; candidate is a descendant for fast-forward integration. No target branch advanced.
- Entry: Automation → Canonical Jobs → `/automation/canonical` (production `/react/automation/canonical`).
- Four independent canonical collections: Jobs, Triggers, EventCreators, JobTemplates. List/pagination, name + definition JSON create/edit, revision-checked retirement confirmation.
- Per-Job paginated Run list, retry/run-again/clone-and-run/cancel/pause/resume with explicit confirmation. Run-again/clone read current Job config revision; controls use the listed Run state revision.
- Shared API JSON/auth handling, dedicated `/api/final/*` service/types. No legacy transport fallback. WP01's 18 error codes have readable messages, including 422 unsupported and 403.
- Operation receipts expose original identities. Accepted/pending/unknown are not effect success. Unconfirmed writes remain blocked until querying the original request; no automatic write replay.
- Legacy `/jobs`, old Jobs implementation and three Automation subpages retained. Automation header only gains an additive link.
- Definitions are an advanced JSON editor, not a guided graph builder. Users must supply valid required fields and versioned references (id/revision/digest); the Action catalog displays published records. Schema constraints are available in the editor.

Validation (Windows, exit 0)

| Layer | Evidence | Result |
| --- | --- | --- |
| Component/service + legacy regression | `vitest.xml`, `vitest.log` | 8 files, 131 passed, 0 failed, 0 skipped; 40 new tests |
| TypeScript | `tsc.log`, `pnpm exec tsc -b` | passed |
| Production | `build.log`, `pnpm build` | passed; 92 compressed assets byte-verified (new lazy page adds 2 variants over 90) |
| Scoped lint | eslint on the 5 new TS/TSX files and layout script | passed |
| Chromium mocked layout | `browser.json`, `browser.log`, four PNGs | 390/1440 × list/editor with expanded schema/run operation = 6 checks, no horizontal overflow, no page errors |
| Whitespace | `git diff --check` | passed |

The browser fixture builds the real React component with the normal CSS and intercepts all requests with Playwright route.fulfill. It has no HTTP listener or Pan process. Run with `node packages/web/e2e/canonical-jobs-layout.mjs`. Its generated bundle is ignored under `fixture/`.

Tests: `pnpm --dir packages/web exec vitest run src/views/CanonicalJobsView.test.tsx src/services/canonicalJobs.test.ts src/services/api.test.ts src/services/api.legacyJobs.test.ts src/views/JobsView.bulk-search.test.tsx src/views/JobsView.schedule-shapes.test.tsx src/views/AutomationWorkspace.test.tsx src/views/AutomationView.test.tsx`.

Source files changed

- `packages/web/src/router.tsx`
- `packages/web/src/services/api.ts`
- `packages/web/src/services/canonicalJobs.ts`
- `packages/web/src/services/canonicalJobs.test.ts`
- `packages/web/src/types/canonicalJobs.ts`
- `packages/web/src/views/AutomationView.tsx`
- `packages/web/src/views/CanonicalJobsView.tsx`
- `packages/web/src/views/CanonicalJobsView.test.tsx`
- `packages/web/e2e/canonical-jobs-layout.mjs`

Not verified: live canonical HTTP/Bearer/cookie integration, real backend execution/effects, real data migration, full application browser routing, WebSocket persistence, Linux/macOS checks, remote CI, deployment, human acceptance. No server.py/MCP/CLI changes, service start/stop, 8767/8768 requests, push, or protected-ref advancement.

Build warnings remain for static/dynamic imports of automationStore/router and the existing large markdown vendor chunk. Initial test-fixture errors (header casing, reusing Response, CSS filename/duplicate heading selector) were corrected. The layout probe found a real long-name button overflow; local wrapping and compact action labels fixed it before the final checks.
