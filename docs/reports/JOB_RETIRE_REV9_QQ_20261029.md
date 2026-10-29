# JOB-RETIRE revision9 — QQ retained behavior and trusted retry boundary

Accepted baseline `93fc1d931c2754565d8667fe41f7c036fe7c1ec0` was reached by normal fast-forward from clean `15fdb606e5c5c13a2d36ec81d3ac5c62ac376224` in the existing worktree/branch. Immutable tested source: `0a9e00e74900ef33c3c24d9e7b5f27b085d4f3a5`. This batch preserves D1–D6 and the v2 minimal migration plan. The r8 remote acceptance is bounded historical input; this revision did not operate remote CI.

The successful original QQ path and ambiguous acknowledgement safety are now exercised against an owned real OneBot HTTP fixture. **M08 manual no-send retry remains open, with two exact red tests.** Producer scope coordination is pending; this candidate does not claim complete revision9 or whole migration acceptance.

## Source changes and publication status

Only two product methods changed, 22 added lines:

- `packages/actions/qq_channel_provider.py::QQChannelProvider.receipt_outcome` (new pure classification helper): require an actual nonempty string/integer original `message_id` for successful QQ acknowledgement. `send_failed`, connection/timeout/invalid or missing acknowledgement remain unknown. Existing preflight error observations may remain rejected; an error code **never** becomes no-send proof or retry authority.
- `packages/actions/typed_executor.py::TypedExecutor._channel`: use that classification only for the exact QQChannelProvider implementation; preserve the original raw plugin receipt under the existing canonical effect receipt. Arbitrary/custom provider result handling is unchanged.

`FinalService._retry_boundary`, all generic safety/permission/owner/CAS gates, product publications, Action retry declarations, schemas and frozen plans are unchanged. No process cleanupProof is fabricated. The existing product declaration's sameKeySupported field is not evidence of remote QQ deduplication; no effect key is sent to OneBot and no remote query protocol is introduced. No automatic QQ retry is added.

Source diff: [product.diff](../../evidence/job-retirement-rev9-20261029/product.diff). New exact tests: [test_retirement_rev9_qq_http.py](../../tests/test_retirement_rev9_qq_http.py).

## Old chain and the exact remaining conflict

The original `43655680f030765de5bce5ea5b675c7a0e2672d5` backlog test uses `_redeliver_undelivered` with QQ and no Session target, invokes the registered sender with fixed target/text/bot, removes a delivered note, and records the returned message identity. Original source excerpts/hashes are retained in [original-source.json](../../evidence/job-retirement-rev9-20261029/original-source.json). That old retry behavior is the retained user capability; its `undeliveredFires` storage is an internal observation, not a reason to restore the old execution engine.

Current chain: immutable product_channel Action → canonical Run/Step/Attempt/effect → TypedExecutor._channel → QQChannelProvider.send → server._send_scheduled_qq → server._qq_plugin_post(`/api/qq/send`) → plugin.api_send → selected/default OneBotChannel.send → actual NoneBot Bot/Adapter HTTP client. The fixture replaces only the remote HTTP endpoint and channel configuration. It does not replace sender/provider/channel.send/Bot.call_api. Synthetic bot IDs are test identities with no real account or gateway.

Local code paths that can establish no send **at their original execution point**:

| Producer/location | What source proves | What persisted current response proves |
|---|---|---|
| QQ adapter argument validation before awaiting sender | No sender call for invalid arguments | No trusted identity-bound plugin retry proof; normal write/runtime validators already own these invalid inputs |
| `_send_scheduled_qq` invalid target-type return | No plugin POST at that return point | Canonical typed validation normally prevents this; not a plugin recovery source |
| `plugin.api_send` unknown selected bot before `ch.send` | No channel invocation at that return point | Raw `{ok:false,error}` only; no producer-stage/request identity |
| `OneBotChannel.send` invalid target type/int conversion/empty text before resolve/call | No call_api at those exact returns | Raw code/message only, insufficient for retry authority |
| `_resolve_bot` raises before call_api | A separated local failure would prove no call_api | Current try/except covers BOTH `_resolve_bot` and awaited `bot.call_api`, so `ChannelNotConnected` code alone cannot prove the phase |
| `send_failed`, connection_error, timeout, missing message_id | External send may already have occurred | Unknown; never new Attempt/send on retry |

The original sender does not receive effect/Attempt identity and passes only target/text/optional bot. Plugin/channel JSON has no structured no-send phase tied to that request. Therefore the named consumer-only scope cannot manufacture a safe proof. An arbitrary provider or an equal error string/code must not supply this authority.

Minimum coordination requested (no edits made to these paths):

1. `packages/qq/plugin.py::api_send`, only the selected-bot-not-found pre-send return; and `packages/qq/channels/onebot.py::OneBotChannel.send`, only separation of `_resolve_bot` from `call_api` and the actual pre-send returns. Supply trusted structured local phase evidence, preserving existing raw errors.
2. `packages/web/server.py::_send_scheduled_qq` / `_qq_plugin_post`, only existing POST request/response identity binding needed to connect that evidence to the original canonical Attempt/effect and frozen arguments. No remote query, deduplication claim, new ledger or transport.
3. Then the already authorized QQ provider/_channel adaptation and a narrowly trusted manual-only predicate within `_retry_boundary`, retaining the generic gates, can consume that original evidence. Whether an additive immutable release is needed must be decided before any publication edit; none was made here.

Until that contract is coordinated, the two strict successor tests intentionally assert the desired retry admission and fail with actual HTTP409 `not_retryable`. Their prior assertions prove zero OneBot requests and one rejected Attempt. They do not turn test-side knowledge of connectivity into product retry permission. Successful same-Run/Step/effect new-Attempt retry, duplicate retry control/CAS and cold retry success are **not verified**; do not mark them closed.

