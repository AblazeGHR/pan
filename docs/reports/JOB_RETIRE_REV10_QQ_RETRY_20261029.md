# JOB-RETIRE revision10 — QQ retained behavior closure

Candidate source is `8b2aa0cfcfd1209037545c52e2b1ac868667fe47`, on branch `r6/retirement-parity-acceptance-20261029-a`. The worktree started clean at r9 candidate `f04011d1691fe0aa2982c772b3d338866ed65b5d`; only the authorized QQ producer, host association, thin adapter, typed channel path, manual retry predicate, dedicated tests and this evidence were added. D1–D6 and the v2 minimal migration plan remain unchanged.

The two r9 red tests now pass through a trusted original pre-send phase: default bot and selected bot are disconnected before dispatch, OneBot receives zero requests, the original failed Attempt records the phase bound to its immutable frozen target/text/bot and canonical effect key, then explicit manual retry after connection restoration sends once through the original plugin/NoneBot/owned OneBot HTTP path. The retry keeps the same Run/Step/effect and creates exactly one new Attempt. Automatic channel retry remains disabled.

## Exact source changes

The product diff is [product.diff](../../evidence/job-retirement-rev10-20261029/product.diff). Changed methods and helpers:

- `packages/qq/channels/onebot.py::OneBotChannel.send`: validation and `_resolve_bot` return structured `qqSendPhase` only for actual before-`call_api` returns. Exceptions from entered `call_api` remain ordinary `send_failed`/`bot_not_connected` without proof.
- `packages/qq/plugin.py::api_send`: unknown selected bot is marked at the pre-channel-send return. OneBot evidence is forwarded only for the exact base `OneBotChannel.send`; custom/subclass receipts cannot gain provenance by copying an error code.
- `packages/web/server.py::_qq_plugin_post`: only `/api/qq/send` successful response objects receive an in-process request-body association. No request token, endpoint, query, cache or replay protocol was added.
- `packages/actions/qq_channel_provider.py::QQSendResponse`, `QQChannelReceipt`, `is_pre_send_phase`, `QQChannelProvider.send`: preserves the raw response while requiring the original sender, associated wire body, exact phase shape and frozen arguments before attaching `qqNoSend` to the existing effect receipt. `effectKey` and Attempt fence are carried inside that existing receipt.
- `packages/actions/typed_executor.py::TypedExecutor._channel`: passes the actual Attempt into the channel adapter and preserves `qqNoSend` only for the exact trusted provider. Unknown/malformed/missing acknowledgements remain unknown.
- `packages/jobs/final_service.py::FinalService._qq_manual_retry_proven` and `_retry_boundary`: a manual-only exception applies only to immutable trusted `product_channel`, exact provider/release/operation/action reference, literal frozen QQ arguments, failed original Attempt, effect identity, owner/generation/lease fence, phase and raw receipt. Generic permission, CAS, failed-boundary, prior-success and unknown/partial gates remain.

No product publication, Action schema, frozen plan, worker, scheduler, transport, cleanupProof, remote same-key claim, ledger or automatic recovery path changed. The old product retry declaration remains unchanged and is not treated as remote deduplication.

## Phase trust chain

`OneBotChannel.send` actual validation or `_resolve_bot` before `call_api` → `plugin.api_send` source-aware forwarding → original `/api/qq/send` response association → `QQChannelProvider.send` exact sender/body/phase validation → `TypedExecutor._channel` Attempt/effect binding → `FinalService._qq_manual_retry_proven` immutable action/frozen argument and owner fence → explicit `control_run` retry.

The following never authorizes retry: an equal error code from a custom provider, `send_failed`, timeout, connection error, non-2xx response, malformed/stale/wrong phase, wrong effect/Attempt/lease/owner/argument binding, null/missing `message_id`, or an exception raised after `call_api` was entered. Unknown and partial receipts remain reconciliation-required with zero new Attempt and zero send.

## Immutable verification

All commands used `D:/project/Pan-main/.venv/Scripts/python.exe` via `scripts/run_tests_isolated.py`, ran serially to avoid shared NoneBot global state, and bound to source `8b2aa0cf`. The owned runner records complete command JSON, JUnit XML, source/file hashes and exit status in [rev10 evidence](../../evidence/job-retirement-rev10-20261029/).

| Final group | Passed | Failed | Error | Skip | Exit |
|---|---:|---:|---:|---:|---:|
| Manual QQ retry and adversarial fences | 8 | 0 | 0 | 0 | 0 |
| Original QQ/channel/plugin/native tests | 96 | 0 | 0 | 0 | 0 |
| r9 HTTP, typed executor, Session closure | 47 | 0 | 0 | 0 | 0 |
| Recovery, write validation, literal/name | 166 | 0 | 0 | 0 | 0 |
| **Unique total** | **317** | **0** | **0** | **0** | **0** |

The original r9 two red tests remain byte-for-byte preserved in their old file and raw evidence; their exact revision10 successors are listed in [parity-matrix.json](../../evidence/job-retirement-rev10-20261029/parity-matrix.json). An earlier concurrent run showed two NoneBot fixture races; the serial rerun passed and only its results enter the final matrix. The two recovery-start capacity refusals occurred before code execution and are operational history, not test outcomes.

The dedicated tests cover default/selected/unknown bot, raw message_id and legal text, frozen edit, duplicate control/requestKey/CAS, cold reopen before and after retry, permission revocation, wrong/stale phase/effect/Attempt/arguments, custom provider spoofing, `call_api` entered exceptions, lost acknowledgement/null ID and unknown/partial no-resend. Existing automatic recovery tests pass and no automatic QQ retry was added.

Synthetic HTTP servers use random loopback ports and are shut down by fixture finalizers. No real bot, account, gateway, profile, data collection, Worker, main/practical, push, remote CI, deployment, WSL or protected ports 8767/8768 were operated. Raw evidence archive is hash-verified. This closes the bounded QQ M08 retained behavior line; default forms/MCP, monitoring/stable-reset, unknown fallback/clear-target, real migration, remote CI and deployment remain open.
