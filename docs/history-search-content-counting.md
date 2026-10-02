# History search: content counting and selectable partitions

## Current increment: legacy IDs and shared sidebar (2026-10-02)

- Session UI explicitly requests `prepareLegacy=true` on initial content
  searches. Missing Pan IDs are persisted for user/assistant/tool/thinking
  without adapter reimport. Existing IDs, content, order, sources and plugin
  metadata are retained. Empty queries/role selections do not prepare.
- Global UI first presents existing-ID matches, then consumes one POST
  `/api/history/search/prepare` NDJSON stream. It checks/prepares Sessions
  sequentially, publishes bounded first-page previews and progress, and only
  enables normal signed pagination after the final complete snapshot. Counts
  during preparation are provisional. Failed Sessions are explicitly reported
  as incomplete with retry; no partial result is labelled a complete search.
- Closing/changing query/unmounting aborts the stream and stops subsequent
  Sessions. A started filesystem operation finishes safely. There is no
  resident migration job, polling loop or adapter import. Ordinary search API
  calls without preparation remain read-only; API clients must opt in.
- Repair uses the existing per-Session persistence tickets and summary lock.
  Loaded histories are written to a same-directory temporary file outside the
  short commit lock; concurrent suffix appends are included, prefix changes
  cause retry. Cold JSONL is streamed without hydrating shared history; corrupt
  lines are retained. Files are flushed/fsynced before atomic replacement.
  History and metadata are separate files: a crash between their replacements
  leaves canonical history intact; same-process metadata failures can retry.
  Existing persistence is single-process; external concurrent writers are not
  supported, and detected file changes cause retry rather than overwrite.
- Ready checks are cached by file signature/history version in process. A
  restart rechecks history but does not rewrite already-identified blocks.
- ChatView mounts one sidebar when navigation OR search is enabled. Search
  buttons are vertically stacked at its top; enabled ordinary navigation is
  underneath and retains its folding behaviour. Either feature remains
  independently unloadable. Popups portal to the chat stage rather than the
  narrow sidebar, and remain clear of the composer on mobile and desktop.

The sections below retain earlier implementation/benchmark history and do
not override this increment's explicit preparation boundary.

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

## First-query performance follow-up

The previous timings above describe the initial backend increment, not the
optimized implementation. Profiling confirmed that counting text is cheap;
index preparation and per-reference SQLite maintenance dominate first-use
latency. PR #3 parses JSONL and counts occurrences, retaining only a bounded
result list. It does not build or write an index. It still scans the entire
history for exact totals; its result limit does not explain this by incomplete
counting. The comparison helper source hash and pinned head remain unchanged.

This follow-up makes these changes without weakening history integrity:

- Schema v3 uses positionless trigram membership, not token occurrence offsets.
  Up to three 3-character anchors select candidates; complete literals are
  always verified. Dense posting statistics choose a selected-role scan when
  FTS cannot narrow the work. This affects the plan, not result coverage.
- Canonical message insertion and FTS feeding use batches instead of crossing
  Python/SQLite separately for every message and partition update.
- Query references are stored in bounded 128-reference JSON chunks rather than
  a row with multiple secondary indexes for every matched block. A page reads
  only the required chunks and retrieves its messages in one bounded query.
- Canonical prefix hashing encodes a sequence once and extends its digest with
  the encoded suffix; it does not serialize the whole old prefix twice. It
  still includes all canonical rows and fields, not only selected roles.
- A possible append materializes only suffix text blocks. If prefix proof,
  versions or ID uniqueness fail, a complete snapshot/rebuild is mandatory.
- Pan ID validation accepts exactly the prior lowercase canonical UUID formats
  without constructing UUID objects for each visited row.

Migration rebuilds only the disposable database, not history. Cursor signatures
from the previous database expire normally. FTS membership omits positions
because final occurrence counting/offsets come from canonical content, never
from FTS approximate candidates.

Representative final 10k mixed-role core run with stage instrumentation:

| Scenario | Initial increment | Optimized follow-up | PR in follow-up run |
| --- | ---: | ---: | ---: |
| New index + first sparse query | 228–230 ms | about 200 ms | about 16 ms |
| First dense query on existing index | 87–90 ms | about 40 ms | about 18 ms |
| Append + first query | 123–151 ms | about 70 ms | about 16 ms |
| All 100 dense pages | 469–513 ms | about 275 ms | no equivalent full pagination |
| Derived SQLite disk size | 15.3 MB | 7.1 MB | no derived index |

These are not a controlled claim of exact percentage improvements: previous
and new timings are separate runs with I/O/scheduling variation. Additional
follow-up runs give cold-build medians around 191–204 ms and append around
62–86 ms. Exact totals and message identities are asserted in every benchmark.

The final instrumented cold run attributes approximately 19 ms to canonical
loading, 27 ms to snapshot/proof preparation, 54 ms to message insertion and
50 ms to FTS feeding. Full Session replacement is around 121 ms **including**
insertion/FTS, not an additional 121 ms to sum with them. Append attributes
about 19 ms to loading, 21 ms to proof/snapshot, 12 ms to suffix/index update
and 10 ms to query references. These are inclusive stage medians and must not
be added to derive an exact end-to-end total.

