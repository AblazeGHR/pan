# Authorization corrective candidate validation

Worktree: `D:/project/Pan/data/workdirs/authorization-mcp-capabilities-20261024-candidate`.
Branch: `feature/authorization-mcp-capabilities-20261024`.
Base practical: `53dff07b42450105b4e43512c43395ccedf228b4`.
Main at baseline and resume check was `03158ec517a98493e41b292b7f0575fefb49fe12`.
Password candidate `506399ec54e0a7b99a7bbb0fc406407040248238` is included by
cherry-pick `6398b215`. This commit corrects superseded candidate `f9454991`.
No push, protected-ref update, protected-service restart, or access to 8767/8768.
Final readback observed main at `8f44cd734646d8ece2030ba85501c73e4eefc13b`;
its reflog records a separate fast-forward at 14:12:55 +0800 during this reporting
phase. This task issued no main/practical mutation. Practical is still the base
above. The candidate was not rebased onto that concurrent main update.

## Recorded results

These are the completed runs from 2026-10-24 before the account rotation pause.
On resume only XML, source hashes, diff and repository state were checked; the
green suites were not run again. `source-sha256.json` has 50 changed/new source
and test files; every byte hash matched on resume. Source was not edited during
the resumed reporting/cleanup phase.
The repository's normal initial commit hook also completed its React frontend
check successfully. Archived console outputs have trailing
whitespace normalized; XML reports and source hashes are preserved.

| Evidence | Result | Duration |
|---|---|---|
| unit.xml / unit-output.txt | 443 passed, 0 failures/errors/skips | 229.014s |
| runtime.xml / runtime-output.txt | 171 passed, 0 failures/errors/skips | 17.490s |
| http.xml / http-output.txt | 10 passed, 0 failures/errors/skips | 153.842s |
| frontend-output.txt | 7 files, 136 passed, 0 failures/skips | 7.69s |
| build-output.txt | TypeScript + Vite + 92 compressed production assets verified | Vite 7.92s |

The Python reports overlap: 456 unique `(classname, name)` cases, not 624.
The later runtime run covers the final additional revocation/recovery cases.
Final isolated HTTP run used a temporary profile and randomly allocated port
62315. The HTTP tests include production Chromium/HTTP/WS flows and owned-process
cleanup assertions. `authorization.png` is the actual App Settings matrix capture.
No persistent Pan background Job or sub-agent was created for this task; all
validation command sessions had completed before the pause.

## Reproduction selections

From the worktree root, use `python -m pytest -q` with the following file selections
and `--junitxml=<report path>` (the archived files are renamed final reports):

```text
unit:
tests/test_authorization_password.py tests/test_authorization_permissions.py
tests/test_authorization_mcp.py tests/test_authorization_runtime.py
tests/test_action_groups.py tests/test_automation_core.py
tests/test_automation_retry_parallel.py tests/test_job_action_templates.py
tests/test_job_mcp_config.py tests/test_event_store.py tests/test_plugin_platform.py
tests/test_plugin_events.py tests/test_jobs_api.py tests/test_scheduler_api.py
tests/test_job_system_actions.py tests/test_job_unified_contract.py
tests/test_mcp_port_propagation.py tests/test_mcp_remote_parity.py

runtime:
tests/test_authorization_permissions.py tests/test_authorization_mcp.py
tests/test_authorization_runtime.py tests/test_automation_core.py
tests/test_automation_retry_parallel.py tests/test_job_action_templates.py
tests/test_job_system_actions.py tests/test_job_unified_contract.py

http:
tests/test_automation_http.py tests/test_automation_retry_http.py
tests/test_job_action_http.py tests/test_event_store_http.py
tests/test_plugin_events_http.py tests/test_job_inbox_http.py
tests/test_job_unified_http.py tests/test_eta_report_integration_http.py
tests/test_eta_final_combined.py
```

The HTTP harness must retain its random free-port selection and temporary profile;
never point these tests at a running production profile or ports 8767/8768.

From `packages/web`, the frontend selection is `npx vitest run` with:

```text
src/components/layout/AuthorizationSettings.test.tsx
src/components/layout/AppSettingsModal.test.tsx
src/components/jobs/JobTemplateEditor.test.tsx
src/views/AutomationView.test.tsx src/stores/actionRegistryStore.test.ts
src/views/ActionsView.test.tsx src/views/JobsView.bulk-search.test.tsx
```

Production build command: `npm run build` (runs `tsc -b`, `vite build`, then
`node e2e/verify-precompressed-assets.mjs`). Python compile validation and
`git diff --check` also passed; generated legacy reports were restored afterward.

## What the tests establish

- Real core and native stdio MCP -> existing local IPC -> ASGI -> object API,
  without a user password/Bearer: grants on succeed, grants off deny. HTTP body
  and identity headers cannot impersonate that private transport marker.
- User Bearer CRUD works with all Agent grants off; matrix CAS rejects stale and
  concurrent writers. All 22 grants have individual mapping/denial coverage.
- Admission and effect barriers recheck delegated authority/revision. User
  execution remains independent; already-started effects are not force-killed.
  Recovery/retry revocation preserves budget and prevents another Attempt.
- Passive Template rejects enabled/lifecycle/execute; one-off Job has null
  templateId. System definitions remain immutable with every grant enabled;
  their separately stored lifecycle override does not rewrite the definition.
- Sensitive command stderr/invalid parameter values do not enter error logs.
  Existing owner, lease, generation, pause, uncertainty and business CAS checks
  retain their assertions. No new skip/xfail or weakened business assertion.
- Existing HTTP fixtures now use their own user identity; original Action,
  Automation, Job, EventStore, plugin and ETA business checks pass. Detailed
  fixture changes are in the design document.

## Failures fixed and remaining boundaries

Final selections have zero failing cases. During repair, stale anonymous-user
fixtures, Template enabled fixtures, disabled-Action retry admission, missing
passive Template usability projection, and raw/incomplete Job WS mutation payloads
caused failures. They were fixed in source or migrated to the correct principal;
the original business assertions remain, including dirty-editor/CAS and responsive
browser assertions. Superseded failing-run outputs were removed from the candidate.

Warnings remain: the environment lacks the pytest timeout plugin (unknown option
and marks), Pydantic emits an existing forward-reference warning, and Vite emits
the large-chunk advisory. None are test failures; timeout enforcement by that
pytest plugin was not active.

This is targeted regression validation, not the entire repository suite or remote
CI. The four-object authorization chain was proved over local stdio/IPC and
isolated HTTP. A deployed OAuth/private-exporter remote gateway was not exercised
for this new chain; it needs the host's existing IPC capability, and missing IPC
fails closed. Existing remote-parity tests passing do not prove deployment of the
new four-object path. No OAuth/exporter changes are included. The reused IPC has
its existing same-OS-user capability boundary, not isolation from arbitrary code
already running as that user. MA acceptance and main/practical integration remain
separate from this local candidate delivery.
