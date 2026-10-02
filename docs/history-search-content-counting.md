# History search: content counting and selectable partitions

## Scope and completion boundary

This increment implements the backend contract, identity extension, bounded
query cache, and a reproducible benchmark. It does **not** switch the frontend
to occurrence navigation or add frontend role controls. The existing lazy,
default-off frontend remains unchanged. That UI migration and HTTP/Chromium
acceptance are the next increment, not completed work.

The user now requires occurrence counts and user/assistant/tool/thinking
coverage. The previous one-message-one-hit product rule is superseded.
Canonical Session JSON/JSONL remains authoritative. Old rows without real Pan
IDs may remain unsearchable; no bulk rewrite is performed. Errors, system/debug
metadata and source=system_prompt are still excluded.

## API

`GET /api/history/search?q=...&sessionId=...&roles=user,assistant,tool,thinking&countMode=content`

Omit sessionId for global search. Existing clients retain body-only,
message-reference response behavior when they omit the new parameters.
Roles are a canonicalized comma-separated subset; explicit `roles=` searches
nothing and opens no database. Unknown roles return 422.

Content mode counts non-overlapping, casefolded literal occurrences. It does
not interpret regex, Boolean operators, or punctuation as query syntax. This
includes full-history single-character queries. Three or more characters use
trigram candidates followed by literal verification; short queries and those
containing NUL use the selected-role literal scan.

The response adds:

- `totalMatches`: exact occurrence count across the complete selected scope.
- `totalMessages`: number of matching logical blocks, not occurrence count.
- `roles`: canonicalized selected roles.
- Each hit adds `matchCount`, `matchStart` (zero-based cumulative occurrence
  ordinal), and `firstMatch` (Python character offset in original content).

One reference can represent many occurrences; no unbounded occurrence array
is transferred. Pages contain up to 100 message references, not 100 occurrences.
`matchIndex=<zero-based ordinal>` selects the block containing that occurrence
without transferring every prior hit or the complete history. It does not yet
return a precise position for every occurrence. Frontend occurrence navigation
and text highlighting must account for this distinction.

Signed cursor v2 binds query, scope, page size, role selection, counting mode,
ordered Sessions and history versions. Changing roles or mode requires a new
search. Old cursors expire during disposable schema migration. Concurrent
history mutations keep the existing 409/re-search contract.

## Storage, filtering and unloading

New append/import/fork text blocks in all four roles receive Pan IDs. Native
IDs remain import/reimport matching evidence only. No search dependency is
introduced in append/save. Old persisted history is not migrated on load.

Body, tool and thinking have separate FTS partitions with role-filtered
external-content views. A partition is populated only when requested; a
search only reads selected partitions. Keeping the content view partitioned
also keeps FTS integrity-check/rebuild from adding excluded roles.

Unselecting a role stops candidate matching and result transport for that
partition; previously derived disk rows may remain for reuse. This is **not**
a promise of immediate physical disk deletion. Version changes still require
canonical prefix validation, including excluded roles, before suffix indexing.
Thus role filtering reduces matching work but does not eliminate integrity
validation I/O after history changes.

Connections close at request completion. There is no resident search worker,
background task, append/save hook, or forced startup indexing. Frontend module
unmounting and request cancellation remain requirements for the next UI stage.

Query references bind literal text, roles and ordered canonical versions.
They store row IDs, counts and ordinals, not copied content. At most 16 queries
and 100,000 references are retained. Oversized queries still return complete
counts/pages using a request-local SQLite window, without unlimited retention.
Metadata and page references are read in one transaction so concurrent eviction
cannot silently erase a valid page. Index replacement/append invalidates caches.
Concurrent partition enabling preserves the union of previously enabled roles.

## Comparison and acceptance

PR #3 baseline is pinned at `bf811a7cc2f0271023b459729b7fb0079591ebaf`.
The benchmark extracts its actual `_search_session_history` function; it stubs
only Session existence and public-row projection. It validates occurrence
totals and first-page message indexes against our results. It does not execute
the PR frontend, HTTP stack, production registry or provider.

Reproduce from the repository root:

```powershell
py -3.14 scripts/benchmark_history_search_content.py --compare-pr --rows 10000
```

All fixtures live in TemporaryDirectory and are removed afterwards. JSONL
is hash-checked before deliberate append scenarios. Output includes environment,
source hash, raw timing samples, counts, disk sizes and cleanup confirmation.

Initial two runs on Windows/Python 3.14.5/SQLite 3.50.4 with 10k mixed-role
blocks show a meaningful cached-query advantage, **not universal superiority**:

| Scenario | Our backend | PR original helper |
| --- | ---: | ---: |
| Sparse cached query | 1.7–1.8 ms | 15–16 ms |
| Dense cached query, 20k occurrences | 1.9–2.6 ms | 18–20 ms |
| Cached single-character query | 2.5–2.9 ms | 16–22 ms |
| Cached punctuation query | 3.0–5.9 ms | 29–30 ms |
| New index + sparse query | 228–230 ms | 15–16 ms |
| First dense query on existing index | 87–90 ms | 18–20 ms |
| Append then first query | 123–151 ms | about 15–16 ms |

Runs have I/O/scheduling variability; they do not establish online latency.
100 dense pages take about 0.47–0.51 seconds at the core layer. SQLite is about
15.3 MB against a 3.0 MB JSONL fixture; derived indexes/cache cost disk space.
Role-filtered first dense matching drops to about 49–51 ms with half the roles;
the smaller matching workload is genuine, not just hidden result rendering.

Remaining priorities are cold-start/first-match cache cost, append-prefix proof
cost, unified backend-driven Session/global UI, occurrence navigation and
highlighting, selectable-role cancellation/unmount coverage, and new real
HTTP/Chromium evidence. Feature parity is an acceptance matrix, not a claim
that every PR behavior should be copied (for example searching system errors).

## Verification boundaries

Focused tests cover counting, large repeated blocks, ordinal seeking,
casefold expansions/original offsets, unusual literal text, role partitions,
empty-filter no-I/O, warm pagination without repeated matching, bounded and
oversized caches, concurrency/eviction, disposable schema migration and FTS
integrity/rebuild, API validation/cursor role binding, identities and existing
Session/Worker/import/stream regressions.

This backend-only stage does not itself constitute frontend build, real HTTP,
browser E2E, provider, production-concurrency or deployment acceptance.
