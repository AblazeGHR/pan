# Task A: filtered Session history and single-message reads

Candidate for MA acceptance only. Worktree: `D:/project/pan-worktrees/task-a-history-read-20261021`; branch: `feature/task-a-history-read-20261021`; base: `6178efe0b7104473ba5968b1fbd7b7b7c0028d75`. Initial status was clean. Only the two assigned reads are implemented; no search work or sub-agent dispatch.

## Delivered behavior

- `session_get(session_id, limit=0, roles=None, tool_output="command_only", before=None)` preserves the original positional session_id/limit parameters. Default roles are user/assistant/tool. Thinking is opt-in. Limit is applied after filtering; positive limits retain the existing 200-row cap. `limit=0` retains full selected-history compatibility; use 50 for bounded reads.
- `session_message_get(session_id, message_id, roles=None, tool_output="command_only")` shares exactly the same projection. It reads one unique existing Pan ID inside that Session. It neither allocates identities nor writes/backfills history.
- Reuses the existing shallow Session loader, read executor, canonical cold scan source, Pan ID validator, and MCP managed access check. The existing Dashboard full/history routes retain their compatibility behavior unless the new historyRoles query activates filtered reads. No duplicate identity store or search tool was created.
- Tool command_only is deliberately conservative: a canonical Name(JSON) display exposes the name and explicit command/cmd **strings** only. Other arguments are omitted, including non-shell tool parameters. Opaque/truncated legacy displays expose no arguments. Output opt-in returns stored content only; it cannot reconstruct provider output that was never persisted. Both modes omit parts, rawContent and arbitrary extra fields. User/assistant/thinking return their stored content body, without auxiliary parts.
- LastResult keeps terminal status/identity and a string final result only when assistant is selected; provider extras are removed. Summary body previews cannot bypass the role/tool projection.
- Canonical `messageIndex`, `historyStart` and exclusive `nextBefore` remain positions rather than message identities. `historyTotal` counts canonical rows; `historyFilteredTotal` counts selected bodies. A null nextBefore ends paging. Tests verify no duplicate/missing selected messages and stable older paging after append.
- Valid existing Pan IDs are retained with identityKind=persistent. Old/native/positional IDs are not promoted: legacy rows have identityKind=legacy_unaddressable and no fabricated messageId. Errors distinguish invalid_message_id, session_not_found, message_not_found (missing/cross-Session/ambiguous), message_filtered, invalid_params, history_changed and history_unavailable.
- Both MCP tools authorize before a target read and retain the existing self/managed/unrestricted semantics. Restricted callers cannot read unrelated Sessions. HTTP remains a trusted local admin interface; no fictional manager authentication was added.

Examples:

```python
session_get(session_id, limit=50)
session_get(session_id, limit=50, roles=["user", "assistant"], before=previous["nextBefore"])
session_get(session_id, limit=50, roles=["tool"], tool_output="include_output")
session_message_get(session_id, persistent_message_id)
session_message_get(session_id, thinking_message_id, roles=["thinking"])
```

## Verification evidence

Final isolated run: **105 passed, 0 failures, 0 errors, 0 skipped**, pytest child exit 0. This contains 23 new read tests, 34 existing MCP isolation cases, 21 history offload cases, 12 bookmark HTTP cases, 13 handbook cases and 2 manifest prompt cases.

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_history_read.py tests/test_mcp_isolation.py tests/test_mcp_handbook.py tests/test_eventloop_history_offload.py tests/test_session_bookmarks_api.py tests/test_sma_manifest_prompt.py -o addopts= -q --junitxml=artifacts/task-a-history-read-junit.xml
```

Evidence: [JUnit](../../artifacts/task-a-history-read-junit.xml), [test output](../../artifacts/task-a-history-read-tests.txt), [regression source](../../tests/test_history_read.py). Compileall for the changed Python modules/tests, manifest JSON parsing and git diff --check all passed (exit 0).

New cases cover mixed roles, default and explicit options, tool output duplicated into auxiliary fields/parts, unknown/truncated tool formats, auxiliary-only summary leaks, post-filter limits/pagination, append stability, stored identity fidelity, old embedded/JSONL records without IDs, crash tails, duplicate identities, no read writes, actual managed checks, Session isolation and concurrent cold-file mutation errors. A 20,000-row cold JSONL test verifies selected latest rows, exact selected count, positive cap, and no full-history hydration. The O(limit + largest row) retention bound follows the locator deque implementation; no RSS/latency benchmark is claimed.

The integration case uses a real owned loopback socket allocated by the OS (asserted outside 8767/8768), real Uvicorn HTTP routing, urllib HTTP calls, and the installed MCP client's initialize/list_tools/call_tool protocol over the SDK's in-memory transport to FastMCP. It verifies schemas, default projections, output opt-in, permission denial for both tools, and listener closure. Only the real relevant app routes are mounted; production lifespan/background services are disabled. This proves protocol dispatch plus actual HTTP, not an external stdio/SSE client deployment.

Five environment/dependency warnings remain: pytest-timeout is absent so its config key is unknown; Starlette/httpx and two websocket APIs are deprecated; Pydantic settings reports the existing lifespan forward reference. No checks were skipped or assertions weakened.

## Boundaries for MA acceptance

Positive-limit cold JSONL reads retain bounded references and selected bodies, but exact totals/ID uniqueness still require O(history rows) scanning. The explicit legacy limit=0 result is unbounded by design for compatibility, as is a single stored body's size. Legacy history embedded inside the metadata JSON requires full-file parsing. Keep these qualifications when describing large-history support.

Pagination uses canonical positions. Append preserves older positions; replacements/reimports can invalidate them. Callers must compare epoch/revision and restart after a change; concurrent disk changes during a scan produce history_changed. There is no new authenticated opaque cross-call snapshot cursor.

No local full-suite run, frontend build/UI, paid model/provider run, external stdio/SSE deployment, production startup/lifespan or human acceptance is claimed. No main/practical movement, push/release, existing-service restart or request to 8767/8768 occurred. No production data was used or migrated.

Repository tool descriptions, MCP manifest usage hints, [Pan skill](../skills/pan/SKILL.md) and [HTTP reference](../skills/pan/references/http-api.md) are synchronized. MA should synchronize the repository Pan skill and reference changes to the applicable installed/user-level Pan skill (including `C:/Users/14709/.agents/skills/pan/` if that is the active installation) and any maintained editor copy after acceptance. This TA did not change user-level skills. The candidate remains subject to MA review and integration.
