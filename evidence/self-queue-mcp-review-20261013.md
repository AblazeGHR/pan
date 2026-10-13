# MA review follow-up: native spawn incident and rejection/idle routing

Parent implementation: `50dc1397ba030e054fd5055e17603d5d7b9905fc`.
Scope remains the isolated `feature/self-queue-mcp-20261013` branch.
No main/practical advancement, push, real-service lifecycle operations or user-data
cleanup were performed during this follow-up.

## Independent incident check

The earlier phrase “whether native data was written remains unknown” can now be
narrowed. Read-only evidence proves **native app-server startup and native log
writes occurred in the exact failed-test time window**. The two Pan instances
strongly correlate with the two unstubbed unit-test creates; historical parent-PID
and full-argv captures are unavailable, so attribution is not represented as a
complete OS audit chain.

- Surviving temporary directory:
  `C:/Users/14709/AppData/Local/Temp/pytest-of-ablaze/pytest-2334`.
  Its `.lock` contains PID **36208**, matching the original exact pytest process
  selected for termination. Two surviving `ses-own.json` files were written at
  **12:02:57.567939** and **12:02:57.842432** (Asia/Shanghai), in the priority and
  cancellation tests. They specify `adapter=codex`, empty workdir/config and no
  `cli_session_id`; Pan legal states are running/idle, respectively. Both history
  JSONL files are empty. File hashes and queue reservation snapshots are retained
  in `self-queue-mcp-spawn-forensics.json`.
- A read-only connection to default user home `C:/Users/14709/.codex/logs_2.sqlite`
  found these exact process UUIDs:
  `pid:4476:a6e8dddb-adca-4953-9e05-5762e52ad67d` and
  `pid:11368:8d55d49f-3db9-47ae-b0df-737cd54feb7d`.
  Each has **61** stored native log rows, from **12:02:57/58 to 12:03:13**.
  Request rows show `initialize` and `thread/start`; request spans identify
  `app_server.client_name="pan"`, `client_version="0.1.0"`, and stdio transport.
  Default-home config warning rows independently confirm use of the real
  `C:/Users/14709/.codex/config.toml`, rather than an isolated native profile.
- The two startup thread IDs in native logs are
  `01a1336d-84e8-7630-b396-ac23199f9575` and
  `01a1336d-8616-7413-a1a8-90aba7c1dd79`. No corresponding thread index row or
  rollout file was found. No `turn/start` request is present in either process's
  retained request logs. This supports startup without a recorded model turn;
  it does **not** establish absence of all network calls, MCP initialization,
  cache activity or other native side effects. Native log writes are certain.
- Actual historical full argv and parent/child process creation timestamps were
  not recorded. The forensic JSON explicitly labels its argv as a **current
  reconstruction**, not a captured historical command: Python `wrapper.py
  --app-server`, Node/Codex JS, default model `gpt-6-luna` and the existing default
  permission option. Wrapper source creates Node/Codex `app-server --stdio`.
  Windows Security process audit was inaccessible without elevation and Sysmon
  was unavailable; no elevation was requested. PID reuse is not treated as proof
  that a historical process is still alive.

No auth/config content was copied, and no native/user file was deleted, edited
or reset. The existing unit fixture now forbids `_spawn_process` and stubs both
create and restart paths. All newly executed provider/process E2E below uses the
existing deterministic fake provider and temporary Pan data root.

## Narrow fix: rejection versus unknown

Root cause: the own-queue wrapper converted every non-null Steer error to
`unconfirmed/retryable=false`. Separately, receipt handling discarded structured
fields and queue settlement guessed whether a failure was unknown from the
error-text prefix. A genuine RPC rejection containing that phrase could therefore
remain blocked, while an unknown transport result with different wording could
be mistaken for a retryable rejection.

The existing `pan.steer_receipt` retains `ok` and now carries `outcome`:

| Control boundary | Outcome | Original item and own-MCP behavior |
| --- | --- | --- |
| Validation/encoding/persistence fails before synchronous control write | rejected | Original item retained; if still visible, `rejected/retryable=true` |
| Wrapper has no active turn or rejects an over-capacity request before RPC | rejected | Same original item, explicit same-ID retry permitted |
| JSON-RPC response has `error` | rejected | Typed proof of provider refusal; error wording is irrelevant |
| JSON-RPC response succeeds, or an adapter's stdin-drain acceptance contract succeeds | accepted | Existing durable sent receipt is still required before `consumed` |
| Control/RPC write attempted but fails, missing receipt, active owner timeout, invalid/untyped failure | unknown | Keep original pending gate, no automatic repost or success claim |

The queue operation's per-call outcome starts as rejected before the write
boundary and becomes unknown synchronously **before** attempting the write. It
only changes afterward upon structured acceptance/rejection. The existing
original request/Worker/generation gates and future remain authoritative; no new
durable registry, public route selector or general abstraction was introduced.
Legacy direct controls retain their string-return API, while queue classification
and settlement no longer inspect error text. Legacy untyped failures are
conservatively unknown. Hidden/guessed IDs still share `item_unavailable`; a typed
rejection does not reveal a newly locked or paused item's existence.

## Adapter route contract and idle behavior

- Running stream Worker with actual `supports_native_steer`: consume the original
  item through the native confirmation chain. Codex confirms its RPC; Claude's
  existing contract accepts at stdin drain. The tool surface never exposes these
  internal route choices.
- **Idle/queued stream Worker with that capability**: save the original priority
  intent and wake the ordinary consumer. No extra Restart, generation bump or
  process replacement is needed. The normal consumer preserves the existing
  delayed-restart priority and final queue gate. The result stays pending until
  actual handoff, never “consumed” merely because it was woken.
- Worker without native capability: retain the user's requested priority-plus-
  restart contract and normal consumer handoff. No live Worker: create/recover
  normally. A worker that changes state before an attempted native write gives
  a pre-write rejection, allowing a later explicit same-ID call to reevaluate
  its current route safely.

## Red/green and durable cancellation evidence

`self-queue-mcp-review-red.xml` and `.log`: four new contract tests failed against
the parent implementation: typed rejection/retry, typed unknown with neutral
error text, provable encoding failure, and native-capable idle no-restart.

`self-queue-mcp-review-green.xml` and `.log`: **212 passed, 0 failed, 0 skipped**.
Suites: own queue, unified queue/FIFO, queue restart hardening, Session queue API,
Worker branch/direct Steer, history ordering, Codex adapter, Codex conversion/
delayed restart, and MCP handbook. Coverage includes the actual worker stdout
reader preserving structured receipts and wrapper RPC error/write-failure
classification, not just the own-wrapper mapping.

The new native cancellation tests use real `Task.cancel()`, a real persistence
executor ticket and thread gate, and actual temporary Session JSON files. For
both successful and failed cancelled writers they reread disk after the owner
retires, verify original ID/body/parts/envelope and no stale pending token, prove
zero control writes, then explicitly retry the same original ID and verify its
sent receipt on disk. Rejection tests likewise reread disk before retry and after
acceptance. Native provider responses remain simulated; no production credential
or real model was used for these regression tests.

The isolated HTTP/FIFO/restart suite passed **2 tests** on dynamic loopback port
**62608**; its log/JUnit are retained as `self-queue-mcp-review-http.*`.
Existing timeout-plugin/Pydantic/Starlette warnings are recorded rather than
suppressed. Real provider end-to-end acceptance, browser/OS interaction and MA
independent acceptance are still separate, unverified layers.
