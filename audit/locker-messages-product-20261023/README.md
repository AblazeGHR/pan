# Locker ordinary-message product candidate

本批补齐普通 **Locker msg** 的内部接纳、持久化、受保护用户读取/分页/标读/清理，并与 Card 在独立 Locker panel 按时间和稳定 ID 混排。没有新增公共生产者 HTTP CRUD/MCP，没有转存 Chat、QQ、Job 消息。旧 Card API 和响应兼容。

## Baseline and source

- Worktree: `D:/project/pan-worktrees/locker-product-integration-20261023`
- Branch: `integrate/locker-product-20261023`
- Accepted Card/layout stage: `5eee57c5546c23c1c68e2fc894e5b52c220e7831`
- Integrated main: `818f6b849d31cac98f03766ad1a6eb7788f5ea58`
- Ordinary-message implementation: `6be8003b`; main merge: `b8300d7d91cebbe68f607e15a98f4e7d58170c73` (no conflicts).
- Full gate tested source: `0c4509e7ec6694deecdd491a18d95547df4fb1e0`.
- Product source: `44bd35da23fd7d496ddc01b9ed0ba65b2497af9d`. Its only change after the full gate is five CSS declarations keeping timestamps/status/read buttons from shrinking or wrapping on narrow screens; build and the real-service browser/cold-restart test were repeated on this source.
- Final tested source: `cd92067b5140694751417fd9be29b8d517e661c0`. It changes only the screenshot harness to capture the top of the timeline deterministically, and repeats the real-service browser/cold-restart test; `screenshot-commands.json` and `screenshot-live.xml` record this run.
- Original mock tree remains clean at `7d0bfee7fd2f9f0a8abe2dc0980e287a167eba6f`. Practical remained `2652ac8e6d0ff312a255666bc5e459890346e6e5` at delivery checks. This task does not move main/practical or push/release.

## Changes

`packages/interactions/locker_messages.py` exposes only `accept_locker_message` for a trusted in-process adapter/test caller. It validates the existing source Session and opaque attachment scope before storage. It is not an HTTP/MCP producer endpoint and does not derive producer identity from the user Bearer.

`cards.sqlite` receives additive, transactional, idempotent tables and metadata columns. Ordinary records contain a Message-compatible document (`messageId`, `role`, `content`, `parts`, `ts`, future fields), not a Card definition. Identity is scoped to recipient/source/messageId; exact replay is idempotent, changed content conflicts, capacity rejects without truncation, and deletion retains an identity/signature-digest tombstone. Old Card records remain readable and retain their response/binding lifecycle. Tests create the old schema directly, migrate repeatedly, and check concurrent same-ID admission.

The existing UserAuth principal protects the new `/api/user-locker-messages` list/get/read/delete/download endpoints. User requests cannot select a recipient. Read/unread updates the original row and revision, while the insertion-sequence query cursor only paginates; it is never a consumption cursor. The API preserves existing zero-count Locker metadata compatibility; the panel omits empty sources. Source metadata snapshots retain the name/Workspace of deleted sources. Aggregates remain frontend views without containers or copies.

The panel continues to use the actual Sidebar/WorkspaceRail and Session drag/quick-action presentation from the accepted stage. It merges both API result types by parsed time then stable ID; ordinary messages have no submit/choice controls and can be cleaned as completed reading records. Open Cards remain non-deletable. Markdown, code, timestamp and safe attachment rendering use existing components. Locker supplies an optional authenticated attachment download callback to MarkdownRenderer; Chat's normal editor/file behavior is unchanged. The download endpoint checks recipient, message membership and the existing source attachment registry rather than trusting href/path/name metadata.

Logout/401 immediately remove old-user rows and source metadata. Requests capture their auth identity so a delayed response from an old login cannot overwrite the successor token/data. Ordinary read revisions prevent older responses from replacing newer state. Existing Card request-ID retry, trusted binding, Trigger/Job/Pause and Queue unknown/rejection semantics are retained.

The system-field main merge is preserved unchanged: no local changes to canonical system fields, RuntimeIssue, EventStore, Trigger engine or Worker source. Relevant system-field and runtime-issue identity tests are part of the targeted Python gate; the Locker choice/Pause service loop exercises the combined automation wiring.

## Validation and exact evidence

The full gate ran in persistent Job `job_32e745a3608996a570031bc5`, target `ses_b51e6891580d75eb`; terminal notification and Registry both report completed/exit 0. Registry records `processTreeEnded=true`, `notificationState=delivered`. Its console log is copied to `background-job.log`; `commands.json` records exact argv, per-command exit and tested SHA. The stop instruction arrived after launch; no inspection or further mutation occurred until the explicit continuation. Final layout-only follow-up commands are in `layout-commands.json`; screenshot harness follow-up is in `screenshot-commands.json`.

