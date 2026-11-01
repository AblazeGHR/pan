# JOB-RETIRE revision13 — frontend caller boundaries

This bounded candidate repairs loss of multiline literal strings in the active canonical editor and proves its current assign/process request shapes. **Legacy simple assign/shell form wiring remains open.** No new canonical or legacy backend route, dispatcher, publication, permission rule or provider was introduced. No original 43-node or remaining-nine status is newly closed by these frontend fixtures.

Baseline: `d0b8f821319863c3ee0c2fcd3ed1a2c667ede407`. Worktree: `D:/project/pan-worktrees/job-retirement-closure-20261027`; branch: `r6/retirement-parity-acceptance-20261029-a`. Tested source: **`d3e2e9cf33d78624c4b5631152b872014da369ba`**. The later evidence-only commit does not alter tested sources. Prior r6–r12 artifacts and mappings remain unchanged.

## Actual callers and why automatic old-form mapping stops here

| Entry | Current chain | Disposition |
|---|---|---|
| Active canonical Jobs editor | `CanonicalJobsView` → `CanonicalDefinitionFields` → catalog-issued immutable Action ref and parameter bindings → `canonicalJobs.write` → `POST /api/jobs` | Existing assign/process path verified at React request boundary; no new execution behavior |
| Canonical time Template | `TimeTemplateFields` embeds the same Action chain; `instantiateTimeTemplate` posts revision/requestKey/activate | Existing disabled-by-default activation and unsupported error surface verified with fixture responses |
| Old `NewJobForm` | Only production importer is `SelectedSessionActions`; rendered with `broadcastTargets`, whose handler ignores input and reports migration | No live assign/shell caller to reconnect in this component; old scheduled shape includes `kind`, `target`, `action`, `schedule` and cannot be posted as a canonical definition |
| `JobTemplateEditor` / `UnifiedJobForm` | Imports from their own tests only; editor uses old `/api/jobs/job-templates` writes | Orphaned old form, not an active canonical creation path. Reintroducing it unchanged would restore an obsolete API |
| r11 `normalize_legacy_form_fields` | Defined in `final_service.py`, no production call sites | Python helper is not an HTTP operation. Its direct tests do not establish a frontend connection |
| No-Creator default Template | `FinalService.instantiate_time_template`: empty `composition.creators` raises `capability_unsupported` | Explicit design boundary preserved; frontend does not invent a no-Creator operation or hide the rejection |

The r11 helper’s owned queue/process tests establish mapper + provider capability **when called directly**. They do not establish production caller wiring. Its old matrix’s arrow must be read with this limitation. Likewise, generic canonical editing does not preserve the simplicity of the old field/default-template user entry.

For shell `command`, the helper chooses a shell on the execution host. A browser does not have authoritative execution-host OS information; converting it to local `cmd` or `/bin/sh` in React would guess semantics. The current published process schema is argv/cwd/hideWindow, not command. The safe existing argv editor remains available.

Minimal next coordination scopes, not implemented here:

1. Decide how an old simple/default form obtains a complete passive Template including a Creator, or resolve the no-Creator decision first. Exact backend boundary: `FinalService.instantiate_time_template` empty-Creator guard; frontend composition boundary: `TimeTemplateFields` / `CanonicalCollection` in `CanonicalJobsView`. No relaxation is proposed in this candidate.
2. If retaining command-string input, coordinate a narrow server-side canonical Template/form normalization call to the existing `normalize_legacy_form_fields` before definition persistence. Choose the exact existing canonical write/instantiate boundary with MA; do not add a second client shell converter or reopen a generic dispatcher.
3. Separately, `SelectedSessionActions.broadcast` closes its modal with `setPanel(null)` before putting the migration message into modal-only error UI. This broadcast issue is outside assign/shell and was read only.

## Minimal UI repair and exact tests

`CanonicalDefinitionFields.tsx::TypedValue` now uses an explicitly labelled textarea for string literals, retaining the same labels, values and `onChange` string binding. Number/boolean/array/object controls and API envelopes are unchanged. A single-line input strips line breaks before React emits a value; the preserved initial log shows `"  原文\ntext  "` became `"  原文text  "`. The new assign assertion is unchanged by the repair.

