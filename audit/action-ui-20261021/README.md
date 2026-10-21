# Action IDs and three-section Panel — candidate for MA review

The task is complete in isolated tree
`D:/project/pan-worktrees/action-groups-review-20261021`, branch
`integration/action-groups-review-20261021`. Fast-forward delivery policy applies;
MA acceptance and target ref advancement remain separate. No main/practical ref
was moved by this task, and no acceptance or deployment is claimed here.

Initial HEAD was clean `6eff88c6fc665f989e475700abf92386d0fbbb93`.
Implementation commit: `3f593a27`.
During the task MA advanced main/practical to
`4d3b1cf1261eb2c0cb324aa1a955dfac5b79ba2c` (hang diagnostics/list projection work).
That baseline was merged without conflicts into this tree as
**`9abca940efae776390c30641ebbd05d5852ac3ab`**, the final tested source commit.
The subsequent audit-only commit records these exact sources without changing them.
At final inspection main was an ancestor of this candidate.

Read CODEBUDDY, repository constraints, current canonical `.workflow/constraints.md`
and overview, and the Pan skill. Inspected the supplied Session plugins screenshot.
No frozen design rewrite, unrelated migration or new import feature was introduced.

## Result and identity contract

- Store.put allocates an ID when `item.id` is absent, under the existing registry
  writer that also validates revision and atomically publishes the item. UUID-based
  `action_`/`group_` IDs have 24 hex digits and retain the existing 32-character
  lowercase/digit/underscore constraint. Display names do not define identity.
- Allocation checks both collections and retired plugin IDs, retries collisions,
  and fails without publishing after bounded exhaustion. Concurrent same-name
  creates cannot overwrite each other. No dependency or registry migration is added.
- New user creation routes `POST /api/actions/{actions|groups}` return the registry
  plus the authoritative `item`. Explicit valid IDs are accepted but existing IDs
  reject rather than overwrite on this creation route. Existing explicit-ID PUT
  compatibility remains, requiring the same URL/body ID for updates.
- Agent action_drafts put can omit item.id and returns the authoritative item;
  explicit-ID updates remain compatible. Agent Action writes still force disabled;
  no build/enable/unbuild/execute permission was added. MCP description, repository
  Pan skill, HTTP reference and Actions documentation were updated. User-installed
  skill synchronization is left to MA as requested.
- Renaming updates the original ID. Existing IDs, shared references, release
  snapshots and plugin identities are unchanged. There is no frontend ID generator.
- Creation without an ID is not idempotent after an unknown response. The docs/tool
  description require list/reconciliation before replay. On a confirmed create,
  the UI stores the returned identity before secondary catalog refresh; a catalog
  refresh failure cannot cause the next Save to issue another creation.

## Panel

Actions / New Action / Groups are three English navigation sections. Actions has
definition cards, status, execution/effect, reference counts and explicit Edit.
New Action omits ID inputs and separates identity, input fields/advanced JSON
schema and API/command execution. Existing Action and Group details expose a
selectable ID and Copy ID, including a clipboard-unavailable fallback.

Groups contains its own creation/editing, shared Action choices, release metadata,
build/unbuild/delete and running blockers. Use in Session opens contextual binding
for that Group, explains what selection does and handles an empty Session list.
It reuses PluginSelection and sends the entire prior binding set with only the
chosen plugin changed; unrelated plugin bindings remain intact. Existing mode
availability, selection revisions and Worker apply policy are preserved. Session
load responses are abort/stale guarded. Global loading/error and field-level schema
errors retain repairable user input. No global unlabelled Session plugins card remains.

The Panel uses existing theme tokens and shared Button/PluginSelection components.
Narrow layouts wrap controls, stack cards/fields and break long identity text.
JSON schema values preserve existing advanced properties on field edits; raw JSON
can represent nested/default/constraint definitions and receives server validation.

## Validation and exact scope

