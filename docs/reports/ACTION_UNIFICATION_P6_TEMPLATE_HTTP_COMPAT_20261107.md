# P6 first remaining slice — legacy Template HTTP/MCP handoff

This candidate starts from `6d997d689a50d57d0c16c6eb63b34c2bfad2f803` and handles only the first remaining P6 group.

Before this slice, the authenticated old `POST /api/jobs/job-templates` writer was a retirement boundary returning 410, and the MCP `job_config_drafts` adapter did not expose old delete/instantiate transfer. The original gap is recorded in `evidence/action-unification-p6/template-http-compat-red.txt`.

The old HTTP shape now goes through the same `FinalService` owner fence, SQLite transaction, operation receipt and canonical Template/Job composition:

- old GET/POST/PUT/DELETE `/api/jobs/job-templates` and `/api/legacy/jobs/job-templates` keep the old `item`, `expectedRevision`, `idempotencyKey`, `overrides`, `templates` and `deleted` shapes;
- legacy IDs remain `jtpl_*`; source `referenceGeneration` and old revision are retained in `legacy_template_alias`; edits after a published canonical Template allocate a reviewed canonical successor while preserving the old alias and incrementing the old revision;
- instantiate accepts display overrides, translates them into the created canonical Job definition, and reuses the original request key for replay/cold lookup;
- Event Template `creatorSelector` and scalar query fields are mapped to canonical Creator reference specs and Trigger `creatorSelector`/`eventFilter`; unknown selector modes and missing Creator identities fail before canonical persistence;
- MCP `job_config_drafts` keeps its legacy operation envelope and now forwards template delete and instantiate (including display overrides) to the old HTTP adapter. A real stdio MCP run also queried the old template list envelope.

Focused evidence:

- `template-http-compat-final.xml`: 6 focused tests passed, covering HTTP CRUD, published successor/revision alias, display override, cold request-key query, Event Template selector/query mapping, unknown preserve/blocked, and MCP delete/instantiate forwarding.
- `template-mcp-stdio-final.xml`: 2 real stdio MCP canonical client runs passed; the test additionally queried `job_config_drafts(operation=list, kind=template)` through the owned HTTP bridge.

The result is source, focused unit/HTTP boundary, and owned stdio MCP evidence. No production build, Chromium, remote CI, real data migration, deployment, real CLI/QQ, or human acceptance was run in this slice. The other P6 remaining groups (background-process/main-lifecycle, full old Scheduler/Trigger cancellation/notification/history, advanced old UI parity, and external real-provider acceptance) remain open.

No push, `main`/`practical` advance, real profile/data access, or 8767/8768 operation occurred.
