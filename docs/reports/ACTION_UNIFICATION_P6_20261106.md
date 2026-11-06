# ACTION-UNIFICATION-20261104-P6 revision 4 candidate

Later slices and the current remaining scope are recorded in
[P6 follow-up](ACTION_UNIFICATION_P6_FOLLOWUP_20261106.md). The matrix below is
the retained 267287c candidate boundary, not a replacement for the follow-up.

Base: `a298b32ea3299fdd9eae6964f79d6989ccd452cb`.
Worktree: `D:/project/pan-worktrees/action-unification-p6-20261106`.
Branch: `implement/action-unification-p6-20261106`.

This candidate closes the named synthetic old-Group → adoption → upgrade →
standalone stdio path. It does **not** authorize real-profile migration or claim
that arbitrary historical registries have a known writer. Unknown writers,
abandoned leases, complex records and incomplete snapshot mappings remain blocked.
No legacy capability or history host is deleted.

## Authority and identity

`group_migration.py` reuses `OwnerFence` for the source native lock, durable legacy
lease and epoch CAS. `Store.mutate` participates, including generic mutations,
put/delete/build/unbuild. There is no new executor, scheduler or effect ledger.
`enroll_fresh_source` requires a directory that does not exist. It establishes a
cooperative source **before** creating any registry. It cannot enroll an existing
registry, attest an unknown old binary, clear a lease, or accept a caller-provided
`offline/stopped/PID` assertion. The named test creates this real cooperative
source, writes/builds an old-format JSON Group, and calls its original provider.

Adoption requires explicit per-draft/per-historical-snapshot mappings:
`(groupId, version, actionId, definitionDigest) → fixed ActionRef`. Definitions,
enabled states, entire-scalar argv bindings, defaults, effect, idempotency and
capabilities are checked. An alias or equal ID alone is insufficient. Reserved
descriptor business fields, complex schemas, protected/API/session/template
Actions, extra release files, damaged pointers and unreferenced Actions block
this source-wide handoff. Disabled definitions stay disabled; unknown data is
not normalized or rewritten.

The source freeze precedes the authority commit. It fences future cooperative
mutations even if their process remains alive. This is cooperative writer
revocation, **not** a claim that an arbitrary old process/tree was terminated.
The original requestKey, principal, profile and reviewed plan digest are retained
in the existing source owner metadata. A changed epoch without that exact intent
is rejected. A frozen-source status reports blocked with no invented operationId
until the canonical transaction commits. Abandoned leases
block claim and are never reset.

One canonical transaction writes the Group heads, every original release mapping,
`action_group_handoff` authority generation, legacy projection revision and the
original `final_operations` adoption receipt. The canonical owner epoch and source
epoch are recorded. Revalidation and permission checks occur inside that
transaction. A pre-commit crash leaves a frozen, blocked source; explicit resume
requires the same reviewed plan. An after-commit response loss queries/replays
the original receipt without importing again. There is no cross-file atomicity
claim and no source JSON/file replacement during adoption.

Imported releases retain their exact original version, plugin ID, tool aliases,
capability/snapshot files and directory. Old loaded pins continue using the
original immutable provider release. Explicit future publication writes a new
release with fixed ActionRefs, retaining the versioned `legacy-flat/v1` adapter.

## Compatibility matrix

