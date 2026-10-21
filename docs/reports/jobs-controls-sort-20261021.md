# Job controls, sorting, labels and explicit background names

Candidate for MA acceptance; not merged, pushed or deployed.

- Worktree: `D:/project/pan-worktrees/jobs-controls-sort-20261021`
- Branch: `feature/jobs-controls-sort-20261021`; base `1e74c3742561ce28b5b2385d4a94bcc430bf988b`.
- Initial worktree was clean. Existing protected listener: 8768 / PID 33556 / Python314 python.exe; no 8767 listener. Neither was operated. No subdelegation or paid model invocation.

Source facts and resulting behavior:

- Before this change, `JobsView` row actions and `JobDetailDrawer` exposed Delete but no Job cancellation. Dialog dismissal buttons called Cancel, but did not cancel Jobs. Cancellation already exists at `POST /api/background-jobs/{id}/cancel`; the new frontend API calls that endpoint, then reads the unified Job view. DELETE remains independent.
- Pending, scheduled, starting and running non-`scheduled-task` Jobs expose a visible **Cancel job** button in the row and drawer. Terminal Jobs do not. Scheduled task definitions retain Pause/Resume; their actual process executions are separate background-process Jobs. Request failures retain the Job and report the backend error. In-flight and accepted asynchronous cancellation remain disabled until a terminal live update; no polling was added. An accepted cancel followed by a failed status read reports the uncertainty explicitly.
- Sort choices are Active first (previous default), Updated time, Created time and Kind. The last three support ascending/descending. Pin priority remains first; explicit sorts do not keep the active-status ranking ahead of the selected key. Equal values use Job ID, independent of incoming list/event order. Invalid/missing timestamps stay last in either direction; numeric epoch seconds and ISO strings are supported. `pan:jobsSort` uses guarded localStorage reads/writes, consistent with existing UI settings conventions.
- `kind` is the actual Job classification. `source.type` describes the creator and is not used as Job type. No invented type field or classification was introduced.
- Optional label is a distinct accent badge immediately to the right of the title in list and drawer. It shrinks/truncates with a native title tooltip. Status/kind moved to the secondary row to preserve title, label and action space on mobile. Missing label renders no badge. Existing search, filters, selection, runs pagination and unified live store paths remain in place.

Naming contract and audited boundaries:

- Direct user override received in the TA conversation: “但是用户前端允许不填写job name，空的时候按照现有逻辑自动生成job name”. It arrived after the forwarded instruction requiring name. This is the source of the candidate behavior; the MA later reported that this direct override was not visible in its context. The frontend **may leave Name blank**. `/api/jobs` retains the existing automatic `job-N` algorithm before invoking process creation. The existing NewJobForm Name entry remains optional; explicit names over 200 trimmed characters are rejected.
- `agent_background_start` now has required keyword `name`, optional `label`; its actual MCP tool schema is tested. Name must be a nonblank string of at most 200 trimmed characters. It forwards to `/api/background-jobs`, which now forwards name to the process registry. The core process start and independent service reject missing/blank/overlong new names before spawning. Label remains optional.
- Production process callers audited: unified Job API, legacy background HTTP API, core retry, scheduled-shell child creation, service lifecycle creation, MCP start and the navigation fixture. Retry explicitly preserves the previous displayed name (bounded for the new record); old independent payload replay is checked before validating a new insertion. New lifecycle Jobs use operation/port names. Scheduled-shell children already have parent-derived meaningful names.
- Session-message/broadcast scheduling APIs, scheduler task definitions and templates are a separate scheduling boundary: their existing optional/automatic names and signatures are retained. They are not routed through the ordinary process start contract. No broad removal of automatic names was made. Existing persisted Job reads and names are unchanged, including overlong historical names; no migration or registry rewrite was added.
- Repository skill source and HTTP reference are updated. **MA sync item:** copy the reviewed source changes to the user-level `C:/Users/14709/.agents/skills/pan/` installation and any configured skill copies after acceptance. This candidate does not mutate those external copies.

Validation (all commands run in this worktree; frontend commands use `packages/web`):