## Current exact proof mapping

Machine-readable commands, actual counts, node IDs and source hashes: [parity-matrix.json](../../evidence/job-retirement-rev9-20261029/parity-matrix.json). Previous successors.json, original43 artifacts and r6/r7/r8 evidence remain byte-for-byte untouched. This is an M08 overlay; no unrelated original-node status is rewritten.

| Old node / retained semantics | Exact new successor | Current disposition |
|---|---|---|
| `test_scheduled_qq_registered_callback_posts_only_to_plugin_send_route` / original default and selected bot, raw message_id | `test_original_plugin_real_onebot_http_success_exact_receipt_single_send[None-user-111111-send_private_msg]` and `[222222-group-222222-send_group_msg]` | Owned real plugin/OneBot HTTP pass; exactly one request, original raw text/CQ bytes, actual message_id and canonical receipt; dispatch/cold reopen no repeat |
| `test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure` | Original r6 `test_real_plugin_default_selected_bot_failure_manual_receipt_and_no_replay` all3 cases, unchanged | Reverified original selected/default/unknown bot raw errors; no Session target, no dispatch replay |
| `test_scheduled_qq_backlog_retry_runs_without_session_target` | `test_local_disconnected_no_send_manual_retry_retained_gap[None]` and `[222222]` | **Two real reds; producer identity evidence blocked**. No invented backlog or second retry engine |
| `test_patch_text_updates_dispatch_body` / admitted frozen target/bot/text | `test_edit_after_admission_keeps_actual_original_bot_target_text` | Actual original selected bot/target/raw text used after editing live definition; frozen plan unchanged |
| Transport accepted but response lost / missing acknowledgement | `test_accepted_without_ack_is_unknown_cold_retry_no_new_attempt_or_send[lost-response]` and `[missing-id]` | Real OneBot HTTP fixture records acceptance, returns empty/missing acknowledgement; original plugin raw error/null receipt preserved, outcome unknown; cold retry raises reconciliation_required, zero extra Attempt/request |
| Forbidden short IDs/blank text and legal text/IDs/bot, custom/schema/CAS/no-write | Unchanged r6/r7/literal/name tests in immutable-validation group | See exact JUnit counts; only the originally proven write semantics are claimed |

Initial reproduction [initial-http.xml](../../evidence/job-retirement-rev9-20261029/initial-http.xml) is 2pass/3fail: two meaningful product reds (lost response incorrectly rejected; missing-ID incorrectly succeeded), plus an owned test setup KeyError from reading public projection `plans`. The setup was corrected to the real canonical store's `finalRuntime.plans`; it is not counted as a product defect. After classification repair, [http-after-unknown-fix.xml](../../evidence/job-retirement-rev9-20261029/http-after-unknown-fix.xml) has 5pass/0fail, exit0. These preliminary runs preceded the immutable source commit; final three matrix commands bind hashes to the immutable SHA above.

## Verification and resource boundaries

Every command uses designated `D:/project/Pan-main/.venv/Scripts/python.exe` through `scripts/run_tests_isolated.py`; the owned runner records full argv, actual exit, source SHA and bound-file hashes before execution, then verifies no source mutation. [environment.json](../../evidence/job-retirement-rev9-20261029/environment.json) records actual interpreter/dependencies. No dependency was installed this revision.

| Immutable source group | Passed | Failed | Error / skip | Exit | Wall seconds |
|---|---:|---:|---:|---:|---:|
| immutable-qq-http | 5 | 2 | 0 / 0 | 1 | 93.646 |
| immutable-runtime | 138 | 0 | 0 / 0 | 0 | 106.900 |
| immutable-validation | 133 | 0 | 0 / 0 | 0 | 149.971 |
| Unique executed nodes | 276 | 2 | 0 / 0 | two red targets remain | Separate commands, no duration sum claim |

Actual environment: Python3.14.5, nonebot2 2.5.0, nonebot-adapter-onebot2.4.6, httpx0.28.1, uvicorn0.52.4, tzlocal5.4.5. Each focused command was expected and completed below three minutes; no background durable Job was needed. Independent isolated commands ran concurrently with separate synthetic roots and random listeners.

- `immutable-qq-http`: complete seven dedicated real HTTP nodes, including the two retained red retry targets.
- `immutable-runtime`: entire session_product_closure, job_final_http_service, job_final_executors, job_automatic_recovery and job_qq_native_provider. Existing provider doubles in historical tests are regressions only; the real HTTP proof is the dedicated seven-node group.
- `immutable-validation`: r7 write validation, unchanged original QQ invalid/error/edit cases, source-aware Session validation and literal/name scope tests.

Actual counts/exits/durations are retained in the machine matrix and command JSON files; no collection/mock result is counted as delivery proof. Raw artifact ZIP is independently hash-verified; plaintext views are normalized only if Git line-ending handling requires it, while raw bytes remain archived.

Both owned HTTP listeners bind random loopback ports and tcp fixture finalizers join their server threads and assert the listeners are gone, including on red tests. The plugin HTTP client is explicitly closed; real NoneBot HTTP sessions use their request context cleanup. Canonical roots, auth, history and synthetic data are temporary. No Worker is spawned by QQ sends. Existing services/8767/8768, accounts/profiles, real51/60, main/practical, push, CI, WSL and deployment were not operated.

Unverified layers remain real QQ gateway/account delivery, remote CI for this candidate, actual migration/runtime activation and deployment. Default forms/MCP, unknown fallback/clear-target, monitoring/stable-reset and other capability decisions remain outside this revision. Clean Git candidate means reviewable source/evidence, not that the two red retry targets or overall migration are accepted.
