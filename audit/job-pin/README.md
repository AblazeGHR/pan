# Job Pin TA evidence

Baseline: 6919bf98c6adf08fbc7fd78c77ec714e6628a085. Branch: feature/job-pin-20261011.

JobsView menu Pin/Unpin and badge. Pin partition precedes original active/status + updatedAt descending comparator. No lifecycle timestamp changes. Non-pinned ordering, filters, actions and NewJobForm preserved.

PUT /api/jobs/{id}/pin accepts only strict boolean pinned. Pan owns pins.json under scheduler store.data_root() (unified Job registry), reusing background_jobs._atomic_write and cross-process registry mutex. Five local kinds, scheduled task aliases and independent Job service projections supported without changing execution records. No localStorage. Read failures surface rather than clearing existing metadata. Pins do not exempt Jobs from retention; deleted IDs remain invisible even if metadata remains. Unpin removes its key.

Actual checks and raw evidence:
- backend.txt / backend.junit.xml: 117 passed, no skips/failures. Five-kind disk reread, independent owner, validation, corrupt metadata, failure/no broadcast, multi-pin/filter; ordinary message/background Job/bridge regressions.
- frontend.txt: 45 passed. Legacy schedules, existing actions, WS lifecycle pin preservation and explicit unpin restoring ordering.
- build.txt: TypeScript/build passed, 62 compressed assets verified; existing bundle warning.
- chromium.json / chromium.txt / desktop.png / mobile.png: real isolated FastAPI router and registry with headless Chromium desktop 1280x900 and touch mobile 390x844. Menu pin/unpin, multi-pin order, status/kind/search filters, refresh, backend process restart, injected write failure and reread, unpin restoring order. Zero page errors / 8767 or 8768 requests. Dynamic ports, temporary data/profile, cleanup in finally.

Limits: JobsView MemoryRouter harness, not production shell. Lifecycle WS preservation component-tested. Independent owner bridge-fixtured, not deployed independent service. No native phone or production service restart. No push, merge or main/practical advancement.

Dependency hashes match before/after shared source: package.json C719902161F24170FAB65116F7BCEDC77E8499A316B5366CDFFCCC0C8946FFC3; pnpm-lock.yaml 249D2FC3A1A0FD511EF440B4F51224F946F1F58DD850060B0BAE7D78A164B149. No installs or dependency manifest changes. First failed browser attempt used default Vite optimization cache; final runner uses temporary cacheDir. Initial test harness search role mismatch corrected without weakening assertions. Test typing corrected after first build failure; final build passed.

Shared paths: packages/jobs/api.py (view/list projection + standalone endpoint), packages/web/src/services/api.ts (pinJob append), packages/web/src/types/jobs.ts (optional pinned field). server.py untouched.


Compatibility repair: see [repair/README.md](repair/README.md). The original implementation allowed pin read failures to fail Job responses; the repair exposes unknown presentation state while preserving original operations. Original evidence above is retained unchanged.
