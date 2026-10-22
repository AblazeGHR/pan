# Unified Job / Agent Inbox compatibility follow-up

- Tree: `D:/project/pan-worktrees/job-unified-inbox-followup-20261023`
- Branch: `feature/job-unified-inbox-followup-20261023`
- Job base: `886842338c623b73bac8f16a12f4675879664674`
- Inbox/Locker input: `7446efcb55545be35e76b4845941991202b8cd93`
- Composition merge: `32bdba7a` (both candidates retained as ancestors).
- **Tested source: `16429246ac1527ae89f3b6948407a527fd29f729`.**
- Final handoff adds evidence/docs only. No main/practical/integration movement,
  push/release, production service or protected-port operation.

[Changed files after composition](files.txt), [machine-readable validation](validation.json).

## Fixed boundaries and evidence

| Boundary | Implementation | Verification |
|---|---|---|
| Dirty Job + remote WS config revision | `JobsView.tsx` freezes the opened record with its CAS revision. WS refresh changes the list, never the draft's revision. Backend409 keeps user inputs; reopen is deliberate reload | `JobsView.bulk-search.test.tsx`: before saved revision8 from draft7, after saves7; production Chromium performs a real HTTP update/WS refresh and verifies409 + retained text |
| Template capability vs copied Job execution | `JobTemplateEditor.tsx` independently edits outer enabled and config.enabled. An enabled fixed Template can create disabled Jobs. Delete/new resets capability false | Component regression + actual HTTP instantiate returns disabled copy |
| Agent draft nested enable | `action_templates.py` rejects Agent writes if either Template enabled or config.enabled is true. Existing disabled drafts remain editable under normal schema/revision gates | `test_job_inbox_boundaries.py`; real unauthenticated Job create and nested-enabled Template both401, authorized fixed instantiate works |
| Unknown instantiate and new revision | Unknown outcome retains the key; confirmed revision save rotates it, avoiding reuse of an old request fingerprint | `JobTemplateEditor.test.tsx`: unknown retries same key, confirmed revision8 uses a new key |
| Missing Action repair | `UnifiedJobForm.tsx` preserves missing ID as an option and shows exact `/config/actionId` location | Component regression; existing backend Action deletion/repair/cleanup tests retained |
| Legacy schedule/permission migration | `jobs/unified.py` emits disabled review config with typed schedule container, no record write. Known interval/weekly/absolute once map conservatively; uncertain relative dates/seconds/unknown shapes report issues | Negative/positive migration tests; old-kind API and message execution tests |
| Ambiguous migration fail closed | Invalid projections use schedule+empty specs, which new Job admission rejects; they never invent immediate. Legacy top-level status/fields and editor remain authoritative | Negative matrix asserts diagnostics, disabled flag, invalid schedule contract and unchanged original bytes |
| Job result / Locker consume separation | Existing result `enqueue_notice` path and per-Attempt event ID stay separate from Inbox fixed source/Queue IDs and shared consumption | Actual one-Queue HTTP: full Locker message consumed once, notification keeps body private and unconsumed, explicit silent-message consume leaves Job notice untouched |
| Permissions and handoff | Inbox MCP self/managed does not grant Job enable/instantiate/recovery. Existing local HTTP Inbox trust boundary remains. Pause blocks actual handoff while durable Queue acceptance remains allowed | MCP denial regression, HTTP401 and paused/unpaused fakeCLI receipt checks; existing source metadata tests |
| Readonly boundary | Trusted channel/system notifications may persist, matching existing contract; automatic Force remains blocked. No new readonly admission rejection is shipped | Deterministic test verifies both accepted Queue rows and no auto-Force task |
| Legacy notification callback from a thread | `scheduler/api.py` closes an awaitable when no event loop exists instead of leaking an unawaited coroutine; durable strict Job notification remains unchanged | Deterministic coroutine lifecycle test; no parallel notification registry |

The merge preserved both HTTP guide additions. The test conflict in
`test_agent_notify.py` preserved the real ordered-writer spy/assertion shared by
both candidates. No product gate was removed to resolve a conflict.

## Contract and migration notes

`unified.compatibility` adds `reviewRequired:true` and `migrationIssues:string[]`.
For legacy records `unified.config.enabled` is always false; actual execution
state remains the original top-level Job enabled/status fields. This projection
is an explicit review aid, not an executable automatic migration or a copied
historical snapshot. The old API/kind/editor remains available. A new reference
Job must still pass full Action schema and protected enable/repair validation.

Weekly weekday0(Monday)..6(Sunday) maps to cron1..0, retaining timezone. Nonzero
weekly seconds cannot be represented exactly and are rejected for projection.
Relative once uses persisted nextRunAt only; missing occurrence does not get a
new current-time deadline. Protected main-lifecycle is not a generic Job migration.
Unknown fields/shapes have diagnostics and cannot accidentally become immediate.
Run identity projects lastExecution.runId rather than an Attempt execution ID.

Job result metadata is **not** routed into Agent Inbox and does not change shared
Locker consumption. Full Inbox mode still accepts on durable Queue commit, not
CLI consumption. Notification carries identity/status only. IDs, dedup, unknown,
Pause/Illegal/lease/owner/generation and existing auto-Force policy remain in their
existing layers. No account/provider/credential/exporter interface is connected.

