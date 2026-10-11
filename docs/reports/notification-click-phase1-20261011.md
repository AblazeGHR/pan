# 通知点击 → Session 深链：阶段一正式实现与验证

> 研究分支 `research/notification-click-session-20261011`，本阶段提交在 `ca81ed58` 之上追加（保留历史）。
> 基线 main = practical = `b80b272c`，未移动；未合并、未推送、未部署、未重启服务、未改用户配置、未弹真实系统通知。
> 可行性调查见 `docs/reports/notification-click-session-20261011.md`；实验日志与原始证据在 `audit/notification-click/`。
> 快进（fast-forward）与 practical build 按约定留待 MA 独立验收后执行。

## 1. 阶段范围

仅"Pan 页面存活（前台或后台标签）"的 Web Notification 点击：聚焦并准确跳转通知对应 Session、回到聊天视图。

不在本阶段、行为未改变：Windows system NotifyIcon / SW / Push / 浏览器关闭后的通知。后端 `notifications.py`、WS 广播、通知开关与发送路径均未改动；`?session` URL 契约保留（可靠化）。

## 2. MA 收紧扣分 → 实现对照

| 复核意见 | 处理 |
|---|---|
| fire-and-forget select + lazy import 可能迟到覆盖用户操作 | `openSessionFromNotification` 改为 async；单调 intent 计数 + 起始 selection/位置快照守卫；**先路由后选择**，navigate 落定且选择同步应用后才返回 true；被更新 intent supersede 的旧点击静默丢弃（新动作已获胜） |
| 只凭缓存 sessions 不存在不能判删除 | `lookupSessionRef`：缓存 miss → 等待在场刷新落定 → 发起权威列表加载；仅"我们发起、未被 supersede、同 serverEpoch、已完成"的加载缺该 id 才判 `missing` |
| `_loadSeq>0 && loading=false` 含失败，不能把失败说成删除并清深链 | 加载失败返回 `unknown`：点击路径 toast「会话列表暂时无法加载」；`?session` 路径**保留参数**、警告一次，后续成功刷新（缓存命入）自动恢复 |
| router 异常被吞、true 非实际完成 | `navigateToChat` 失败 → toast「无法返回聊天视图」+ 返回 false；不再吞异常 |
| 用现有路由/选择契约，不滥加全局状态 | 守卫用模块级 intent 计数 + hook 内 ref；判定/选择复用 store 的 `loadSessions`/`selectSession`（含 `_selectionSeq`）既有契约 |
| `?session` 保留或精简均可，但留下路径必须可靠 | 保留并可靠化：只删 `session` 参数（其它 query 与 `/react` basename 不受影响）；失败保留可恢复；区分真实缺失与加载失败 |
| 正文不进 URL、无任意外链、不发真实 QQ/通知 | URL 只携带校验过的 sessionId（`^[A-Za-z0-9_-]{1,64}$`）；E2E 断言 0 外部请求（应用既有字体 CDN 在测试中封禁）；全程未发任何真实通知 |
| 复用现有组件/store/helper，不引入 SW/无关设置 | 仅前端 2 文件改动 + 新 util；无 SW、无设置面变化 |

## 3. 代码改动

- `packages/web/src/utils/session-deeplink.ts`（重写）：`openSessionFromNotification`（async + 守卫 + 权威判定 + 路由/选择顺序）、`useSessionDeepLinkParam`（冷启动，失败保留/恢复）、`lookupSessionRef`（settle + 有界双轮，自愈 supersede）、`isValidSessionRef`。
- `packages/web/src/hooks/useWebSocket.ts`：`desktopNotification.onclick = () => { void openSessionFromNotification(sessionId); }`。
- `packages/web/src/views/ChatRoute.tsx`：不变（阶段一未改；阶段 0 已挂 hook）。
- 测试：`session-deeplink.test.tsx`（6）、`useWebSocket.test.tsx`（95，含 6 个点击链用例）。
- E2E：`e2e/notification-deeplink.real.e2e.mjs` + `e2e/notification-deeplink.server.py`（隔离真实后端）、`e2e/notification-deeplink.mock.mjs`（URL 回归）。

