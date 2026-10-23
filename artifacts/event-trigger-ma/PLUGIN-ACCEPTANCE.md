# Plugin event lifecycle: MA acceptance (2026-10-22)

Accepted source: `838fe1673a40a81b15b640a5102aef3e960874a3`.
Isolation merge: `e5e9f05ca0c3e098b55eb2dc346f2b1df6b62f7d`.

MA independently reviewed per-plugin task ownership, stale reconcile tickets,
immediate local revocation, terminal close, late OS spawn cleanup, EOF/slow-reader
retirement and the plugin data-directory concurrency repair.

Independent command, run in event-plugin-adapter-20261022 using canonical Python:

```
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_plugin_event_lifecycle.py tests/test_plugin_events.py tests/test_plugin_events_http.py tests/test_automation_concurrency.py -o addopts= -q
```

Result: **36 passed, 0 failed/skipped, exit 0, 14.82 seconds**.
The four test-refreshed transport/HTTP JSON files are preserved in `plugin/`;
only those MA-generated modifications were restored in the TA tree afterward.
HTTP evidence confirms no owned process remaining, released port and unchanged
simulated practical sentinel. Original TA red/final evidence remains separately
in artifacts/event-plugin-rework and .validation/event-plugin-rework-final.

Accepted into isolation integration only. This does not claim whole-feature
acceptance, production deployment, reliable replay, POSIX process cleanup,
paid-provider consumption, or human acceptance. Main/practical unchanged.

Next: query selector backend remains in progress; UI TA is adapting the combined
feature to main fd15054c and its cached Action Panel, preserving duplicate and
all existing Action/Group semantics. Query UI and final combined verification
follow the backend contract.