## Exact commands and results

All Python commands run from the new worktree with existing
`E:/software/miniforge/python.exe`. No full-repository suite was run. Frontend cwd
is `packages/web`. Dependencies are reused through package junctions in an owned
node_modules directory; writable Vite caches stay in this tree, no install/update.

**327 Python passed, exit0; no failures/errors/skips.**

```powershell
E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_job_inbox_boundaries.py tests/test_job_unified_contract.py tests/test_job_action_templates.py tests/test_job_system_actions.py tests/test_job_completion_recovery.py tests/test_job_overlap.py tests/test_job_mcp_config.py tests/test_jobs_api.py tests/test_session_message_jobs.py tests/test_agent_inbox.py tests/test_agent_notify.py tests/test_source_metadata.py tests/test_scheduler_dispatch.py tests/test_scheduler_mcp.py tests/test_action_groups.py -o addopts= -q --junitxml=artifacts/job-inbox-followup/python.xml
```

[JUnit](python.xml), [log](python.log). One existing Pydantic incomplete-field
warning remains and is not a skip or suppressed failure.

**3 real isolated HTTP tests passed, exit0.**

```powershell
E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_job_inbox_http.py tests/test_agent_inbox_http.py tests/test_job_action_http.py -o addopts= -q --junitxml=artifacts/job-inbox-followup/http.xml
```

[JUnit](http.xml), [log](http.log), [combined instance/Queue/cleanup receipt](http/evidence.json),
[browser receipt](http/browser.json), [browser command log](http/browser.log).
Actual production Chromium executes new Job, remote config conflict/dirty draft,
independent Template grant/instantiate and desktop1280/390/320px layouts. No HTTP
route mocks. The companion Inbox fixture exercises actual MCP stdio and fake CLI;
the Job companion covers reference/snapshot/recovery/permissions.

**65 Vitest tests passed, exit0.**

```powershell
pnpm exec vitest run components/jobs/UnifiedJobForm.test.tsx components/jobs/JobTemplateEditor.test.tsx views/JobsView.bulk-search.test.tsx views/JobsView.schedule-shapes.test.tsx stores/workerStore.test.ts components/jobs/NewJobForm.selected-broadcast.test.tsx components/jobs/NewJobForm.resume-legal-running.test.tsx components/jobs/NewJobForm.scheduled-qq.test.tsx --reporter=junit --outputFile=../../../artifacts/job-inbox-followup/ui.xml
```

[JUnit](ui.xml), [log](ui.log). `pnpm exec tsc -b` exit0; `pnpm build` exit0,
84 precompressed assets verified ([build log](build.log)); standard chunk-size/
mixed-import warnings remain. `git diff --check -- packages tests docs` exit0.
The build ran on cb47b91a; the final source commit only tightened Python migration
projection. `git diff cb47b91a 16429246 -- packages/web` is empty, proving the exact
built frontend source is unchanged. Python/HTTP/Vitest final runs are on16429246.
Raw pytest evidence retains tool-emitted trailing whitespace.

## Failure experiments retained

- [before.xml](before.xml)/[log](before.log):6 pre-fix failures reproduce nested
  Agent enable and unsafe/untyped legacy projection. Working source experiment,
  not a claim of a clean tested baseline.
- [ui-before.xml](ui-before.xml): dirty revision test fails8 vs7 before fix.
- [readonly-before.log](readonly-before.log): an initial investigation expected
  readonly to reject Queue admission. Current code inspection and comparison show
  trusted ingress admission is allowed, while Force/handoff are gated. The
  experimental rejection was removed; final regression checks original semantics.
- `failed-formatted-receipt/`: first combined HTTP assertion used unformatted
  equality for a QQ-formatted CLI receipt. Saved CLI evidence showed both expected
  deliveries. Final assertion verifies expected body plus QQ source framing;
  body privacy assertions use substring detection too. This was a harness error.
- `failed-browser-scope/`: a new click locator collided with sidebar preview text;
  final browser targets the actual Jobs list. Screenshot/log/cleanup retained.
- Initial new-tree merge hook lacked reused dependencies; after local dependency
  setup the real TypeScript hook passed. A test-only unsupported role query option
  was also corrected; no typecheck, test or assertion was disabled.

## Boundaries and cleanup

No automatic in-place user-profile migration or mixed-version writer support is
added. Unknown receipts still require existing explicit reconciliation; this
batch does not introduce a force-replay API. Existing ledger capacity/archive
boundaries from88684233 remain. No dedicated Inbox/Locker UI, private visibility
model, real QQ/WeChat transport migration, real provider/model/account effect,
OAuth/keyring/private exporter or production service restart was tested/added.

The combined harness uses owned random non8767/8768 port, temporary data/profile,
catalog with an explicitly empty gateway manifest, deterministic fake CLI, and
records process/root identities. It reports zero owned processes remaining and
released port. Chromium closes in finally. Test-generated legacy tracked evidence
is copied here then restored. Original core tree remains at88684233 and clean.
Overall human acceptance and MA independent review remain pending. Stop here.