| Check | Result | Evidence |
| --- | --- | --- |
| TypeScript | exit 0 | `typescript.log`, `commands.json` |
| Build + compressed assets | exit 0; 94 assets verified | `build.log`, final `layout-build.log` |
| Targeted Vitest | 6 files, 147 passed, zero failures/errors/skips | `vitest.xml`, `vitest.log` |
| Targeted Python | 69 passed, zero failures/errors/skips | `python.xml`, `python.log` |
| Real service + production Chromium + fakeCLI + restart | 1 passed, zero skips; exit 0, also repeated after CSS fix | `live.xml`, `layout-live.xml`, `live/evidence.json`, `live/browser-*.json` |
| Browser checks | exercise 10 groups; cold 2 groups; no page errors | `live/browser-exercise.json`, `live/browser-cold.json` |
| Whitespace / baseline ancestry / clean tree | checked at delivery | `delivery-manifest.json` and final Git checks |

The real service runner uses a fresh temporary profile, a random loopback port and the existing isolated queue/automation harness with fakeCLI. Browser core Locker, UserAuth and Card APIs are **not intercepted**. The only added test-only routes admit fixtures through the internal adapter and temporarily hold a real serialized GET response to test logout/late response. They are defined solely in `tests/support/locker_product_http_server.py`, not registered in production.

Verified in this live chain:

- Ordinary message + Card in one Locker, cross-source aggregate, stable mixed ordering and distinct record kinds; dedicated records do not enter Session Chat.
- Real query pagination without marking read; Workspace/ungrouped/all switching via WorkspaceRail; original-row Read all; manual unread across refresh and cold restart.
- Source deletion retains an unavailable Locker and readable record, including remembered Workspace membership.
- Real protected attachment bytes downloaded by a browser click; unauthenticated download denied, unrelated-message attachment denied, cross-source admission denied.
- Trusted choice/Pause submit, no-choice BTW submit, rejection with reason, silent reasonless rejection, terminal Card delete and ordinary record delete.
- Source fakeCLI actually receives the three expected notifications, each once. The existing queue batches them into **one stdin handoff**; the three original QueueIDs are preserved in the user history receipt. Summary bodies are 200 characters, responses are included, and full Card bodies are excluded. Only the source workdir consumes them. Reasonless reject and delete add none. Replaying the original submit does not create a second run/notification.
- Existing Trigger/Action produces one successful Pause run; subsequent target input remains queued while paused. Cold restart preserves accepted dispatch and does not replay the notifications.
- Logout while a real GET response is held, successor login, and actual server 401 remove old-user visible rows/sources; unit coverage additionally checks a delayed old-token response cannot clear a successor token.
- 1440/768/390/320 light and dark production layouts without horizontal overflow. Eight width/theme screenshots plus initial mixed-message screenshots are under `live/`; 320px timestamps/status stay on one line after the final CSS fix.

## Reproduce

Run from the isolated tree. Dependencies are local to this tree; no provider setup is needed.

```powershell
python audit/locker-messages-product-20261023/verify.py
```

`verify.py` writes exact commands/results into `commands.json`. Its test suite starts and stops owned services automatically. For just the real-service browser chain after a build:

```powershell
pnpm --dir packages/web run build
python -m pytest -q tests/test_locker_product_live.py --junitxml=audit/locker-messages-product-20261023/live.xml
```

These commands are suitable for a persistent Job when conservatively expected to exceed three minutes. Do not run the historical harness with its default protected port; the new test explicitly allocates and patches a random port itself.

## Boundaries and cleanup

The final live evidence records the exact random port, profile, server/fakeCLI PID creation times, zero remaining owned processes, exited server and released listener. The prior full-gate run used port 57973 and was independently checked after resuming. No operations target 8767/8768, real profiles, QQ/provider accounts, user Workers, OAuth/keyring/private exporter or the original mock tree. The fixture's adapter label is CBC solely because the existing harness substitutes `fake_stream_cli.py`; no CBC model/provider Worker is used. Agent execution did not switch model/account.

Existing dependency/config warnings are retained: missing pytest timeout plugin/mark registration, Starlette/httpx deprecation and pydantic forward-reference warning. Vite's existing large-chunk advisory remains. Sidebar unit fixtures log deliberately unavailable fake history; assertions pass. Early fixture failures (invalid Session naming, old history response key, batched-notification counting) remain in `red/` and numbered JUnit artifacts; they are not final pass evidence.

No public dedicated producer API/MCP, Chat/QQ/Job forwarding or plugin-source expansion is included by design. Draft/source order remain mounted-panel state rather than durable user preferences. Physical touch drag, actual external providers, full-repository tests, remote CI, deployment and practical/data gates are outside this candidate. The real service runs prove the owned fakeCLI transport and production local APIs; they do not claim a real model consumed the notifications. Candidate completion is not MA acceptance.