Reproduce stage instrumentation:

```powershell
py -3.14 scripts/benchmark_history_search_content.py --compare-pr --profile-stages
```

The remaining cold-start disadvantage was architectural: this implementation
still requires a complete index before its first result. Future work should
borrow PR's lightweight first scan, separating first exact results from full
index preparation while retaining version-bound pagination, IDs, selectable
roles and unloading. That hybrid path is a design direction, **not implemented
by this follow-up**. For append, canonical prefix validation remains linear;
any future shortcut needs independently verifiable integrity evidence, never
revision/total growth alone. The new frontend contract is still pending.

## Completed scan-first fallback and frontend contract (2026-10-02)

The user explicitly authorized adopting PR's approach if indexing could not
meet first-result latency. Content mode now uses a PR-inspired read-only scan,
not a prerequisite FTS build. Both Session and global UI request this mode.
The former message-mode API/FTS path remains for backward compatibility;
it is not called by the new UI and does not warm up in the background.

- Canonical cold JSONL is streamed, not loaded into the shared Session cache.
  Selected pages reread only their bounded message rows. There are no index
  writes or new append/save hooks. New and imported four-role Pan identities
  from the previous increment remain authoritative; legacy IDs may be absent
  from results, as authorized. Native adapter IDs are never Pan identities.
- Counts are exact non-overlapping `casefold()` literal occurrences. Four
  roles are selected independently. Unselected roles are excluded before
  case folding/counting; their JSONL bytes still require parsing. Empty role
  selection performs no scan. Short/CJK/punctuation queries cover all history.
- A process-local LRU retains only IDs, row locations, roles and counts: at
  most 256 Session/query entries and 100,000 references. It retains no body
  copies, loaded Session objects, background jobs or persistent files. Cold
  cache reuse requires epoch/revision/total and file size/mtime agreement.
  Unknown totals and in-memory histories are conservatively scanned. Append
  or replacement invalidates reuse; append's first search is still linear,
  avoiding unsafe prefix assumptions. Cache eviction changes latency only.
- Signed version/scope/query/role-bound cursors, 409 stale handling, ordinal
  seeking and stable-ID relocation remain. Content cursors use a process-local
  signing key; restart expires them without a search database. Global results
  expose exact occurrences separately from loaded message count and paginate
  past 500. Session N/M and Enter/Shift+Enter now count occurrences.
- `showHistorySearch` is still default off and both components remain lazy.
  Unmount clears requests, keyboard listeners, selection and highlighting.
  A selected hidden QQ row is shown temporarily, without changing filtering.
  A selected tool/thinking row is split out of a folded group temporarily.
- The selected mounted message receives React-owned Markdown word marks and
  an active occurrence, in addition to block highlighting. Closing search
  restores ordinary rendering. Passive marks are capped at 500 per Markdown
  renderer; a later selected occurrence remains marked. Counting is never
  capped. Markup-only matches and matches spanning rendered inline nodes may
  have no literal visible word mark; Unicode length-changing case mappings
  retain correct backend totals but may fall back to block highlighting.
- Browser testing found overlapping mode buttons; popups now start below the
  button row, scroll within available chat height and leave the composer clear.

Same-machine pinned PR #3 helper comparison on 10k mixed-role synthetic rows:

| Core scenario | Scan-first | PR helper |
| --- | ---: | ---: |
| First sparse query, no retained references | 19.2 ms | about 15–16 ms |
| First dense count, 20k occurrences | 24.5 ms | 18.2 ms |
| Repeated dense query | 1.08 ms | 18.2 ms |
| Append, first query | 18.0 ms | 15.7 ms |
| All 100 dense pages | 125 ms | no equivalent cursor pagination |
| Derived database size | 0 | 0 |

This is deliberately **not** a claim of universal performance superiority.
First scans still pay for stable identity/last-ID-wins and paging contracts.
The earlier 200 ms build and 70 ms append prerequisites are removed, while
warm queries are substantially faster than repeatedly scanning. The pinned
helper is PR's actual function, not a substitute algorithm; its full UI is
not covered by these measurements. Cache-cold is not OS-page-cache-cold.

Real isolated HTTP observations: first sparse query 24 ms, first dense count
30 ms, append-first 24 ms, hot queries 2–3 ms; PR helper HTTP about 18–21 ms.
The real Chromium 10k count appears in about 144 ms including 120 ms debounce,
using one search request rather than 50 full-history downloads. These are
synthetic fixture observations, not production latency promises.

Reproduce from the repository root, with no production service/config/data:

```powershell
py -3.14 scripts/benchmark_history_search_content.py --engine scan --compare-pr
# From packages/web, after its production build:
node e2e/historySearchAcceptance.mjs
```

The browser harness uses a newly allocated temporary runtime, loopback 18769
(overridable by `PAN_SEARCH_TEST_PORT`, excluding 8767/8768), actual FastAPI
routes, and production Chromium assets. It stops only its own child process
and verifies port release. Evidence, screenshots and traces go into the ignored
`packages/web/e2e/test-results/history-search-content-completion` directory;
temporary fixture data is retained for inspection, never real Pan data.
