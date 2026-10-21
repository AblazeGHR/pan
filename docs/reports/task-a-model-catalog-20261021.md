# Task A9: credential-free model catalog

Fast-forward candidate for MA acceptance. Worktree `D:/project/pan-worktrees/task-a-history-read-20261021`; branch `feature/task-a-history-read-20261021`; accepted base `e70c2b5adc85af167fa7da0c61bd049fdf9fd059`. Initial status was clean. Only A9 is implemented in this round; no sub-agent delegation or other A work.

## Original definition and scope

Checked `D:/project/Pan/data/workdirs/Pan_SMA/action-dispatch-20261021.md`: the historical A list explicitly identifies item 9 as model_catalog. PAN_SMA `.workflow/overview.md` records the current A9 dispatch. `D:/project/Pan-main/.workflow/overview.md` defines the pending unified catalog as returning model capabilities, multiplier provenance and freshness. These records do not define more detailed mandatory fields. This implementation follows the current user scope: easy consolidated reads of existing service model data and explicitly declared adapter/model capabilities, without credentials, account aggregation, new polling or paid model calls.

Inspected the original model_list, /api/models, /api/adapter/config, registry, adapter declarations, Codex model-effort parser and CBC multiplier selection/TTL code before implementation. Existing APIs already provide discovery and UI configuration; the new entry does not replace their behavior.

## Delivered contract

```python
model_catalog(adapter="codex")
model_catalog()  # separate metadata directories for registered adapters
model_catalog(adapter="cbc", session_id=accessible_session_id)
```

The MCP wrapper uses GET /api/model-catalog. Explicit session_id or bound PAN_AGENT_SESSION_ID passes through existing managed access checks before the read. Without a Session identity, it retains public model-metadata read semantics. HTTP is trusted local admin, not manager-authenticated access. Unknown adapters/blank explicit adapter return explicit errors. model_list and its adapter-required discovery behavior are unchanged.

The catalog reads existing process `_cached_models` or literal supported_models declarations. It does **not** invoke dynamic model/default discovery properties, CLI commands or refresh/invalidation. Cold dynamic sources return models=[] with modelList.status=unknown; this is not proof that no models exist. Stale caches remain labeled stale. IDs are selectable metadata, not proof of account authorization, balance, provider reachability or paid-model availability.

Adapter capabilities are sourced from explicit boolean declarations (resume, fork, nativeSteer, nativeInterrupt, steerReceipt); absent/non-boolean values are null/unknown rather than false/support. supportedSettings, adapterEffortValues and executionModes are separately scoped to the adapter, never copied into per-model support. Retired oneshot is filtered consistently with existing service configuration. Dynamic defaults remain unknown rather than selecting the first model.

Per-model effort uses explicit model_efforts only. The Codex adapter adds a credential-free metadata hook that reuses its existing models_cache.json parser; hidden models and unrelated/raw fields are not exposed. Generic effort values do not fill missing model entries. Descriptor properties are not invoked as hooks. Hook failures degrade safely without exception text.

Each projected value has status/source/scope. modelList.source records the cache/declaration attribute, while underlying origin and upstream sourceUpdatedAt stay unknown/null if not recorded. Cache age/fresh-or-stale reflects only existing monotonic cache timestamps and TTL, not upstream freshness; absent/invalid timing stays unknown. No timestamp is fabricated from request time.

Multiplier values are projected only from directly readable literal adapter maps, with unknown source freshness. Zero is preserved; malformed, absent or unsafe values are unknown, never default x1. **Current CBC dynamic multipliers remain unknown in model_catalog, even with a warm multiplier map:** refreshing that property reads the current-account JWT, while its existing process cache has no credential-free proof of current-account binding. The catalog neither calls that property nor reads the unbound map. Existing CBC multiplier APIs retain their behavior. This is the intentional safety downgrade required by the no-credential boundary, not a claim that multiplier support was removed.

No credentials, account collections, new database, model call, background indexing/polling, cache mutation or new discovery process is introduced. The HTTP read runs on the existing store executor; per-adapter projection failure preserves other adapters with an explicit unknown row.

## Evidence

Final isolated command (exit 0):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_model_catalog.py tests/test_mcp_model_list.py tests/test_mcp_handbook.py tests/test_adapter_config_model_meta.py tests/test_sma_manifest_prompt.py tests/test_codex_adapter.py::test_model_efforts_from_models_cache tests/test_codex_adapter.py::test_supported_models_ttl_cache -o addopts= -q --junitxml=artifacts/task-a-model-catalog-junit.xml -o junit_family=legacy
```

**44 passed, 0 failures/errors, 0 skipped**: 14 new catalog cases, 5 unchanged model_list cases, 13 handbook cases, 8 existing adapter-config/model-multiplier API cases, 2 manifest cases and 2 existing Codex model-cache/effort cases.

[JUnit](../../artifacts/task-a-model-catalog-junit.xml), [test output](../../artifacts/task-a-model-catalog-tests.txt), [catalog regressions](../../tests/test_model_catalog.py). Tests cover cold/empty/stale caches, cache age and unknown timing, declared false/empty/zero, per-model effort isolation, dynamic-property/discovery traps, CBC credential/config/multiplier-refresh traps, unbound warm multiplier suppression, Codex safe metadata parsing, unknown adapters, managed denial/self/allowed access, safe failure and descriptor handling, serialization and legacy compatibility. The test auth.json sentinel is synthetic and confined to tmp_path; no user credential files are read.

Real protocol test: owned **127.0.0.1:64222**, test PID **6464**, Uvicorn serving the real catalog handler without production lifespan, injected deterministic adapter metadata, actual urllib HTTP from the MCP wrapper, and installed MCP client initialize/list_tools/call_tool over SDK in-memory transport. Exposed schema, per-model effort and null unknown capability serialize correctly. Listener/thread shutdown and port closure were asserted. This is real HTTP plus MCP protocol dispatch, not external stdio/SSE deployment or production account validation.

Compileall of the changed Python modules/tests, manifest JSON parsing and final diff whitespace checks pass (exit 0). Three existing environment warnings remain: pytest-timeout option unavailable, Starlette/httpx deprecation and Pydantic lifespan forward reference. No assertions/skips were weakened.

## Acceptance and synchronization boundaries

This is an intentionally cache/declaration-only catalog. Missing/current-account-unsafe multipliers, cold dynamic model lists, dynamic defaults and unrecorded provenance/freshness remain unknown. It does not establish live availability, per-model undeclared capabilities, context limits, modality support, prices, account quotas or entitlement. No full local suite, frontend/UI build, paid provider, existing-service request, production lifecycle, external MCP transport or human acceptance is claimed.

Tool description, manifest usage hints, [repository Pan skill](../skills/pan/SKILL.md) and [HTTP reference](../skills/pan/references/http-api.md) are synchronized. MA should sync this round's skill/reference additions to the installed user/editor skill copies after acceptance; this TA did not modify user-level skills.

No main/practical movement, push/release, existing service restart/operation, or request to 8767/8768 occurred. The candidate remains subject to MA acceptance and integration. No quota-95 notification arrived during this round; the last quota value was user-provided, not independently refreshed through a protected service.