```text
pnpm exec vitest run src/views/JobsView.bulk-search.test.tsx src/views/JobsView.schedule-shapes.test.tsx src/components/jobs/JobDetailDrawer.test.tsx src/components/jobs/NewJobForm.window.test.tsx src/stores/jobStore.test.ts
58 passed (5 files)

pnpm exec tsc -b
passed

pnpm exec eslint src/views/JobsView.tsx src/views/JobsView.bulk-search.test.tsx src/components/jobs/JobDetailDrawer.tsx src/components/jobs/NewJobForm.tsx src/components/jobs/NewJobForm.window.test.tsx src/services/api.ts
passed, no warnings

pnpm build
passed; 72 compressed production assets verified

python scripts/run_tests_isolated.py -- python -m pytest tests/test_background_job_api.py tests/test_background_jobs.py tests/test_independent_job_service.py tests/test_independent_job_bridge.py tests/test_jobs_api.py tests/test_independent_job_deployment.py tests/test_job_runner_venv.py tests/test_job_window_policy.py tests/test_job_lifecycle_prevention.py -o addopts= -q --junitxml=evidence/jobs-controls-sort/backend-final.xml
231 passed, 1 skipped

python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_hidden_production.py tests/test_job_cleanup_failures.py -o addopts= -q --junitxml=evidence/jobs-controls-sort/cleanup-final.xml
27 passed

python scripts/run_tests_isolated.py -- python -m pytest tests/test_background_job_api.py -o addopts= -q --junitxml=evidence/jobs-controls-sort/schema-final.xml
25 passed (includes final actual public-schema regression; overlaps the main run)

node packages/web/e2e/jobs-controls-production.e2e.mjs
passed against final dist; owned ephemeral HTTP port 63455; server/browser closed

git diff --check
passed
```

Evidence is in `evidence/jobs-controls-sort/`: final logs/JUnit, browser-result.json and four screenshots. Production Chromium loads the real built app with isolated HTTP and WebSocket fixtures: cancellation failure/success/in-flight feedback, zero DELETEs, terminal entry removal, updated sort/direction persistence, live event reorder, and same-row long-label truncation/no horizontal overflow at 1280/375/320 pixels. A running Job at 320 pixels is also captured. No page errors occurred.

Real process evidence is separate from browser fixtures: `test_cancel_proves_and_terminates_the_owned_process_tree` creates and cancels its own temporary independent Job via the real HTTP service, with process-tree termination checks; lifecycle/launcher tests likewise own their temporary roots and processes. No real user Job was cancelled. The browser fixture is not an end-to-end cancellation against a running full Pan instance.

Limits: no full suite, protected service, release, push, merge or interactive human acceptance. One existing test is skipped because registering a temporary Windows task requires explicit opt-in (`test_external_user_host_survives_real_pan_graceful_and_job_tree_exit`). Pytest reports existing timeout-plugin, Pydantic forward-reference and Starlette/httpx warnings. Build retains existing dynamic-import/chunk-size warnings. Dependencies were reused through a local node_modules junction; no dependency installation or lockfile update was performed.


## MA baseline integration and naming conflict follow-up

MA requested integration of `69ca7574912f57f4b8cc63e2f57a119962ab7a0a`. It was merged into this isolated branch as `24203a13` without conflicts. `69ca7574` is an ancestor; Action implementation remains identical to that baseline and Jobs product implementation remains identical to `02014ac6`. Task A API/skill additions remain present.

The TA did receive a direct user override that MA apparently did not see: **“但是用户前端允许不填写job name，空的时候按照现有逻辑自动生成job name”**. This was a standalone user-role message between the forwarded naming assignment and the TA response acknowledging optional frontend Name. It was not inferred from code or a skill. Per the current MA instruction to report direct override evidence before reversing behavior, this follow-up does **not** change the frontend to required Name or tighten `/api/jobs` against blank Name. Naming policy therefore remains explicitly pending reconciliation with MA/user, and the candidate is not marked accepted. MCP/process-name requirements remain as previously implemented.

Post-merge targeted coexistence validation:

