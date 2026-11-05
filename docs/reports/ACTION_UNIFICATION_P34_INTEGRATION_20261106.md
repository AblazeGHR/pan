# Action Group and Job integration

Integration starts at P4 `269ee28a5b74abc06f4d4f0176bb92fc01998b4e` and includes P3 candidate `1ba0e8dec6f7c9410ea9aa83525acb8f251cc497`. P3 remains under review for legacy JSON Group adoption.

The initial combined test `tests/test_action_unification_p34.py` failed with `KeyError: 'source'`: Group-created Jobs omitted the release from provenance. The fix forwards the release selected from the trusted Group record into the existing invocation source; callers cannot supply that field. Same-key replay must retain the original Job and operation identity.

The isolated P3, P4 and combined test files passed together: 45 passed, 2 dependency deprecation warnings. The test exercises the canonical Group invocation adapter; it is not a complete live MCP-to-Job transport or cross-platform acceptance.
