# Shortcuts 设置独立标签页 — 交付证据

- 分支：`feature/keyboard-shortcuts-20261019`
- 基线：`e4078c18`
- main 上下文：`8c10b2fc`（**未追合**；分类与自启动组合冲突由 MA 统一处理）
- 日期：2026-10-19
- Tab 顺序：General / Preferences / Behavior / Appearance / Notifications / **Shortcuts** / Adapter / Plugin / Data

## 1. 现有 app 快捷键审计与处置

| 原快捷键 | 动作 | 作用域 | 处置 |
|---|---|---|---|
| Ctrl/Cmd+B | 折叠/展开侧栏 | 全局 document | 保留默认，迁移为可配置 `app.toggleSidebar` |
| Ctrl/Cmd+1 | 打开 Chat | 全局 document | 保留默认，生成 `panel:builtin:chat`（`Mod+1`） |
| Ctrl/Cmd+2 | 打开 Editor | 全局 document | 保留默认，生成 `panel:builtin:editor`（`Mod+2`） |
| Ctrl/Cmd+K | 命令面板 | 全局 document | 保留默认，迁移为 `app.commandPalette` |
| Ctrl/Cmd+F | 当前 Session 历史搜索 | 全局 document，受 Appearance 开关 | 保留默认，迁移为 `app.historySearch` |
| Ctrl/Cmd+S | Monaco 保存 | 仅编辑器焦点内 | **固定**，不纳入全局注册 |
| Ctrl/Cmd+V | 终端粘贴 | 仅 xterm 内 | **固定**，保留浏览器粘贴路径 |
| Escape | 关闭最上层弹窗/菜单 | 焦点上下文 | **固定**，不纳入全局注册 |
| Enter / Shift+Enter | 发送 / 换行 | composer 内 | **固定** |
| Arrow / Home / End | 菜单/标签/列表导航 | 组件内 | **固定** |

Jobs、Terminal 与 plugin panel 现在都由实际导航注册目录生成 `panel:*` 动作；原有 Ctrl+1/2 之外的 panel 默认不绑定（新高影响动作默认未绑定），不会硬编码数量或使用顺序 index。

## 2. 新增可配置动作

- General：Toggle sidebar（`Mod+B`）、Command palette（`Mod+K`）、Session history search（`Mod+F`）
- Panels：由 `entries + /api/plugins/panels` 动态生成；稳定 id：
  - 内建：`builtin:chat`、`builtin:editor`、`builtin:jobs`、`builtin:terminals`
  - 插件：`plugin:<pluginId>/<panelId>`
  - 保存的 action id 为 `panel:<stable id>`，从不保存数组位置
- Queue（默认全未绑定）：
  - `queue.togglePanel` 展开/折叠队列
  - `queue.enqueueLocked` 当前文本/附件 occurrence/URL 节点作为**锁定消息**入队
  - `queue.lockAll` 复用产品 “Lock all queued messages”
  - `queue.pauseAll` 复用产品 “Pause everything”
- Selected Session（默认全未绑定）：
  - `session.toggleStopReport` 复用卡片 Stop report
  - `session.toggleNotification` 复用卡片 Notification/bell
- 可录制、覆盖、清除（存 `null`）、恢复单项默认、全部恢复默认；动态 panel 新增/移除后设置按稳定 id 隔离保留。

## 3. Lock 动作精确语义

### 3.1 锁定入队（`queue.enqueueLocked`）

- 前端复用 composer 正常发送事务：文本、URL Markdown、attachment parts、`clientMessageId`、队列 CAS/revision、失败恢复全部不变；额外传 `locked: true`。
- API：`POST /api/sessions/{id}/queue`，body 含 `{text, clientMessageId, parts?, locked: true}`。
- Worker 在 `queue_lock(session_id)` 内设置 `queueLockManual`，然后 append + 持久化 + 递增 revision；**不是**先入队再 PATCH。
- 调度器 `_select_queue_unit` 跳过 locked 行；reserve 阶段再次校验，因此锁确认前不会被交接；locked 行也不会唤醒/创建 Worker。
- 若 Worker 不支持锁定入队，返回 `locked_enqueue_unavailable`，不降级为“先入队后锁”。
- 已有真实 HTTP 证据：live idle worker 下锁定行 0.8s 未到达 fake CLI；unlock 后才交付。