- Frontend: ActionsView, PluginSelection, JobsView search/bulk/schedule, JobDetailDrawer, NewJobForm window and jobStore: **66 passed / 7 files**.
- `python scripts/run_tests_isolated.py -- python -m pytest tests/test_action_groups.py tests/test_background_job_api.py tests/test_jobs_api.py -o addopts= -q`: **141 passed**; JUnit/log `coexistence.*`.
- Report subscription/agent subscription: **41 passed**; `task-a-coexistence.*`.
- `tests/test_report_reads.py`: passed; exact counts in `task-a-reads.*`.
- Build (includes TypeScript) and **74** compressed-asset validation: passed; `coexistence-build.log`. The previous report said 72; that was a summary error. The committed log contains ActionsView and reports 74. Existing bundle warnings remain.
- Scoped ESLint on JobsView, ActionsView, shared API and JobDetailDrawer: passed.
- Final production Jobs browser: passed again at owned ephemeral port **51977**, zero page errors, Cancel POSTs 2, DELETEs 0, desktop/mobile layouts and live updates verified. Fixture server/browser closed. Action browser against a real HTTP instance was not rerun in this follow-up; Action coexistence is covered by targeted backend/UI tests and build.
- No main/practical update, push, release, protected-service operation, full suite or subdelegation. No naming behavior was silently reversed.


## Final integration: report_consume baseline f576d1b7

User/MA has now accepted the naming policy: frontend Name remains optional with existing automatic naming; MCP background start requires name; label remains optional. No reverse naming change is requested or made. Earlier wording that naming awaited reconciliation is historical and superseded by this confirmation. Candidate acceptance remains MA's separate decision.

`f576d1b74b566f2d78531207cadc8b7542004a25` was merged into this isolated branch as `2d72718b` without conflicts. Auto-merged shared files were the Pan skill, HTTP reference, MCP server and web server. Review confirms:

- MCP `report_consume` and HTTP report-consume are present, report_next is removed as a callable tool/operation, and report bodies are withheld from self_queue_read/consume responses until the actual CLI handoff path. References to report_next only describe its removal.
- Action admission/batch ownership fences from the new baseline remain intact. Jobs creation/cancellation/sorting/label code is unchanged. Background process creation, retry and registry sources are byte-identical to the preceding candidate; shared MCP/web-server changes are limited to report handling and preserve name forwarding.
- Skill content contains Action/Group management, the new report_consume and metadata-only privacy rules, and required MCP background name/optional frontend name and label guidance. No side's skill content was discarded.
- New report tests explicitly include a `background_job_terminal` notice alongside reports; Job/notice/channel items are excluded from report batches and retain their non-report behavior. No Job creation path calls report_consume. Real HTTP/MCP tests exercise the newly registered interface and removed old interface.

Current targeted validation, **not the historical 23-test report reads evidence**:

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_report_reads.py tests/test_report_action_coexistence.py tests/test_self_queue.py tests/test_background_job_api.py tests/test_jobs_api.py -o addopts= -q --junitxml=evidence/jobs-controls-sort/consume-final.xml
175 passed; no skipped tests
pnpm exec tsc -b --noEmit   (packages/web)
passed
node e2e/verify-precompressed-assets.mjs   (packages/web)
Verified 74 compressed production assets
```

Build provenance correction and rebuild decision: initial pre-Action Jobs build correctly had 72 variants. The **post-69ca7574 build log already reports 74**, including `ActionsView-Dsc_BOkL.js`; the previous summary incorrectly carried forward 72. The final report-consume merge changes no frontend/build inputs. Git object IDs of `packages/web/src`, vite.config.ts, package.json, lockfile and tsconfigs were compared against 31dcf17e and match. Current dist's complete 49-file JS/CSS set equals the post-Action log; 37 files are at least 1024 bytes, each with Brotli and gzip, giving 74 checked variants. Every compressed variant was decompressed and compared with the actual plain output by the verifier. Actions route `/actions` and builtin entry exist in source and the Actions chunk exists in dist. `dist-provenance.json` records input Git objects and SHA256 of all current JS/CSS outputs.

This round **did not produce a new build or rerun unchanged frontend/browser layers**. It verifies that the existing post-Action build has identical frontend inputs to the final integration; it does not mislabel the original pre-Action 72-variant dist as a final fresh build. New behavior evidence is the 175-case current backend suite, not the earlier report-read result. Existing pytest timeout/Pydantic/Starlette and record_property warnings are recorded in consume-final.log.

No main/practical movement, push, release, existing-service operation, protected-port access, paid model, full suite or subdelegation. User-level skill copies remain an MA sync item after candidate acceptance.
