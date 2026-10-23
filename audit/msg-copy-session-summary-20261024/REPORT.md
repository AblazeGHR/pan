# 消息复制全文与 Session 卡片预览候选

工作树：`D:/project/pan-worktrees/msg-copy-session-summary-20261024`。分支：`fix/msg-copy-session-summary-20261024`。初始 HEAD 精确为 `81415706d0dc55b1edbe02b62c9b4a043d5fd44a`，初始 status clean。已读取 `CODEBUDDY.md`、`docs/plans&overviews/constraints-and-acceptance.md` 与 `docs/TESTING.md`。本候选不依赖 main 后续变化。

## 行为与根因

1. 现有 MessageBubble 的 Message actions 菜单（含 Add/Remove bookmark）增加“复制全文”。复用 `copyText`，并复用现有单消息 GET `/api/sessions/{sessionId}/messages/{messageId}` 及其 `history_read.project` 原文提取，不新增服务端接口或 Session 导出。不能直接用展示用 `message.content`：`_api_history` 和实时消息会将本地 Markdown 链接替换为 editor/attachment URL。新 API 客户端严格核对返回 Session 和 messageId，原样复制完整 canonical content。空正文、缺持久身份时禁用，读取或剪贴板失败展示错误，不将展示投影冒充原文。bookmark、手动汇报、rewind 的既有权限与入口保持原规则。单消息接口既有角色/source 限制保持不变。
2. 卡片滞留 user 的可复现遗漏入口是 `InputRow` 在 `await steer(...)` 返回之后调用 `appendLocalMessage`。assistant WS 投影可能已经先到，随后本地 user 回显无条件改写 `lastMessage`；`lastDisplayPreview` 实际仍是 assistant，而卡片优先读 `lastMessage`。再收到同 revision 的正确投影时，既有 `preserveNewerSummary` 将这个被污染的 user 文本当成应保留的 live preview，持续锁住。最小修复：versioned Session 的本地 Steer 回显只维护聊天记录，不写权威卡片预览；未版本化兼容路径保留原回退。
3. 顺链发现两处辅助污染：`extractStreamText` 在角色判定前直接返回任何 `stream_text`，使 thinking/tool delta 可冒充 assistant；后端 `_apply_summary_message` 会用空白正文清掉已有主角色预览。现在流文本通过已有 block 角色提取确认，空白行仍计入 history/cursor 而不覆盖预览，thinking/tool 的独立字段仍保留，但不作为卡片正文。SessionItem 尊重权威空预览；只有没有权威预览的兼容数据才从本地 user/assistant/system 非空正文回退。

真实链：Worker `append_history` → `_apply_summary_message` → Session 元数据持久投影 → `_session_summary` / `_attach_session_summary_patch` → `useWebSocket` → `sessionStore` → `SessionItem`。没有新增摘要系统，也没有按 agent/report/automation/system_prompt 来源隐藏正文。没有 assistant 时后续有效 user 仍可作为预览。

## 红绿与验证

修前定点红灯：前端 36 用例中 5 failed / 31 passed，分别为迟到 Steer 回显覆盖 assistant、两类辅助 delta、复制入口缺失两例；Python 空正文冷加载 3 failed / 34 deselected。修后扩展到以下受影响范围，全部通过。

| 命令 | 结果 / 退出码 |
| --- | --- |
| `pnpm --dir packages/web exec vitest run`，下列 13 文件 | 287 passed，0 failed，0 skipped；exit 0 |
| `python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_session_summary_projection.py tests/test_history_read.py tests/test_replay_resync.py -q -o addopts= --junitxml=packages/web/test-results/msg-copy-pytest.xml` | 65 passed，0 skipped；exit 0；3 个现有依赖 warning |
| `pnpm --dir packages/web build` | `tsc -b` 与生产构建 exit 0；94 compressed assets 验证通过 |
| 定点 `pnpm --dir packages/web exec eslint` | exit 0；MessageBubble 既有 3 个 Fast Refresh export warning，0 error |
| `$env:PAN_E2E_PYTHON='D:/project/Pan-main/.venv/Scripts/python.exe'; node packages/web/e2e/session-summary.e2e.mjs` | exit 0；真实 HTTP/WS、生产 Chromium、fake CLI，7 项检查通过 |
| `git diff --check` | exit 0 |

13 个前端文件：`hooks/useWebSocket.test.tsx`、`hooks/liveIndicators.test.tsx`、`stores/sessionStore.snapshot.test.ts`、`stores/sessionStore.refresh.test.ts`、`stores/sessionStore.interleaving.test.ts`、`stores/sessionStore.terminalTransient.test.ts`、`stores/sessionStore.t055.test.ts`、`components/chat/MessageBubble.bookmark.test.tsx`、`components/chat/MessageBubble.manualReport.test.tsx`、`components/chat/MessageBubble.rewind.test.tsx`、`components/session/SessionItem.test.tsx`、`utils/clipboard.test.ts`、`services/api.messageText.test.ts`。

Vitest 的 HTTP unavailable 日志来自刻意拒绝兜底 HTTP 的 hook fixtures，用来证明 WS 自身可更新后台卡片。构建有既有 dynamic/static import 和 chunk size 提示。依赖在本工作树独立安装；没有写共享 node_modules。首次复制依赖未能还原 junction，后以 offline frozen-lockfile、ignore-scripts 的本地安装恢复，依赖/lockfile 未进入改动。

## 浏览器证据与边界

提交前完整生产构建：`index-Cy7RZf9J-pc2.js`。成功 runtime：`packages/web/test-results/summary-1792773361375`，随机端口 `61868`，launcher PID 见 [browser.json](browser.json)，`portReleased=true`。临时 Chromium 私有 context/profile，页面错误 0。记录包含真实 WS 事件、preview timeline 和清理结果。

覆盖：旧 WS / 新 HTTP 乱序；旧 history response；后台 Session 实时 assistant；切换与冷加载；paused Send 不伪造已交接历史；fake CLI handoff / streaming / reconnect / result；Force replacement；空白 assistant、thinking/tool 后卡片不回退；点击实际菜单并读取 Chromium 剪贴板。复制用例包含本地链接、Markdown、代码、长 CJK 正文和尾空格，断言展示内容已被 link projection 改写而复制结果仍为原文。Windows 剪贴板会把 LF 转为 CRLF，比较时仅规范化换行，其余正文逐字一致。

首次浏览器复制断言过早读取，随后发现 Windows CRLF 差异，均为 harness 断言修正；保留在 ignored runtime 中，最终验证没有用截断、contains 或去除尾空格降低断言。

迟到 Steer HTTP acknowledgement 的精确时序由 store 红绿用例验证；浏览器 Force 使用 fake CLI 的 capability-absent restart，不能宣称真实 provider native Steer 已验收。未测真实 provider、账号、native TUI、移动端或其他浏览器；未跑全仓/全量 CI；不包含既有坏投影的全数据迁移。该候选未合并 main/practical，未 push/release，不重启用户服务，不使用 8767/8768，不访问用户 Worker/真实 data 或活动 CI 工作树。

提交后冻结 HEAD 的复验日志保留在 ignored `packages/web/test-results/`；精确受测 SHA 与新 runtime 由最终 TA 报告给出。TA 完成仅代表 clean 候选，仍待 MA 独立验收。