### 3.2 “Lock everything” 的澄清

产品没有名为 “Lock everything” 的动作，存在两个不同动作：

- **Lock all**：`PATCH /api/sessions/{id}/queue/locks` `{locked:true}`。一次 durable revision 锁定所有 `deliveryState=="queued"` 行；不暂停队列、不锁未来消息、不停止 Worker。
- **Pause everything**：`PATCH /api/sessions/{id}/queue/paused` `{paused:true}`。暂停当前与未来 dispatch；不锁 item、不停止 Worker，正在执行的任务继续。

本实现将它们暴露为两个独立快捷键，语义不混用；没有把任何一个映射为“停止 Worker”。

### 3.3 Stop report / bell

- Stop report：`PUT /api/sessions/{id}/report-to-manager`，body `{managerId, enabled, expectedEnabled}`；仅修改直属 manager 的 `report_subscriptions`，不 unclaim、不影响 Worker、不影响手动报告。
- Bell：`PUT /api/sessions/{id}/msg-bridge`，body `{enabled, expectedEnabled}`；一次切换 system/browser/QQ bridge；readonly Session 被拒绝（`readonly_session`），无 manager 也可用。
- 与卡片一致的门禁：无 Session → no-op；Stop report 无 manager/布尔状态 → disabled；bell readonly/未知状态 → disabled；队列编辑 lease 由 composer 正常发送路径拦截；pause 不额外阻断卡片动作。
- 不做乐观翻转；失败不自动重试，只重读权威 Session 状态并提示。

## 4. 持久化与平台

- 存储：`config.json` → `ui.shortcuts`，形如 `{ "app.toggleSidebar": "Mod+B", "panel:plugin:alpha/one": null }`。
- 缺 key = 使用内置默认；显式 `null` = 用户清除；字符串 = 覆盖。
- 复用既有 `GET/PUT /api/settings/ui`，无新增后端端点；后端按原有 generic `ui.update` 保存，前端 `sanitizeShortcuts` 丢弃非法/保留组合。
- 规范化使用 `Mod`：Win/Linux 显示 Ctrl，macOS 显示 Cmd（Meta）；支持显式 `Ctrl`/`Meta`/`Alt`/`Shift`。
- 保留组合：浏览器/窗口生命周期（F5、Mod+R/W/T/N/L/O/P/U/J/H/Q、Mod+Tab 等）、编辑器/终端编辑键（Mod+S/V/C/X/A/Z/Y 等）、Mod+Shift+DevTools 组合、Escape/Tab/Enter/Backspace/Delete。录制时拒绝；持久化加载时丢弃。
- 冲突检测覆盖动态 panel 动作；录音时拒绝重复组合；已持久化的冲突在设置中标记且 dispatcher 不执行任一动作。

## 5. 注册与守卫

- 全应用仅一个 `document keydown` 监听（`useGlobalShortcuts`）；App、CommandPalette、SessionHistorySearch 的旧监听已移除，避免新旧双触发。
- 忽略：`defaultPrevented`、IME `isComposing`/229、`repeat`、打开 `aria-modal` 弹窗、input/textarea/select/其他 contenteditable、Monaco、xterm。
- 显式作用域：Queue/Session 动作允许在 `.composer-editor` 内触发（否则“边输入边锁定入队”不可用）；General/Panel 动作在 composer 内仍不触发。
- 录制器在 window capture 阶段拦截按键并 `preventDefault + stopImmediatePropagation`，录制期间全局分发放置；Escape 取消录制而不关闭弹窗。
- 动态 panel 刷新：Layout 首次加载、窗口 focus、打开 Shortcuts 页时 `load(true)`；无轮询。注册失败保留上次目录并显示提示，不崩整页；移除后动作立即消失，已保存绑定保留且不会串到其他 panel。
- 异步切 Session：锁定入队桥只接受启动时 Session；Stop report/bell 捕获 Session id；失败不自动重试，后继 Session 不会被操作。