## 4. 实现中发现的两个故障性发现

1. **冷启动 supersede 竞态（已修复）**：hook 的权威加载与 Layout 的初始 `loadSessions` 同任务竞速被 supersede（loadSessions 对被取代的响应静默丢弃），旧逻辑落入 unknown 且不再重试 → `?session` 不收敛。修复：`settleLoads`（等在场加载，15s 上限）+ 有界两轮重试；新增单测锁定。
2. **删除竞态（观察到，范围外，未修复）**：删除 worker 存活/刚完成的 Session 时，DELETE 先删存储、worker 清理为异步；worker 侧迟到保存可把 Session 写回（服务端列表复活，E2E 诊断转储为证）。E2E 用 kill→delete 循环建立确定前置。建议独立任务核查该后端删除竞态（阶段一不改变删除路径）。

## 5. 验证结果

- 定向单测：101/101（deeplink 6 + useWebSocket 95）。
- 全量 vitest（phase1b）**1448 过 / 2 失：与基线同两名**（`App.test.tsx` 全量并行 flake；`EditorPane.test.tsx` Monaco 环境），断言未削弱；phase1 首跑多出的一次 `SessionList.drag.realBackend` flake：单跑 16/16、复跑全量消失、与被改模块无 import 边（日志 `vitest-phase1-full.txt`、`vitest-phase1b-full.txt`）。
- 隔离真实后端 E2E（真实 React 生产包 + 真实 FastAPI/Worker/队列/WS + 确定性假 CLI；动态端口；数据根在 `packages/web/test-results/notify-*`）：**10/10**
  1. 真实 worker.result→通知创建（标题=Session 名，正文=结果文本）
  2. onclick（其它视图）→回聊天+准确选中该 Session、URL 无 session/正文泄漏
  3. browser=off 的 Session 完成时静默（无新增通知）
  4. 点击前删除→删除 toast、不跳转、不误选
  5. 列表加载失败：不清深链、不误报删除、警告后随成功刷新恢复（其余 query 保留）
  6. 慢加载期间用户抢先选择→用户选择获胜
  7. 连击只生效一次
  8. 双 tab 隔离：点 A 不影响 B
  9. 后台标签（headless 下 visibilityState=visible，如实记录）handler 正常应用
  10. 0 外部请求 / 0 pageerror / 字体 CDN 封禁计数 5
- mock/URL E2E 6/6（含其它 query 保留、双 tab、非法引用拒绝）。
- 后端回归 `tests/test_notifications_reminders.py` 12 passed（通知开关/发送/提醒链未动）。
- 质量门：tsc 干净；scoped eslint（7 文件）全绿；`vite build` + `verify-precompressed-assets`（66 项）通过；`git diff --check` 干净。
- 依赖与隔离：三方 `package.json`/`pnpm-lock.yaml`/`vite.config.ts` 哈希一致后经 `prepare_validation_env`（canonical=Pan practical 完整依赖）复用 junction，未安装、未写共享依赖；未触 8768/8767；E2E 全程仅与动态端口隔离实例通信。

## 6. 未验证 / 边界（如实声明）

- 真实 OS 通知的显示与点击、浏览器窗口聚焦：属浏览器/OS 边界；E2E 以 stub 捕获实例并程序化触发应用挂载的 `onclick`（真实事件链的 handler 侧），**未验证 OS 投递**，全程未弹真实系统通知。
- 浏览器关闭后的冷启动（SW/Push）不在阶段一。
- headless 下后台标签 `visibilityState` 为 `visible`（Chromium headless 未隐藏后台页），后台场景按"handler 在非聚焦页仍工作"验证并如实记录。
- 多窗口同 origin 的浏览器聚焦选择规则（官方无明文）。
- 删除/live-worker 竞态（§4）为范围外观察，未修复、未深挖。

## 7. 交付

- 本阶段实现提交（见 `git log`，位于 `ca81ed58` 之后）；完整 SHA 证据见 `audit/notification-click/sha-evidence.txt`。
- 证据产物：`audit/notification-click/`（含 phase1 全量日志两份、real/mock E2E JSON、实验日志）。
