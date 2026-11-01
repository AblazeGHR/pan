# Main readiness: MCP retirement contract alignment

Base merge: a7c1ed4602d39a9b2f01462b49c86ff8800f1c0b, parents main9b4506c94 and complete Job9bdd490de.

On this exact combination, the original scheduler-surface suite reproduces 13 failures: 12 direct calls expecting ToolError("Unknown tool") and one core stdio text expectation. The approved rev16 implementation instead returns CallToolResult(isError=True, structuredContent.error.code=capability_unsupported), before transport. Native projection still rejects tool_unavailable; genuinely unknown tools still raise Unknown tool.

Preserved original-red/ contains byte-exact pre-change test source, JUnit and full failure log, plus the first full-suite collection JUnit (missing local nonebot). No historical red result is relabeled green.

The successor edits only the obsolete response contract assertions. All original tool x identity cases, native projection, catalog snapshot, no callable definitions, no HTTP calls and no file mutation remain. Added exact structured guidance/text consistency and genuinely unknown-tool refusal. No tests skipped, no roots excluded, no product dispatcher change.

Focused command (isolated venv with QQ dependencies):
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_surface_retirement.py tests/test_retirement_rev12_mcp.py tests/test_retirement_rev16_mcp_stdio.py -q --junitxml=.validation/readiness/mcp-successor-v2.xml
Exit0. Exact counts are in the JUnit summary below; full-suite and private-copy acceptance remain pending.
