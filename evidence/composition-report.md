# Unified Job + User Inbox/Locker composition candidate

- Worktree: `D:/project/pan-worktrees/job-inbox-panel-composition-20261023`
- Branch: `feature/job-inbox-panel-composition-20261023`
- Unified Job/migration input: `4e51ec704bd3d2d04c6913da7c8f90f76fbc93f7`
- User Inbox/Locker panel input: `ba847160a01daf0049bb84575c427317ab7db236`
- Composition merge: `8418d6426b4180c02d6d558a3c48250b40c1dc98`
- Final compatibility fix commit: `cfe2663f2ad3b206406d5a28a5df193337f31b42`
- Main/practical topology checked at `140455976b7c60097fafdd4dc3f8e3b18a699ccc`; neither moved.

The merge keeps the unified Job/Action/Template registry, conservative legacy
projection, Event/Query/Run/Attempt/snapshot semantics, shared Agent Inbox and
Locker routing, and adds the User Inbox/Locker panel plus Interaction Card
contract. User Inbox remains separate from Chat and is labelled Locker. The
aggregation is a client-side view over authorized raw Lockers; it does not add
a new storage container or consumer cursor. User operations remain read/unread
and response/delete lifecycle only. History remains Session Chat history.
Agent Inbox receives no GUI route.

The only source fixes after the merge are compatibility tests: the shortcut
fixture now includes the existing `builtin:automation` entry, and the isolated
HTTP cleanup assertion accepts an explicit Windows terminated/dead process
state (including a reaped PID) while still failing for a live process. No
production permission, notification, queue, Job, Action, Template, or Inbox
policy was weakened.

## Composition checks

| Layer | Result | Evidence |
|---|---:|---|
| Cross-line Python | **517 passed**, 0 failed/errors/skipped, exit 0 | [composition-python-final.xml](composition-python-final.xml) |
| Vitest Job + Locker + Interaction + navigation | **121 passed**, 0 failed/errors/skipped, exit 0 | [composition-ui-final.xml](composition-ui-final.xml) |
| TypeScript | `pnpm exec tsc -b`, exit 0 | build command below |
| Production build | `pnpm build`, exit 0; 88 compressed assets | build output; no protected port |
| Isolated HTTP/MCP/Chromium | **2 passed**, exit 0 | [interaction-browser-final.xml](interaction-browser-final.xml) |
| Migration drill | Included in Python run: 4 temporary-copy tests passed | `tests/test_job_inbox_migration_drill.py` |
| Working tree | clean after final handoff | `git status --porcelain=v1` |

Python command:

```powershell
E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_job_inbox_migration_drill.py tests/test_job_inbox_boundaries.py tests/test_job_unified_contract.py tests/test_job_action_templates.py tests/test_job_system_actions.py tests/test_job_completion_recovery.py tests/test_job_overlap.py tests/test_job_mcp_config.py tests/test_jobs_api.py tests/test_session_message_jobs.py tests/test_agent_inbox.py tests/test_agent_notify.py tests/test_source_metadata.py tests/test_scheduler_dispatch.py tests/test_scheduler_mcp.py tests/test_action_groups.py tests/test_interaction_cards.py tests/test_interaction_card_api.py tests/test_interaction_card_http.py tests/test_user_locker_lifecycle.py tests/test_user_locker_browser.py tests/test_data_catalog.py tests/test_session_queue_api.py tests/test_illegal_queue_wake.py tests/test_independent_job_bridge.py packages/qq/test_qq_api.py packages/qq/test_channels.py -o addopts= -q --junitxml=evidence/composition-python-final.xml
```

Frontend commands:

```powershell
pnpm exec tsc -b
pnpm build
pnpm exec vitest run src/views/LockerView.test.tsx src/components/chat/MarkdownRenderer.test.tsx src/components/layout/ViewEntryGroups.test.tsx src/components/layout/ViewEntryRail.test.tsx src/shortcuts/registry.test.ts src/shortcuts/useGlobalShortcuts.test.tsx src/components/jobs/UnifiedJobForm.test.tsx src/components/jobs/JobTemplateEditor.test.tsx src/views/JobsView.bulk-search.test.tsx src/views/JobsView.schedule-shapes.test.tsx src/stores/workerStore.test.ts --reporter=default --reporter=junit --outputFile=D:/project/pan-worktrees/job-inbox-panel-composition-20261023/evidence/composition-ui-final.xml
```

The HTTP test uses a random loopback port, temporary data/profile, empty QQ
manifest, fake CLI, synthetic UserPrincipal and synthetic Trigger adapter. It
proves producer-only MCP Card creation, user 401/response authorization,
directed Trigger receipt, no Session Chat history contamination, Locker submit,
reject, reload, delete/tombstone, and responsive desktop/mobile page behavior.

The temporary migration drill proves that old Job/Template/Queue/Inbox data is
read without source rewrite; unknown legacy fields/content are retained in the
read/audit projection; old `enabled:true` does not enable a unified Job; and
Action/Template snapshots remain separate from Inbox content and notification
payloads.

## Failure and boundaries

The first combined run was 515 passed with two environment failures: the
browser ran before the local production `dist` was built and saw API Not Found,
and Windows psutil retained a terminated PID handle. Building first and
checking explicit terminal process states resolved both. No assertion was
skipped or weakened for a live process.

Not verified in this candidate: real user authentication, real QQ/provider or
account effects, OAuth/keyring/private exporter, protected services, ports
8767/8768, true mobile/OS interaction, large-scale Locker aggregation, real
notification delivery, in-place user-profile migration, or a full-repository
test run. Existing warning output (Pydantic forward references, frontend route
and chunk-size warnings, scheduler notification warning) remains documented;
it was not hidden or converted into success.

No push, release, deployment, main/practical movement, or existing service
restart occurred.