`initial-caller-tests.log` contains the first run: 3 passed / 2 failed, exit 1. One failure was this actual newline loss; the other was an owned test query matching two identical validation messages. The query was corrected to assert both messages, without product or validation changes. This log predates the source commit; it is not claimed as an immutable baseline-suite run. An intermediate Chromium check also exposed unstable implicit-label lookup after a textarea update; the final control has the existing label as `aria-label` and the browser check passes.

Dedicated file: `packages/web/src/views/CanonicalJobsView.retirement-r13.test.tsx` (five tests):

- `existing assign caller writes exact literal fields and immutable Action reference to a passive canonical Job`: sessionId, multiline raw text, source, sourceSessionId, taskId, fixed catalog reference/completion, expectedRevision=0/requestKey, no Run request.
- `existing process caller preserves argv boundaries/cwd/hideWindow without guessing command-string shell syntax`: exact argv elements including spaces/quotes/non-ASCII, raw cwd, bool, fixed catalog reference/completion, no command conversion or early request.
- `missing Action selection rejects locally without a legacy fallback write`: two missing references, zero writes.
- `server rejection preserves the structured draft without retrying a legacy endpoint`: fixture 409, retained draft, one request.
- `no-Creator Template exposes structured unsupported and keeps activation false without a fallback or Run request`: fixture 422, visible existing error, original Template revision, activate=false, one instantiate request.

The source also includes an owned browser entry and runner using the actual `CanonicalJobsView`, intercepted fixture HTTP replies, no proxy, and a randomly reserved loopback port with strict binding. This is not a provider or live backend proof. The harness is not added to production routes or the production build entry.

## Immutable verification

Reproduce from repository root (dependencies must already exist):

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe evidence/job-retirement-rev13-20261029/run_matrix.py
```

The runner checks tracked sources are clean, records HEAD, and serially runs each command through `scripts/run_tests_isolated.py`. All checks completed well under three minutes; no persistent long Job was needed. Exact argv, versions, exit and durations: `evidence/job-retirement-rev13-20261029/commands.json`.

| Check at source SHA above | Actual result | Exit |
|---|---|---|
| Dedicated five + CanonicalJobsView / CanonicalForms / old Template/form/window / selected-session regressions | 7 files; **59 passed, 0 failed, 0 errors, 0 skipped**; 4.300 s | 0 |
| `pnpm --dir packages/web build` | `tsc -b`, Vite production build, **88 compressed assets verified**; 22.499 s | 0 |
| `node packages/web/e2e/retirement-r13-callers.mjs` | **3 scenario groups passed**, 0 page errors, exactly two passive Job writes plus one unsupported Template request; 2.894 s | 0 |
| Normal source commit hook | React TypeScript check passed | 0 |

Interpreter `D:/project/Pan-main/.venv/Scripts/python.exe`: Python **3.14.5**; Node **v24.22.1**; pnpm **9.7.0**; Vitest **2.1.9**; Chromium **153.0.8010.12**. Existing Pan-main `node_modules` was used through a temporary junction; no packages were installed. Vite warns about existing large chunks/static-and-dynamic imports; build succeeds. Browser log contains Babel’s dependency-size note, not a page error.

`react.xml` lists every exact test ID. `react.log`, `build.log`, `browser.log` retain final raw output. `parity-matrix.json` records exact new assertions and open M01/M05/M07 boundaries. The 59 count includes the dedicated five; browser groups are separate and are not added to the unit count.

## Cleanup and acceptance limits

Browser and owned Vite HTTP server close in `finally`; final port was **56903**. The runner uses no Pan API proxy, real profile or account. The temporary dependency junction was removed after verification; generated ignored build/cache files are local evidence products, not source changes. Resource cleanup is recorded separately in `cleanup.json`.

No backend/product publication or shared fixtures changed; no legacy host was deleted. Actual provider delivery, trusted caller authorization enforcement, backend CAS enforcement, process launch/cleanup and real migration were not rerun in this frontend-only batch. Fixture CAS/unsupported replies verify only the caller response handling. Chromium uses Vite-transformed source; production bundle interaction remains unverified even though the production build passed. No remote CI, deployment or live services were operated; main/practical were not moved and nothing was pushed.

M01/M05/M07 overall acceptance remains open for legacy simple/default entry compatibility. Prior accepted QQ, Session, recovery and mapper evidence is retained as bounded historical input, not newly rerun or expanded. Monitoring/stable-reset, unknown fallback/clear-target, real migration and all no-Creator decisions remain open. MA review/integration is still required.