## 6. 验证结果

### 前端

- 全量 vitest（最终微调前）：**175 files / 2089 tests passed**。
- Tab 顺序调整后定向：**110 tests passed**（AppSettingsModal、ShortcutsSettings、shortcuts、appSettingsStore、sessionQuickActions）。
- composer 作用域最终调整后定向：**86 tests passed**（useGlobalShortcuts、registry*、InputRow）。
- `tsc -b`：通过。
- `pnpm build`：通过（r8 e2e runner 内执行，72 compressed assets）。
- 变更文件 ESLint：exit 0（`frontend-eslint-changed.txt`）。全量 lint 仍有既有 e2e `no-undef` 错误（未触碰文件），非本分支引入。

### 后端

- 定向隔离测试：**16 passed / 2 deselected**，JUnit `failures=0`。
  - `test_put_ui_shortcuts_round_trip_stable_ids_and_explicit_clear`
  - `test_real_http_locked_enqueue_is_not_handed_off_before_unlock`
  - `test_real_http_report_and_bell_toggles_persist_across_restart`
  - 既有 settings UI 回归
- 全量 pytest：**未完成**。此前 Job 在 27% 被取消；已从原始日志的进度流解析出 `F` 位于收集顺序第 **104**、**603** 个，并用 `--collect-only` 顺序定位为：
  - `tests/test_backend_perf_opt.py::test_result_flushes_debounced_blocks_through_read_stdout`（`fake_save()` 缺少 `offline_override` 参数）
  - `tests/test_codex_resumed_init.py::test_resumed_init_marks_worker_ready_without_replaying_queue_or_history`（`native_kernel_ready` 仍为 False）
  - 两个节点在当前分支定向复现均失败；用 `git archive e4078c18` 的临时基线树只跑这两个节点，**同样 2 failed，错误签名一致** → 属基线既有失败，不是本分支引入。
  - 详见 `pytest-suspects-identity.md`、`pytest-suspects-rerun.xml`、`pytest-suspects-baseline.xml`、`full-pytest-cancelled.txt`。
- 未重跑全量本地 pytest，未改断言/skip。

### Chromium 真实键盘 e2e

- 结果：**11/11 passed**，exit 0。
- 隔离：动态端口 `37560`，临时 `PAN_E2E_RUNTIME`，`PAN_TERMINALS_DIR` 指向临时根，`tests/support/fake_stream_cli.py`，未访问 8767/8768 或实用 data。
- 清理：`remainingListeners: []`，stoppedPid `37636`；无残留 terminal launcher 进程。
- 覆盖：录制→执行→刷新持久化、保留/冲突、动态双 plugin 注册增删与绑定隔离、IME/composer/编辑器/真实 xterm 避让、无 Session 门禁、拒绝入队保留草稿、附件+URL 顺序、长按只触发一次、切 Session 竞态、队列展开/Lock all/Pause 语义、Stop report/bell 刷新往返。
- IME：CDP `Input.imeSetComposition` 真实 composition 可用；同时验证 synthetic `isComposing` 守卫。
- Terminal：隔离 fixture 缺少 terminal emulator sidecar，`POST /api/terminals` 返回 `startup-failed`；用例选中该次创建留下的真实 registry 记录，挂载真实 `.xterm-helper-textarea`，聚焦后按 Ctrl+K/Ctrl+B，断言命令面板不打开、侧栏不切换（`terminalAvailable=true`，`terminalEngineStarted=false`）。

## 7. 证据文件

