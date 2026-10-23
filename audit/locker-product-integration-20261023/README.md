# Locker product integration candidate

本候选把已验收 Locker mock 的布局方向接到现有 `/react/locker` 产品入口，使用真实 UserAuth、Locker/Card API 和服务端持久化。它是当前后端契约下的前端整合候选；普通 Locker message 尚缺后端契约，不能宣称完整冻结功能已经实现。

## Source and baseline

- Worktree: `D:/project/pan-worktrees/locker-product-integration-20261023`
- Branch: `integrate/locker-product-20261023`
- Verified latest main / integration base: `c9c9fe92160bd257eb4b7d53b0669fa85aa1cf2a`
- Initial isolated base: `73289afb5ee3d1e8bf225078cc0ef953f7a9c5eb`
- Accepted mock reference: `bfa9c680fe07d485785c7ebae27eeb6420193f61`
- Preserved mock branch HEAD: `7d0bfee7fd2f9f0a8abe2dc0980e287a167eba6f`
- Implementation: `b00a4ec4`; main merge: `e6118442668351c2d8c6129a02aeb100a4bfb205`; validated source: `8a77a6215f8d9d134619d5fe983fc4ffcf97eaf3`
- Main merged using Git's `ort` strategy without conflicts. Its changes concern CI test support, evidence and MA documentation; no system-field implementation was included. `git merge-base HEAD main` equals the integration base above.
- Local practical remained `2652ac8e6d0ff312a255666bc5e459890346e6e5`. This task did not move main/practical or push/release.

## Product changes and reuse

- `App` and `Sidebar` supply a route-specific list host. Locker uses the existing resizable Sidebar, navigation, mobile drawer and actual `WorkspaceRail`, rather than rendering a second sidebar or a Workspace selector. The Session list remains on the Chat route.
- Source rows reuse `sessionQuickActions.css` for upper-right Navigate to chat / Read all controls. `SessionDragGutter` extracts the original SessionItem presentation and is consumed by both SessionItem and Locker. Aggregate has no draggable gutter. Locker order is a local view preference; it does not mutate Session membership or create containers.
- Source navigation loads/validates the source Session, calls the existing `selectSession`, and navigates to Chat. Unavailable sources retain their records with navigation disabled.
- Workspace/ungrouped/all projections retain original Card IDs and one persisted read flag. Empty sources are omitted, including after deleting their last record. Source filters keep each mounted Card's draft and uncertain mutation identity intact. Refresh reads all API cursor pages before applying the snapshot.
- The timeline reuses `MarkdownRenderer`, scoped attachment helpers, `MessageTimestamp`, shared `Button`, message-row classes and actual CSS theme variables. No Chat composer is added to Locker. Source IDs and timestamps remain visible; operation digests are folded under details.
- Every open Card offers optional Answer or BTW. Choices show explicit options and trusted execution summaries; unbound executable choices remain disabled. Submit freezes the response; rejection preserves a record and optional reason; Delete is exposed only for terminal records. Unknown HTTP results retain the same request ID/content for retries and cannot switch operation.
- Minimal backend contract adjustments: Reject accepts optional `answer`; its persisted response retains the reason. A reason creates an owner-only rejection outbox entry; no reason creates no notification. Every Card accepts a BTW answer even without an explicit `answerInput`. Existing maximum lengths, UserAuth, producer permissions, trusted bindings, Trigger/Job dispatch and terminal deletion gates remain in place.
- Submit/reject notifications retain the server's 200-character truncated `summaryBody` plus response fields. They do not contain the full body; deletion adds no notification. Backend tests exercise reason/no-reason, idempotency, single owner delivery and truncation.

## Reproduce validation

Run from the worktree root in PowerShell. Dependencies were installed only in this tree with `pnpm install --frozen-lockfile`.