| Old entry / record | Successor / behavior | Evidence / remaining boundary |
| --- | --- | --- |
| `Store.mutate/put/delete/build/unbuild` | Enrolled source native lock + lease; after freeze no JSON mutation | P6-02; real competing process; abandoned lease and generation conflict |
| Old HTTP Group PUT and draft PUT/DELETE | `LegacyGroupCompatibility` → existing `Groups` owner-fenced operations | P6-08 ASGI; old `action_ids`, names and global registry revision shape retained |
| Old Group build | Same canonical publish; existing coordinator and delayed restart | P6-08; stale old registry CAS returns the old HTTP 400 |
| Old Group unbuild/delete | Same canonical unbuild/delete; shared existing Session detach/rollback helper | P6-08 plus original Group suite; immutable original files remain |
| Catalog / standalone / native / gateway discovery | Adopted JSON row excluded; canonical release supplies one plugin authority | P6-05 standalone; canonical P3 tests cover all three modes; adopted flat native/gateway have no new end-to-end evidence |
| Flat MCP arguments | Business object remains flat; schema stays exactly the old schema | P6-05 actual standalone stdio before and after adoption/upgrade |
| Business `requestKey` | Passed as business data; transport identity lives in MCP `_meta.panRequestKey` | P6-05; defaults, quotes, backslash, newline, Chinese, changed-payload conflict |
| Old client without outer key | Stdio process assigns one connection/request identity; reply `_meta.panOperation` exposes original key/operation | Same canonical operation; a new call is a new intent; no automatic retry |
| Canonical MCP envelope | Existing `{arguments, requestKey}` unchanged | Original P3/P3-review real stdio tests |
| JSON/text/nonzero/invalid JSON output | Project from the one canonical command receipt; JSON structured content and old text/error shape retained | P6-06; 512 KiB/1 MiB limits remain explicit |
| Unknown / response loss / revocation | Original operation metadata; same key returns/query-only uncertain receipt; new invoke denied after revocation | P6-05; one counted effect, no hidden Job |
| Original loaded Worker pin | Original provider object/version; publication changes future pin only | P6-05 actual stdio, byte comparison and runtime pin capture; no real model CLI Worker claimed |
| Existing query/cancel/notification/history | Existing canonical/legacy owners and endpoints retained; no new notifications, history rewrite or cancel engine | Original suites retained; no new proof of every historical Job/Scheduler/channel contract |
| Adding an unresolved old Action ID to an adopted Group | Explicitly blocked; canonical edit requires a reviewed fixed ActionRef | No alias guessing; this old edit capability is **not** declared fully covered |
| Legacy Action CRUD/lifecycle after the source-wide freeze | Old source is immutable; canonical Action APIs remain available | No general legacy Action-write adapter claimed; unreferenced source Actions block handoff |
| Unknown old writer / arbitrary preexisting registry | Preview reports unknown writer; apply refuses; fresh enrollment refuses existing directory | P6-01 negative gate; no real migration claim |
| API GET, system/session/template/QQ, secret-bearing/complex/unknown record | Preserve/blocked, no guessed runnable conversion | P3 red/blocked evidence retained; not retirement or complete parity |

The Windows legacy provider now explicitly uses UTF-8 JSON lines. The old locale
encoding could silently convert `雪` into another valid character. The original
failure is preserved byte-for-byte in `utf8-red.xml.gz`; assertions were not relaxed.

## Offline commands

The CLI requires existing, distinct explicit roots and a preexisting canonical
profile. No service, background engine or command effect is started. Dry-run is
the default; the output plan must be outside protected roots and distinct from
the input mapping. Plan contents may include source names/paths/schema and must
be reviewed locally, not committed from a real profile.

```powershell
python scripts/migrate_action_groups.py --source-root <source> --canonical-root <canonical> --authorization-root <auth> --mappings <reviewed-mappings.json> --output <plan.json>
python scripts/migrate_action_groups.py --source-root <source> --canonical-root <canonical> --authorization-root <auth> --apply --plan <plan.json> --request-key <original-key> --expected-owner-epoch <reviewed-epoch>
python scripts/migrate_action_groups.py --source-root <source> --canonical-root <canonical> --authorization-root <auth> --status-key <original-key>
```

Dry-run needs no owner claim and has a byte-preservation test. Apply uses the
existing native canonical owner claim and CAS, and requires that the profile has
no current canonical runtime owner. The same OS user is the offline administration
boundary; the CLI reuses the existing UserAuth permission reader without logging
in, reading a credential or pruning login sessions.

## Named gates and evidence