- `browser-results.json` — 11 个用例逐项结果
- `e2e-lifecycle.json` / `e2e-cleanup.json` — 端口、fake CLI、清理
- `pytest-targeted.xml` — 16 个后端定向用例
- `full-pytest-cancelled.txt` — 被取消全量 pytest 原始日志（保全）
- `pytest-collect-order.txt` — 4125 个测试的收集顺序
- `pytest-suspects-identity.md` — 两处 F 的定位、复现与基线对照
- `pytest-suspects-rerun.xml` / `pytest-suspects-baseline.xml` / `pytest-suspects-baseline.txt` — 当前分支与 e4078c18 的定向结果
- `frontend-eslint-changed.txt` — 变更文件 lint（空即通过）

关键文件 SHA-256：

```
675bb47505ec0ae0891423fc995c8c5d065105f71a9af8582804f6723251cc50  packages/web/src/shortcuts/bindings.ts
c07f936900e1ba186d2ac1f614b4c614639bfe4d80703caf26b87cb0b2abb755  packages/web/src/shortcuts/registry.ts
0c4c13b76f9ea0c420f85e81e65c8e6d63bfd3ea9bdf84bd81f060d2184a144b  packages/web/src/shortcuts/useGlobalShortcuts.ts
cd96db5267e2833b2306e98c523e10a6e235c4bf4e187eb4c6fd6746151c9ff5  packages/web/src/components/layout/ShortcutsSettings.tsx
aa3d65cbf5195ba9f1b714e4c247ea6b1c2a77eec6006d10165138653c84f07f  packages/web/src/components/layout/AppSettingsModal.tsx
3805163f4601e0e7d4d6a53f114e028fd4fe15e2790ee52eb43cf2a1111c6399  packages/web/src/components/layout/viewEntries.ts
34401db90422ece680fa1581e8d3c0c000aeaefacf25fe81f8091c947ef3c22c  packages/web/src/stores/appSettingsStore.ts
11cc2a86047174fe37610a8e433ef15372c7637bfd527cb114b761ab520e71c8  packages/web/src/components/chat/InputRow.tsx
6d777a5f325753af496e42494f4db50c5ed250e6bdc6d378b787e4784effec96  packages/web/e2e/shortcuts.e2e.mjs
76f1f55b4a873a69bd1a8d8af31d13aa44c274cf3607f9888f57c3db01956846  packages/web/e2e/server.py
8690ee385cb9c3a218d86babb801e1029c66a12477f2bcae955c90ace27429e5  packages/web/e2e/run-shortcuts.ps1
e4a414c4f94f21cb1ad5d57d18c59f68e97a5b1725136e410f7046f4c0b64b36  tests/test_settings_ui.py
85ddf5831e461c748f580fa18fa98918c62725d90c9c5bd498e37e9fba400fa1  tests/test_real_queue_http_e2e.py
```

## 8. 未验证 / 剩余阻塞

1. **全量 pytest 未完成**：取消前 2 个 `F` 已定位并在 e4078c18 基线复现，属既有失败；其余未跑部分仍未验证，不能声称全量后端绿。定向相关 16 个用例绿。
2. **Terminal engine/PTY**：隔离 fixture 缺 emulator sidecar，终端进程未启动；真实 xterm DOM 的焦点避让已验证，但 PTY/输入/快照链路未验证。
3. **真实 OS IME**：CDP composition 已验证，但未接入真实中文输入法进程；另有 synthetic isComposing 守卫。
4. **macOS 实机**：仅单测验证 Mod→Meta/Cmd 展示与匹配；没有 macOS Chromium 实机运行。
5. **main 组合**：未追合 `8c10b2fc`；Lifecycle/Behavior/Remote 分类、自启动与本次 Shortcuts 的合并冲突由 MA 处理。
6. 产品默认未变：原有 Ctrl/Cmd+B/1/2/K/F 保留；新 Queue/Session 动作默认未绑。