| Check | Result | Provenance |
| --- | --- | --- |
| Final integrated backend, 3 files | **84 passed, 0 failed/skipped**, 22.83s | 10 new identity cases + 63 Action lifecycle cases + 11 plugin settings cases; backend-integrated.xml.gz |
| Targeted Vitest, 2 files | **12 passed, 0 failed/skipped** | 9 ActionsView + 3 shared PluginSelection; frontend.xml.gz |
| Report Steer / Jobs / tool docs coexistence | **82 passed, 0 failed/skipped** | 42 report read/batch cases + 25 background Job API + 15 handbook/developer-help cases; coexistence.xml.gz |
| Targeted ESLint | exit 0 | ActionsView, its tests and production e2e script |
| Typecheck | exit 0 | pnpm exec tsc -b; also pre-commit React check passed |
| Production build | exit 0, **74 compressed assets** | pnpm build; build.log.gz |
| Real integrated HTTP + production Chromium | exit 0, zero page errors | runtime.json, browser.json, live-integrated.log.gz |
| Real stdio capability calls | **6 passed**, 2 per mode | API and fixed-argv command, all three generic modes; runtime.json |
| compileall / git diff --check | exit 0 | Actions package, MCP server and identity tests |

Identity regression cases cover forced collisions, bounded exhaustion with unchanged
bytes, retired-plugin avoidance, 24 concurrent same-name creates, rename with shared
references/release unchanged, explicit invalid IDs, optimistic conflict, creation
without IDs over HTTP, Agent disabled drafts and denied Agent build.
Frontend retains the original five test identities and adds creation-ID retention,
failed secondary refresh, malformed schema preservation and empty Session context.
No threshold, skip or xfail was changed.

Backend commands used `scripts/run_tests_isolated.py` and the existing task-local
pytest-timeout 2.4.0 installation at
`.validation/action-review/integration/pytest-plugins`, preserving timeout=300 and
per-test overrides. No shared environment/dependency installation was performed.
The 82 coexistence cases ran before the later baseline merge; their product paths
were not modified by this task. After merging the new baseline, the 84-case backend
selection and full owned HTTP/stdio/browser probe were rerun. Frontend/build inputs
did not change with that merge, so prior successful frontend checks were retained.

The integrated production browser proves all three sections, missing creation ID
inputs, server-generated Action/Group IDs, stable rename/update targets, Copy ID,
shared Echo references in two Groups, build, stable release on Group rename,
contextual Session selection, preservation of another Group binding, all three mode
selections, idle unbuild/delete and no horizontal document/panel overflow at
**390px and 320px**. Screenshots were visually inspected. Page uses real production
assets and real HTTP; no API interception or paid Worker was used. This is automated
browser evidence, not human acceptance.

The first two browser attempts exposed harness assumptions: a substring Build Group
locator also matched Unbuild Group, and Playwright check() required an immediate
state change although persistence is async. Exact locator plus click followed by
eventual checked assertion corrected those probes; production thresholds were not
relaxed. A later successful probe preceded the final integrated successful run.
Each owned server retired. Existing build chunk/import warnings remain in raw logs.

Reproduce from repo root:

```powershell
python scripts/run_tests_isolated.py -- python -c "import sys; sys.path.insert(0, '.validation/action-review/integration/pytest-plugins'); import pytest; raise SystemExit(pytest.main(['tests/test_action_ids.py','tests/test_action_groups.py','tests/test_plugin_session_config.py','-o','addopts=','-q']))"
python scripts/run_tests_isolated.py -- python scripts/verify_action_groups.py
```

From packages/web: `pnpm exec vitest run src/views/ActionsView.test.tsx
src/components/session/PluginSelection.test.tsx`, `pnpm exec eslint
src/views/ActionsView.tsx src/views/ActionsView.test.tsx e2e/action-groups.e2e.mjs`,
`pnpm exec tsc -b`, `pnpm build`. All commands finished under three minutes; no
long-command Job or active Job polling was needed.

## Boundaries and remaining review

Final owned HTTP PID **28744**, port **59732**, data root
`.validation/action-groups/live-5d56add2bb/data`; serverStopped=true, PID gone,
port released, no remaining owned service/provider processes. 8767 unused. 8768
was observed at PID 39736 initially and PID 2688 finally as MA separately advanced
the practical baseline; no command here operated that service. Final listener facts
are in checks.json. No push/release, paid model, subagent, shared dependency write,
existing service operation, main/practical movement or full suite.

Relative to baseline 4d3b1cf1, the source change is 10 files, 544 insertions and
146 deletions (diff-stat.txt / diff-name-status.txt). Action lifecycle reservations,
Worker, self_queue/report handoff, Jobs and web/server.py have no task delta against
that baseline. Exact source SHA-256s are recorded separately. Source tests do not
prove real paid CLI restart, POSIX process cleanup, crash recovery or multi-process
registry coordination. Those lifecycle boundaries remain as before. MA independent
source/visual acceptance and final fast-forward are pending; do not treat TA
completion as MA acceptance or synchronize the user skill from this TA.