| Gate | Result |
| --- | --- |
| P6-GROUP-01-whitelist-export | Enrolled ordinary command whitelist, explicit roots, dry-run bytes, unknown source exclusion and offline CLI tested |
| P6-GROUP-02-owner-writer-handoff | Native competing writer, durable unknown lease, generation conflict, raw generic writer fence tested; arbitrary existing-writer adoption remains blocked |
| P6-GROUP-03-cutover-crash | Exception injection plus real child `os._exit` at source-frozen/before-commit/after-commit; cold offline reopen and original receipt tested |
| P6-GROUP-04-release-pin-preservation | Folded into P6-05: original version/files/schema/pin remain, explicit v2 publication and future pin differ |
| P6-GROUP-05-old-to-upgraded-stdio | Successful real standalone old flat call → source adoption → specified Action v2 reference apply → publish → old and new pinned stdio calls |
| P6-GROUP-06-compatibility-identity | Business/transport namespace, default/escaped/UTF-8 data, JSON/text/errors, unknown/no-replay and revocation tested |
| P6-GROUP-07-all-scope-atomic | Existing P3 atomic/CAS/retired/schema/permission/storage tests retained; remaining unresolved JSON Groups still block unqualified all scope |
| P6-GROUP-08-legacy-entry-successors | Existing adopted Group edit/build/unbuild/delete/catalog paths tested; unresolved additions, general legacy Action writes and live browser acceptance remain open |

Evidence is in `evidence/action-unification-p6/`. All pytest commands use
`python scripts/run_tests_isolated.py --`. The final verification results and
commands are recorded in `validation.json`.

Final frozen-source combination: **175 passed, 0 failed/error/skipped**, including
21 P6 cases and the original P1/P2/P3/P3-review/P4/P34/Group/ID/persistence tests.
Plugin platform/configuration/authorization combination: **42 passed,
0 failed/error/skipped**. The changed source hashes were unchanged between the
start and completion of these runs. Server import and `git diff --check` passed.
The normal repository pre-commit hook runs during the candidate commit; its
working copy was normalized to the repository's LF bytes without changing its
Git content or disabling any checks.

Original failing evidence is retained as `initial.xml.gz`, `utf8-red.xml.gz`,
`combined-red.xml` and `compatibility-proxy-red.xml`. The latter has four loopback
HTTP/SSE failures under the inherited proxy environment. A comparison loading the
HEAD version of the changed runtime module reproduces the same four failures
(`plugin-baseline.xml`); this is a module-level comparison, not an entire clean
baseline checkout. Explicit process-local `NO_PROXY=127.0.0.1,localhost,::1`
passes the plugin suite (`plugin-localhost.xml` and `plugin-auth.xml`). No user
proxy configuration was changed.

The two gzip artifacts retain the original JUnit bytes, including whitespace in
failure traces; `validation.json` records their uncompressed SHA-256 hashes.

## Platform and delivery limits

| Capability / layer | Windows | Linux/macOS |
| --- | --- | --- |
| Source native writer exclusion | Existing `job.runtimeOwner` + RuntimeLock, exercised with real local processes | Same platform primitive reused; not run here |
| Epoch/lease/CAS/cold recovery | Existing OwnerFence and canonical transaction, exercised locally | Target-platform evidence pending |
| Command ownership/output | Existing `processOwner` / P2 executor; UTF-8 provider pipe regression tested | No new target-platform execution claim |
| GUI | Existing HTTP-facing Group flow adapted; React unchanged | No production browser acceptance in this candidate |

No new capability is advertised and no platform implementation is added.
No frontend source changes require a React build. Full tests/three-platform CI,
production HTTP/Chromium, real-provider operations, real data migration,
deployment, merge and human acceptance are not claimed. This candidate is
reviewable for the enrolled command subset; overall P6 parity remains blocked
by the uncovered rows above. Main/practical, real data/profile and 8767/8768 are
outside this work; nothing is pushed.
