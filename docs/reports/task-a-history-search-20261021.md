# Task A3: Session history search MCP wrapper

Fast-forward candidate for MA acceptance only. Worktree `D:/project/pan-worktrees/task-a-history-read-20261021`; branch `feature/task-a-history-read-20261021`; accepted base `d5fd2cca4d0dba336ca47142530411d5b1b6c180`. Initial worktree status was clean. This round implements only task A3; no other queued A item, sub-agent, main/practical movement, push/release, paid model, full local suite, existing-service operation, or request to 8767/8768.

## Delivered API and reuse

```python
session_history_search(session_id, query, limit=50, cursor=None,
                       roles=None, tool_output="command_only")
```

Recommended first call: `session_history_search(session_id, query="error", limit=50)`. Follow `nextCursor` with identical options until null. Fetch a selected hit via `session_message_get` using matching roles/tool_output.

The wrapper uses the existing `/api/history/search` messages-mode HTTP endpoint and SQLite FTS5 engine in the same `history_search.sqlite3`. Session scope is mandatory; there is no agent-global search path. Existing `_check_access` runs before each search, including continuation requests, so permission revocation is rechecked. The HTTP endpoint remains a trusted local admin interface rather than manager-authenticated access.

Defaults are user/assistant/tool with command_only. Thinking is opt-in; `tool_output="include_output"` explicitly searches/excerpts the complete stored tool content. To avoid output-only text influencing hits or pagination, command projection is indexed and matched before results are limited. Hiding snippets after matching raw output would not meet that contract.

A small extension to the existing index adds internal `tool_command` rows/FTS partition derived by the already accepted `history_read.command_content`. Public hit roles remain tool. The original persisted Pan messageId and canonical messageIndex are retained; no identity is created or backfilled. Non-shell arguments and opaque/truncated legacy call arguments remain conservatively omitted, as in A1/A2. Raw and projected partitions have independent query-reference keys and can coexist. Public role validation does not expose the internal role.

This requires disposable index schema 4: the existing lazy schema upgrade rebuilds the disposable index only, preserving canonical JSON/JSONL. The uniqueness key now admits raw and projected rows for one canonical message ID; canonical duplicate handling remains the existing last-occurrence behavior. Existing prefix-digest append proof, rebuild fallback, transaction protection, FTS candidate/short-literal matching, snippet logic and bounded query cache are reused. There is no second database, new search engine, indexing hook in persistence, polling or background indexing worker.

Queries are trimmed case-insensitive literal substrings, at most 512 characters. Limits clamp to 1..100 (default 50); output consists of bounded snippets and canonical identity/version metadata, without parts or full history. Empty query/roles return an empty page. Legacy rows without valid persistent IDs are skipped without writes. Existing HTTP errors pass through, including error="Session not found" for nonempty queries on missing targets, 422 validation and 503 unavailable errors. Local option errors and missing scope use invalid_params; managed denials use permission_denied.

The existing signed cursor additionally binds tool mode. Old cursors without the optional mode retain include_output semantics. Query/scope/normalized limit/roles/tool-mode changes, history version changes, invalid signatures, the 15-minute expiry, and disposable-index signing-key changes invalidate continuation (409 detail.code=history_search_cursor_expired). Start without cursor again. A schema rebuild invalidates outstanding old index cursors.

## Evidence

Final isolated command (exit 0):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_session_history_search_mcp.py tests/test_history_search_index.py tests/test_history_search_api.py tests/test_history_search_content.py tests/test_history_search_scan.py tests/test_history_read.py tests/test_mcp_isolation.py tests/test_mcp_handbook.py tests/test_sma_manifest_prompt.py -o addopts= -q --junitxml=artifacts/task-a-history-search-junit.xml -o junit_family=legacy
```

**146 passed, 0 failures/errors, 0 skipped**: 11 new wrapper cases; 16 index, 22 HTTP search, 18 content/partition cases (including 2 new concurrency/proof cases), 7 canonical scan, 23 accepted history-read cases, 34 MCP isolation, 13 handbook and 2 manifest prompt cases. The existing schema-upgrade assertion is updated from 3 to 4, and its FTS integrity checks now include the command partition.

[JUnit with owned listener properties](../../artifacts/task-a-history-search-junit.xml), [test output](../../artifacts/task-a-history-search-tests.txt), [wrapper regressions](../../tests/test_session_history_search_mcp.py).

Coverage includes cold/hot sources, raw/projection interleaving and concurrent partition enable, output-only matches excluded by default, explicit output/thinking, parts/raw auxiliary exclusion, post-match limits/paging without repeats, option/scope/mode/version/signature cursor rejection, permission revocation, exact persisted IDs, legacy no-ID skipping without history writes, existing cached index reuse without reload, preserved rowids under verified append, duplicate-ID and changed-prefix rebuild, replacement refresh and schema upgrade. Positive result caps/snippet lengths are asserted; no new memory/latency benchmark is claimed.

A real loopback Uvicorn socket at **127.0.0.1:55124**, owned by test PID **47200**, served the real relevant application routes with production lifespan disabled. The installed MCP client performed initialize/list_tools/call_tool over the SDK in-memory transport, while the MCP wrapper used actual urllib HTTP. This verified exposed schema/defaults, continuation, output/thinking opt-ins, permission denial and mode mismatch. The owned thread/socket was stopped and port closure asserted. No pre-existing listener was used. The evidence proves MCP protocol dispatch plus real HTTP, not an external stdio/SSE deployment.

Compileall of changed Python modules/tests, manifest JSON parsing and final git diff whitespace checks pass (exit 0). Five existing dependency/config warnings remain: unavailable pytest-timeout config, Starlette/httpx deprecation, Pydantic lifespan forward reference, and two websocket API deprecations from the accepted A1/A2 integration test. No assertions or skips were weakened.

## Acceptance boundaries and documentation sync

Bounded search output is distinct from bounded cold-index construction: existing first/rebuild indexing may load a full Session history and verify full-prefix hashes. Query reference limits remain the existing implementation. No full-suite, frontend/UI, production lifespan, paid provider, external stdio/SSE client, long-running load test or human acceptance is claimed.

Existing HTTP defaults remain compatible: default body roles and full stored tool text when tools are explicitly selected. The new command_only HTTP option is for indexed messages mode; content occurrence-count mode rejects it with 422 and otherwise retains its prior behavior. The new MCP tool intentionally exposes the indexed message-hit contract only.

Tool description, MCP manifest hints, [repository Pan skill](../skills/pan/SKILL.md) and [HTTP reference](../skills/pan/references/http-api.md) are synchronized. MA should sync this round's repository skill/reference additions to the applicable user-level installation and maintained editor copies after acceptance; this TA did not modify them. Candidate completion is not MA acceptance/integration.
