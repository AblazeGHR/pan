# Delayed restart MCP candidate

Worktree: D:/project/pan-worktrees/delayed-restart-mcp-20261022
Branch: feature/delayed-restart-mcp-20261022
Original baseline: 1210129c515b57d2556e89c488be05d7ba2596ef
Fast-forward parent: fd15054c3fe78cf1beb419ec4c3f0ab35b969b28 (main/practical observed, neither moved by TA).

Two tools share the stdio/remote registry: agent_restart_delayed(session_id), agent_restart_delayed_status(session_id). Existing managed/self authorization is reused, without claiming. POST uses the existing delayed-restart route with existing_worker_only=true. This opt-in synchronously refuses missing/offline/Illegal targets; an accepted restart's retirement gap returns its existing receipt. Original HTTP default offline start policy remains unchanged. Worker lifecycle implementation is unchanged. Settings/model are never written. Pause continues to gate message handoff while allowing retirement. Held, terminal-error and shutdown gates remain authoritative.

## Final validation

Interpreter: D:/project/Pan-main/.venv/Scripts/python.exe, shared read-only; dependency script -Check exit 0, no installs/junction changes.

- `python -m pytest tests/test_mcp_delayed_restart.py tests/test_steer_restart_priority.py tests/test_delayed_restart_locked_queue_http.py::test_locked_queue_delayed_restart -q -s --timeout=45 --junitxml=audit/delayed-restart-mcp-20261022/final-lifecycle.xml`: exit 0, 31 passed, 0 skipped, 6.291s. Log: final-lifecycle.log.
- `python -m pytest tests/test_mcp_remote_parity.py::test_remote_tools_list_equals_stdio_registry tests/test_mcp_remote_parity.py::test_remote_delayed_restart_real_http -q -s --timeout=60 --junitxml=audit/delayed-restart-mcp-20261022/final-remote.xml`: 2 passed, 0 failures/errors/skips, 16.871s. Log: final-remote.log. Terminal session handle expired before exit-code retrieval; JUnit confirms successful assertions and teardown.
- Earlier complete remote contract run: 28 passed, exit 0, no skips (delayed-restart-remote.xml/log). Includes managed denials for both tools and required schema fields.
- compileall of changed Python modules/tests and git diff --check: exit 0.

Real stdio MCP subprocess -> isolated HTTP -> local fake CLI proves running acceptance, duplicate intent, status equality, terminal-before-replacement, idle restart, unchanged settings/model, Pause preservation, and Illegal refusal after an owned fake provider crash. Real authenticated streamable HTTP MCP -> isolated HTTP proves read-only null status and offline refusal. Catalog/schema/description parity is tested through real remote tools/list. No paid provider is involved. Real process IDs, data roots, ports and receipts are printed in logs. Owned fixtures verify identity before cleanup; cleanup.txt confirms recorded loopback ports released. No access to 8767/8768, no user Worker/service restart, no child tasks, push, release, or main/practical movement.

## Failed experiments and limits

Failure evidence is retained. Initial new-test failures were incorrect queue field `paused` (actual queuePaused), a fake CLI hold token outside its scoped workdir, and an assertion including the service startup discovery probe rather than target-workdir processes. Corrected tests retain substantive assertions.

The broader existing plugin integration test fails in plugin-first and manual-first at test_delayed_restart_locked_queue_http.py:207: manager queue_pending length is 0 rather than 1. The exact failures reproduce with original baseline server.py (delayed-restart-baseline-plugin.xml/log, 2 failures, exit 1); candidate server restored byte-for-byte afterward. No report mechanism change, skip or weakened assertion was made. This remains an existing failing boundary, not a passing regression claim.

Not run: full suite, UI/browser, real model/CLI providers, service deployment, real Cloudflare/Internet transport. Pydantic unresolved forward-reference and deprecated streamablehttp_client warnings remain. Candidate awaits MA acceptance.
