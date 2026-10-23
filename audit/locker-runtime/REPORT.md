# Locker production wiring repair candidate — 2026-10-23

TA delivery for MA review; not a claim of MA or human acceptance.

## Identity, ancestry and source

- Worktree: `D:/project/pan-worktrees/locker-runtime-wiring-20261023`.
- Branch: `fix/locker-runtime-wiring-20261023`.
- Base: `ddf146bcc36039e98776e359cbdf700c05d86a84`; fresh registered worktree was clean before edits. `git merge-base --is-ancestor 1ca7cbd3 HEAD` and the same command for `26ea1cb2` both exit 0.
- Exact tested source: **`980fae907a077476e193f1aa9dd556b0ea906a24`**. The delivery adds evidence/documentation only after this source commit. No product/test source changes after its verification.
- Independent defect report: `D:/project/pan-worktrees/job-inbox-independent-review-20261023/audit/independent-review-20261023/REPORT.md`.
- No original composition/core/integration/main/practical checkout or ref was moved by this task; no push/release/service operation. Main and practical were observed at `140455976b7c60097fafdd4dc3f8e3b18a699ccc` during final verification, not changed here.

## Implementation and compatibility decisions

| Area | Implementation | Evidence / limit |
| --- | --- | --- |
| F2 user authentication | `packages/interactions/api.py` and new `runtime.py` reuse the running host's Automation UserAuth. Its valid Bearer maps to profile-local `UserPrincipal('user')`. No new key, login, OAuth or body identity. | Missing/invalid Bearer 401; foreign read/write 404. Shared frontend Bearer GET and writes; 401 clears only the matching current token. |
| Frontend credential wiring | `lockerModel.ts`, `LockerView.tsx`, shared `JobUserAuthorization.tsx`. Existing memory login/logout, token change cancels stale refresh. Locker-specific explanation; no credential persistence. | Production browser logs in and submits choice; desktop/390/320px screenshots and no page errors. Existing Job authorization default stays compatible. |
| F3 directed dispatch | Host lifespan binds real schema to trusted core/pan handle. `runtime.py` rechecks Trigger enabled/revision/type/dependency under existing ledger writer coordination, then writes Event Store off-loop with only the selected Trigger target. New `EventStore.lookup` provides durable reconciliation. | Two enabled matching Triggers; exactly one selected Run. Stale revision/disable at admission rejects without Event. Existing Engine/Action/Pause gates execute, no new executor or Job wait/resume. |
| Response notification | Response and unique `response_outbox` intent share Card SQLite transaction. Existing SessionQueue gets fixed summary <=200 Unicode characters and stable QueueID. | Body/answer/attachments not forwarded. Queue commit followed by lost receipt reconciles after reopening, only one put. Accepted outbox payload cleared. |
| Reject/delete | Existing rejection semantics retained; shared notice delivery helper now handles both outboxes. Reject never produces choice Event. Delete inserts no new notice. | Browser rejects a card carrying a valid executable choice, then deletes it. Run count remains one; source Queue has exactly submitted + rejected notices. |
| Restart/dedup | Existing Event Store dispatcher and Event+Trigger Run identity; Card unknown/in_flight looks up original receipt only. Both notice outboxes recover at startup. | Product HTTP restart preserves accepted Card, one Run and two Queue notices. Old Bearer rejected, same temporary key re-login reaches same recipient. Lost Event receipt test reconciles without a second admission. |
| Old recipient migration | Existing `user` records are visible without rewrite. Other IDs remain intact and invisible; no default exposure, alias guessing or historical reassignment. Only additive response_outbox table. | Temp-copy test drops the new table to emulate old shape, verifies every old Card column unchanged and original file SHA unchanged. Unknown alias still invisible after copy upgrade/reopen. |
| Job / Agent Inbox | Existing registries, snapshot and queue gate paths unchanged. Agent Inbox shared consume remains separate from User Inbox read/unread. | Focused Job Action/Template/recovery/overlap/boundary/migration and Agent Inbox suite included; not a full repository run. |

Changed product files: `packages/automation/event_store.py`, `packages/interactions/{api,runtime,store}.py`, `packages/web/server.py`, `packages/web/src/components/automation/JobUserAuthorization.tsx`, `packages/web/src/views/{LockerView.tsx,lockerModel.ts}`. Added tests: `tests/test_interaction_runtime.py`, `tests/test_locker_runtime_http.py`, `packages/web/src/views/lockerModel.auth.test.ts`, `packages/web/e2e/locker-runtime.mjs`. Design, HTTP tool documentation and TESTING updated with the production mapping and remaining boundaries.

No cross-file atomicity is claimed. Card in_flight precedes Event admission; durable Event ID or QueueID is the recovery evidence. Missing/expired receipt remains unknown, never proof that an effect did not happen. An unavailable/stale reference stays blocked/diagnosable. Atomic admission uses existing Trigger writer lock on the I/O executor, never holds it across Action awaits or executes synchronous disk I/O on the event loop. Existing business Pause/Illegal/owner/generation/Group pin contracts are retained.

## Red / green evidence and exact commands

`auth-red.log/xml`: restored base `packages/interactions/api.py` temporarily and ran the new real auth fixture; exit **1**, actual 503 versus required 401 at the first no-token check. Restored repair immediately. `trigger-red.log/xml`: restored base `packages/web/server.py` temporarily and ran product HTTP fixture; exit **1**, real logged-in submission returned `unavailable` versus `accepted`. These are deliberately failing pre-repair evidence, not final failures. The fixtures were subsequently extended with browser submission and additional assertions; red files retain their original trace/line numbers.

