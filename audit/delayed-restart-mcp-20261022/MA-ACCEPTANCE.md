# MA acceptance 2026-10-22

Source 37ca60ea3f06da92d4c477c16e2db7eeb27a9417, parent fd15054c.
Reviewed MCP self/managed access without claim, existing_worker_only HTTP
atomic admission, offline/Illegal refusal and retiring receipt handling.
Worker lifecycle unchanged. Stdio and remote use shared registry.

Independent pytest command: canonical .venv Python -m pytest
 tests/test_mcp_delayed_restart.py tests/test_steer_restart_priority.py
 tests/test_delayed_restart_locked_queue_http.py::test_locked_queue_delayed_restart
 tests/test_mcp_remote_parity.py::test_remote_tools_list_equals_stdio_registry
 tests/test_mcp_remote_parity.py::test_remote_delayed_restart_real_http
 -q -o addopts= --timeout=60 --junitxml=audit/delayed-restart-mcp-20261022/ma-final.xml

33 passed, 0 failed/skipped, exit 0, 23.54 seconds. Four dependency warnings.
Includes isolated real stdio/HTTP/fake CLI and remote HTTP, not paid provider.
Earlier two baseline plugin queue assertion failures remain documented in README;
not represented as passing by this focused acceptance.
Accepted for local fast-forward; no service restart or production activation claim.