```powershell
pnpm --dir packages/web exec tsc -b
pnpm --dir packages/web run build
pnpm --dir packages/web exec vitest run src/views/LockerView.test.tsx src/views/lockerModel.auth.test.ts src/components/layout/Sidebar.test.tsx src/components/session/SessionItem.test.tsx src/components/session/SessionList.drag.test.tsx
python -m pytest tests/test_user_locker_lifecycle.py tests/test_interaction_cards.py tests/test_interaction_card_api.py tests/test_locker_trusted_binding.py
node packages/web/e2e/locker-product-fake.mjs
```

The Chromium runner serves the production build on a loopback random port. All API requests are fulfilled with fake data inside a fresh Playwright context; external requests are aborted. It does not proxy to a backend or use real data, credentials, providers or accounts. The static server and browser close in `finally`. The last successful run used port `64575`; a subsequent listener/process check found no remaining runner or listener. No operations targeted services on 8767/8768.

| Check | Result | Evidence |
| --- | --- | --- |
| TypeScript `tsc -b` | exit 0 | `typecheck-final.log` (no diagnostics), also build and commit hook |
| Production build | exit 0; 94 compressed assets verified | `build-final.log` |
| Targeted Vitest | 5 files, 108 passed, no skips; exit 0 | `vitest-final.log`, `vitest-final.xml` |
| Targeted pytest | 41 passed, no skips; 3 dependency/config warnings; exit 0 | `python-final.log`, `python-final.xml` |
| Chromium | 11 check groups, no page errors; exit 0 | `browser-final.log`, `browser.json` |
| Formatting/whitespace | `git diff --check` exit 0 | final worktree check |

Build retains the existing large-chunk advisory. Vitest Sidebar fixtures intentionally log failed fake Session history reads; the assertions pass. Python warnings concern pytest timeout configuration, Starlette/httpx compatibility and pydantic forward references.

Browser coverage: actual Sidebar host and WorkspaceRail handle; Workspace/ungrouped/all projection; source drag and non-draggable aggregate; source/aggregate draft retention; Markdown/code/scoped opaque attachment link; trusted target and BTW submit payload; reject reason; terminal deletion; original-ID Read all; reload from fake server state; error and Refresh retry; specified-Session Chat navigation; 1440/768/390/320 widths in light/dark themes with no horizontal overflow; shared mobile source drawer.

Screenshots are from this production route with fake API interception: `desktop-dark.png`, `{1440,768,390,320}-{dark,light}.png`, and `320-source-drawer.png`. `browser.json` records intercepted API requests and assertions. The auth token/key there are explicit fake-only fixtures. Earlier failed experiments/logs are retained, including `red/`, `python-first.log`, `vitest-followup.log` and `browser-red-navigation.log`; they are not final pass evidence.

## Remaining boundaries

1. Current main exposes Interaction Cards through `/api/user-inbox`; it has no ordinary Locker message persistence/list/read API. This candidate renders complete Card body Markdown/code/attachments but does not invent an ordinary-message schema, convert ordinary messages into fake Cards, or claim that mixed ordinary-message/Card delivery is complete. That needs an accepted backend contract before the full frozen feature can pass acceptance.
2. Drafts and source ordering survive source/Workspace/filter changes while the panel is mounted. They are view state, not backend persistence and not durable across reload/logout/navigation away. Read state and completed responses use the real API; only the test harness simulates server state.
3. Chromium validates real frontend components with intercepted APIs. It does not prove a deployed backend, actual notification consumer, real Trigger/Job execution, real provider behavior or actual attachment bytes. Python tests cover those service contracts with isolated storage/fakes. No full-repository tests or remote CI were run by this task.
4. Source reorder uses native drag events with shared Session drag presentation. Chromium desktop drag passed; physical touch drag and OS-specific input behavior remain unverified.
5. No new production route, OAuth/keyring/exporter/provider switch, account change, real data migration, worker restart, deployment, push or practical promotion was performed. The retained mock worktree was not modified. Candidate is awaiting MA/user review; ordinary-message integration and the final practical/data/CI gates remain outstanding.
