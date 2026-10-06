# Rewind message identity and inline action

Baseline: bf95c2d4. Candidate branch: fix/rewind-ui-message-id-20261006.

- Only CBC exposes Rewind; unknown and unsupported adapters hide it.
- User action and timestamp share one horizontal row, action first.
- API accepts canonical durable pan UUID identities and retains msg_/legacy compatibility. Lookup is scoped to the target Session and rejects duplicate identities; legacy epoch checks remain intact.
- Canonical-ID regression uses synthetic Session data and a mocked hybrid rewind driver; no real provider rewind or user history mutation was performed.

Durable Job: job_8df9b0268d9de0e895d68c71. All four commands exited zero on the frozen source recorded in source_blobs.json.

| Check | Result |
|---|---|
| Nine rewind Python test files | 77 passed |
| Full frontend Vitest | 122 files, 1330 passed |
| Full frontend lint | 0 errors, 19 existing warnings |
| TypeScript + production build | Passed, 50 compressed assets verified |

Logs and JUnit reports are retained beside this report. Dependencies reused through a worktree-owned node_modules junction; deployed data/settings were not used by the test child. No service restart or main/practical integration was performed during validation. Backend restart will be required after deployment to activate canonical-ID API support. Browser visual acceptance and real CBC checkpoint execution remain unverified.
