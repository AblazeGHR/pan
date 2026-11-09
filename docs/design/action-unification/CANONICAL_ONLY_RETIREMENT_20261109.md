# Canonical-only execution and English Jobs UI

User approval recorded 2026-11-09. Base: `4a62760baefbffbd4eeb2c4e1bfa8d83187c446a`.
Read-only UI reference: practical `0ed770555493c820355c4f906c2b067425b2cc98`.

This decision supersedes requirements for complete conversion of old Job,
Action and Group formats. Preserved legacy records may be Invalid/Retired and
must not execute. Preserve source files and identities; do not invent migration
identities. No separate legacy management page. New creation and execution use
the existing canonical FinalService, owner fence, permissions, CAS and receipts.
Unknown outcomes remain query-only and must never replay automatically.

## Retirement matrix

| Surface | Required final behavior | Retained boundary |
| --- | --- | --- |
| Legacy Job HTTP/MCP producers | Authenticated retirement or existing canonical adapter; no old runner | Read history, safe cancel and owned cleanup |
| Legacy Action HTTP/drafts | No Store.put fallback; new definitions use canonical | Source JSON preserved; invalid rows remain visible |
| Legacy Action provider/executor | Refuse legacy execution before effects | Canonical typed executor and invocation receipts |
| Legacy Group build/plugin | No old release writer or executable catalog registration | Canonical group releases/pins; safe unbuild cleanup |
| Action Store | No new legacy definitions/releases | Read, explicit safety cleanup; preserve mixed hosts |
| Jobs UI | `/jobs` is the sole primary entry; practical design is the base | Canonical definitions, versions, references, runs, maintenance, templates |
| Old Jobs aliases | Redirect to `/jobs` | No Canonical Jobs product links |
| Action/Group/Jobs/Automation copy | English labels, buttons, errors, hints and menus | User data, definitions and logs are never translated |

## Validation contract

Named tests must prove no legacy writer/executor fallback, canonical creation and
execution, nonexecuting invalid history, auth before retired responses, route
redirects and English product copy. Run related regressions, production build,
and owned isolated HTTP/production Chromium checks including all four kinds and
Action/Group creation and invocation. Keep red evidence and resolve in-scope
failures. Normal hooks, candidate commit only; no push, protected ref changes,
real data/profile or ports 8767/8768. Interaction candidate dbe03097 is excluded.

Inventory and verified results are appended as implementation proceeds; this
matrix records approved scope and is not a completion claim.