Reproduce frozen-source green verification from the worktree:

```powershell
E:/software/miniforge/python.exe audit/locker-runtime/verify.py
```

This driver exited **0**. `commands.json` contains every exact argv, cwd, source SHA and exit code; UTF-8 logs and JUnit accompany it. It does not set PYTHONUTF8 or a global default encoding. Python children/browser logs explicitly decode/write UTF-8. No dependency install/upgrade; existing node_modules junction reused, build outputs stay in this tree.

| Command / selection | Result | Evidence |
| --- | --- | --- |
| `E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_interaction_runtime.py tests/test_interaction_cards.py tests/test_interaction_card_api.py tests/test_user_locker_lifecycle.py tests/test_agent_inbox.py tests/test_job_inbox_migration_drill.py tests/test_job_inbox_boundaries.py tests/test_job_action_templates.py tests/test_job_completion_recovery.py tests/test_job_system_actions.py tests/test_job_overlap.py tests/test_automation_retry_parallel.py -o addopts= -q --junitxml=audit/locker-runtime/python.xml` | exit 0; **184 passed**, no skips/failures; one existing Pydantic unresolved-forward-reference warning | `python.log`, `python.xml` |
| In packages/web: `pnpm exec vitest run views/LockerView.test.tsx views/lockerModel.auth.test.ts components/jobs/UnifiedJobForm.test.tsx components/jobs/JobTemplateEditor.test.tsx shortcuts/registry.test.ts --reporter=default --reporter=junit --outputFile=D:/project/pan-worktrees/locker-runtime-wiring-20261023/audit/locker-runtime/ui.xml` | exit 0; **25 passed**, no skips/failures | `ui.log`, `ui.xml` |
| In packages/web: `pnpm build` (tsc -b + Vite + compressed asset verifier) | exit 0; **92 compressed assets** verified; existing large-chunk warning retained | `build.log` |
| `E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_locker_runtime_http.py -o addopts= -q --junitxml=audit/locker-runtime/http.xml` | exit 0; **1 passed**, real HTTP + stdio MCP + production Chromium/fakeCLI | `http.log`, `http.xml`, `http/` |
| `E:/software/miniforge/python.exe -m compileall -q packages/interactions packages/automation/event_store.py tests/test_interaction_runtime.py tests/test_locker_runtime_http.py` | exit 0 | `static.json` |
| `git diff ddf146bcc36039e98776e359cbdf700c05d86a84 HEAD --check`; `git diff --check` | both exit 0 | `static.json`, `commands.json` |

An initial UI reporter relative path placed its JUnit beneath `packages/audit`; fixed to an absolute owned path and reran the frozen-source sequence. The stray generated file was removed; `ui.xml` here is the final 25-test result, not the earlier 24-test run.

## Real isolated chain and cleanup

`http/evidence.json`: owned random port **59927**, temporary profile, real HTTP launcher with empty gateway manifest and deterministic `tests/support/fake_stream_cli.py`. Real MCP `interaction_card(create)` → existing browser key login → select Pause choice/Submit → durable selected Event → Run `run_ef82bee4515e404d8a3acdafa27e67fa` succeeds → existing target Session queuePaused. A subsequent ordinary send stays queued, not delivered. Source Session is paused intentionally so its two durable notices remain inspectable; this proves Queue enqueue, not source Worker consumption.

Production Chromium **153.0.8010.12** verified 1280/390/320px with no horizontal document overflow and no page errors; screenshots and `http/browser.json` retained. No browser network interception or synthetic principal/Trigger is used in this acceptance fixture. The local CLI/provider is fake throughout.

Both owned service generations and children were tracked by PID + creation time and cleaned in finally. Final `ownedProcessesRemaining=[]`, `portReleased=true`. Fixture refuses 8767/8768 and uses only its temporary data/config/empty gateway manifest. Temporary profile is pytest-owned; never copied into a real profile. No real provider/account/QQ/user Worker was accessed. Test secrets remain transient and are absent from tracked evidence.

## Remaining / deliberately unverified matrix

| Boundary | Status / next action |
| --- | --- |
| F2 real single-profile login + F3 actual selected Trigger engine | Implemented and verified on owned product runtime; awaits independent MA review. |
| Other historical recipient aliases | Preserved and denied; no credible ownership mapping is assumed. Explicit authorized mapping/migration tool would be a separate task. |
| Real user-data migration / deployed service / paid provider / actual user's sessions | Not performed. Temporary-copy and fakeCLI evidence only. |
| Unknown delivery without retained receipt | Remains unknown with no automatic resubmission; deliberate safety boundary. No new reset/force/replay API. |
| Job wait/continue/approval, expanded auth/tenant system, new OAuth/exporter | Not implemented or connected; outside authorization. |
| Source Session actually consumes notices | Not tested here; source paused for durable Queue inspection. Existing queue gates/tests retained. |
| Full repository tests, real QQ, overall human UX acceptance | Not claimed. Targeted tests/build/browser only; overall human acceptance pending. |

Stop after this clean candidate; MA independently reviews/accepts before any integration or deployment.

Readable evidence copies normalize trailing whitespace only; `raw-evidence.zip` retains the original byte-exact red logs/JUnit and build log.
