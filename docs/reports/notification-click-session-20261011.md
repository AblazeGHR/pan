# 通知点击 → 打开/聚焦 Pan 并跳转 Session：可行性调查

> 历史调查/阶段记录，描述当时的基线与验证范围，不是当前操作指南。当前已合入版本的通知选项与 60 秒点击窗口限制见[用户手册](../USER_MANUAL.md#integrations)；真实 Windows OS 点击仍未验证。

> 研究分支 `research/notification-click-session-20261011`（base `b80b272c`，独立 worktree）。
> 本报告为**可行性调查**（不承诺完整产品实现）；所有实验限定在独立 worktree / 隔离浏览器 / 动态端口，未合并 main/practical、未推送、未部署、未重启真实服务、未改用户配置、未发真实 QQ 通知、未弹任何系统通知。
> 日期：2026-10-11。证据与命令见 `audit/notification-click/experiment-log.md`。

## 0. 结论速览（四问直答）

| # | 场景 | 可行性 | 关键依赖 | 本次验证程度 |
|---|------|--------|----------|--------------|
| 1 | Pan 页面已打开（前台或后台标签） | **可行** | 前端加 `Notification.onclick` + session 选择；聚焦由浏览器点击默认行为完成 | 原型已实现；真实 Chromium E2E 验证 URL 跳转与降级；jsdom 单测验证点击处理函数 |
| 2 | 浏览器进程已关闭 | **页面通知不可行**；需 Service Worker 持久通知 | SW 注册 + `showNotification` + `notificationclick`（本项目无 SW，需新增） | 未实现；方案与官方依据见 §4/§5 |
| 3 | Windows 原生通知（system channel）点击 | **现状不可行**；可用常驻 helper/WinRT 改造 | 常驻进程 + `BalloonTipClicked`（或 WinRT toast 激活，安装级） | 仅源码证明 + 安全模拟（不弹 UI）；**未真机验证** |
| 4 | 桌面/移动浏览器限制 | 桌面 Chrome/Edge 支持；**移动端必须 SW**，iOS 仅主屏 Web App | secure context（HTTPS/localhost）+ 用户手势授权 | 官方文档核实（§5） |

一句话：**"已打开的 Pan 页面点击通知 → 聚焦并跳到对应 Session"已有最小可行改法（本分支原型 + 真实浏览器验证）；"浏览器完全关闭后的冷启动跳转"和"Windows 原生通知点击"都超出纯页面通知能力，需要 SW 或系统级 helper，成本中等、均有明确的未验证项。**

## 1. 现有通知真实调用链（审计）

### 1.1 后端（唯一生产触点）

- 触发：worker 终态 `_publish_terminal_events`（`packages/core/worker.py:1379`）调用 `dispatch_completion_nonblocking(session, status, result)`，返回值内联进 `worker.result` 事件的 `notification` 字段（`worker.py:1401-1402`；事件结构 `worker.py:1382-1403`）。
- 门控：仅 `status == "done"` 且 `notification_settings.browser` 或 `.system` 为 true 才产生载荷（`packages/core/notifications.py:124-135`）；两者默认 **false**（`notifications.py:28-34`），存量 Session 默认无任何浏览器/系统通知。
- 浏览器载荷：`{title, body, browser, system}`（`notifications.py:130-135`）——**不含 sessionId/URL**；可定位 Session 的字段是事件顶层的 `sessionId`。
- 通道：WS 广播 `server.broadcast`（`packages/web/server.py:1631-1677`）→ dashboard `/ws`（`server.py:4806`）与 `/ws/agent`（`server.py:4983`，仅按 sessionIds 过滤 worker.result）。
- 系统通道：Windows 下走 `powershell.exe -EncodedCommand` 的 `System.Windows.Forms.NotifyIcon`：`ShowBalloonTip(5000)` → `Start-Sleep 6` → `Dispose`（`notifications.py:37-102`），**发射即失联的子进程，无任何点击回调**（全仓 grep 无 `BalloonTipClicked`/`OnClick`）。
- 提醒：`_deliver_due_reminders`（`server.py:335-353`）→ `dispatch_reminder`（仅 system，`notifications.py:169-177`）→ 广播 `notification.reminder`；前端**未消费**该事件（缺口，非本任务范围）。
- Session 存在性接口语义：`GET /api/sessions/{id}` 返回 HTTP 200 + `{"error":"Session not found"}`（`server.py:6149-6150`），**非 404**——前端降级判断不应依赖 HTTP 状态码。

### 1.2 前端（唯一浏览器通知发射点）

- `useWebSocket.ts` `worker.result` 处理：`browser=true` 且权限已授予时 `new Notification(title, { body })`，**原本无 onclick、无导航**（`packages/web/src/hooks/useWebSocket.ts:757-774`）。
- 权限申请入口：PostboxModal 内的按钮（`components/session/PostboxModal.tsx:312-314`），完成事件从不主动弹权限。
- 路由：path 路由，prod basename `/react`（`router.tsx:17-18`）；Chat 视图 URL **不带** session（index 路由），唯一带 id 的路由是 `/manage/:sessionId`（管理视图，语义不同，不宜复用）。选中态在 zustand `currentSessionId`，`selectSession` 是唯一写入口且对不存在的 id **静默返回**（`stores/sessionStore.ts:2133-2138`）——天然降级点。
- 无 BroadcastChannel / storage 跨 tab 同步；无 SW、无 PWA manifest（`src/index.html` 无 manifest link，依赖无 vite-plugin-pwa）。

### 1.3 产品含义对齐（重要区分）

| UI/产品词 | 实际实现 | 点击行为（改动前） | 备注 |
|---|---|---|---|
| 「浏览器通知」(browser) | **Web Notification API**（页面上下文，系统级 toast） | 无效（无 handler） | 需权限 + secure context；移动端构造抛错 |
| 「系统通知」(system) | PowerShell NotifyIcon balloon | 无效（进程已退出） | Win10+ 上 balloon 的实际呈现官方无明文 |
| 页面 Toast | `components/ui/Toast.tsx` 页内 3 秒 | 复制到剪贴板（`Toast.tsx:39-48`），不跳转 | 不是系统通知 |
| 提醒 reminder | 仅 system + WS 事件 | 前端未消费事件 | 缺口 |
| PWA / SW | **不存在** | — | 冷启动路线的前置 |

## 2. 最小改法（本分支已实现原型）

改动 3 个产品文件 + 测试；不需要后端改动（事件顶层已有 `sessionId`）。

1. **新** `packages/web/src/utils/session-deeplink.ts`
   - `openSessionFromNotification(sessionId)`：存在性检查（store）→ `window.focus()`（best-effort）→ `selectSession` → 路由回 Chat（懒加载 router 单例，避免静态循环）；session 被删则 toast「通知对应的会话不存在或已被删除，无法跳转」。
   - `useSessionDeepLinkParam()`：冷启动 `?session=<id>` 消费；等首次列表加载完成才判定缺失；处理完用 `replace` 清掉参数。
   - 允许清单 `^[A-Za-z0-9_-]{1,64}$`（真实 `ses_<hex>` 与演示 slug 均通过），非法引用直接拒绝。
2. `packages/web/src/hooks/useWebSocket.ts`：通知构造后挂 `onclick = () => openSessionFromNotification(sessionId)`。
3. `packages/web/src/views/ChatRoute.tsx`：挂 `useSessionDeepLinkParam()`（`/?session=` 与未来 SW `openWindow` 共用同一 URL 契约）。

行为要点：URL 只承载**不透明 sessionId**，不含通知正文；正文是否上锁屏沿用现状（body=任务结果，敏感场景可截断/泛化，属后续取舍）。外部地址（loopback/tunnel/tailscale）**与实现无关**：页面内点击是 SPA 跳转，冷启动链接是相对当前 origin 的路径，不需要配置任何 base URL。多 tab：每 tab 各自处理自己的 `?session=`（E2E 验证）；通知重复弹出是既有行为（每 tab 每条完成各弹一个），去重需 BroadcastChannel 选举，未做。

## 3. 实验证据

- 依赖复用（用户要求）：`package.json`/`pnpm-lock.yaml`/`vite.config.ts` 三方哈希一致（`audit/notification-click/dependency-hashes.txt`）；`tools/prepare_validation_env.ps1` 建立 junction 复用 `D:\project\Pan\packages\web\node_modules`（其含 `@xterm/*`；Pan-main 的共享安装陈旧缺包）——**未执行任何 install**。canonical Python 模块检查全 OK。
- 构建：`tsc -b` 干净；`vite build` + 压缩产物校验 `BUILD_OK`（66 项）。
- 单测：新增 `session-deeplink.test.tsx`（4）+ `useWebSocket.test.tsx`（+2）全过；定向 95/95。
- 全量对照：改动后 1442 过/2 失；基线 `b80b272c` 同环境 1436 过/**同样 2 失**（`App.test.tsx` 全量并行 flake + `EditorPane.test.tsx` Monaco 环境问题）→ 预存在、与改动无关（日志 `vitest-full-output.txt` / `vitest-baseline-full-output.txt`）。
- 隔离浏览器 E2E（生产构建 + `vite preview` + 动态端口 `127.0.0.1:61750` + `?mock=1` + headless Chromium 153.0.8010.12）：**6/6**——无参数不选中（对照）、`?session=` 命中并消费、未知会话降级 toast、非法引用拒绝、双 tab 互不串台、0 pageerror / 0 真实后端请求（`e2e-notification-deeplink.json`）。
- Windows 原生路线（安全模拟，无 UI）：`notifyicon-click-prototype.ps1 -Mode simulate` 对 loopback/tunnel 两个 origin 都通过 URL 构造与点击派发断言；非法 id 被拒。**明确声明：模拟 ≠ 真机**——balloon/toast 的真实点击、焦点、通知中心留存均未验证。

## 4. 替代路线（成本/风险）

| 路线 | 内容 | 成本 | 主要风险 |
|---|---|---|---|
| A. Service Worker 持久通知 | 注册 SW；页面→SW `showNotification`；`notificationclick` → `matchAll({type:'window', includeUncontrolled:true})` 逐个 focus 并通过 `postMessage` 让页面 selectSession；无窗口时 `openWindow('.../react/?session=<id>')`（URL 契约已就绪） | 中 | SW 生命周期与更新管理；`Client.url` 不随 SPA 路由更新（不能按 URL 精确匹配，官方明示"不适用 SPA"）；多窗口 focus 规则无官方明文；iOS 仅主屏 Web App |
| B. Windows 常驻 helper | 常驻进程 NotifyIcon + `BalloonTipClicked` → `Start-Process <deep link>`；或 WinRT toast + 协议/COM 激活 | 中高 | 常驻进程生命周期/自启/单例；Focus Assist 会抑制通知；balloon 在 Win10+ 的实际呈现官方无明文；多个待点击通知→目标选择需设计；WinRT 激活需安装级注册（AUMID/COM/protocol） |
| C. 后端补 sessionId/deepLink 到系统通道 | 让 `dispatch_completion` 的 system 分支携带 session 深链（前端通道不需要——事件顶层已有 id） | 低 | 与 B 配套才有意义；注意不泄漏正文 |
| D. 页内 toast 点击跳转 | `Toast.tsx` 点击 = 复制；可改为带 sessionId 的 toast 才跳转 | 低 | 语义变化需产品确认；非本任务必需 |

## 5. 官方资料依据（仅官方来源）

- 页面通知点击默认聚焦 viewport，且可在 listener 里 `focus()`：MDN `Notification: click event`（https://developer.mozilla.org/en-US/docs/Web/API/Notification/click_event）；WHATWG Notifications 规范激活算法（https://notifications.spec.whatwg.org/）。
- 页面通知生命周期绑页面、关闭后无法交互；移动端必须用 SW `showNotification`（构造抛 TypeError）；非持久通知"不应进入系统通知中心"：MDN Notifications API（https://developer.mozilla.org/en-US/docs/Web/API/Notifications_API）。
- SW 事件模型差异与不可取消：MDN `notificationclick`（https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerGlobalScope/notificationclick_event）。
- `openWindow`/`WindowClient.focus()` 需 transient activation（Firefox 明确允许由通知点击触发）；`Client.url` 不随 SPA 路由更新、示例"已打开则 focus 否则开窗"：MDN `Clients.openWindow`（https://developer.mozilla.org/en-US/docs/Web/API/Clients/openWindow）、`WindowClient.focus`（https://developer.mozilla.org/en-US/docs/Web/API/WindowClient/focus）、user activation 页（https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/User_activation）。
- 同源多窗口匹配：MDN `Clients.matchAll`（https://developer.mozilla.org/en-US/docs/Web/API/Clients/matchAll）。
- 权限须用户手势、secure context：MDN Using the Notifications API（https://developer.mozilla.org/en-US/docs/Web/API/Notifications_API/Using_the_Notifications_API）、MDN `Notification`（https://developer.mozilla.org/en-US/docs/Web/API/Notification）。
- iOS 16.4+ 仅主屏 Web App 可收 Web Push：WebKit 官方博客（https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/）。
- `requireInteraction` 语义：MDN（https://developer.mozilla.org/en-US/docs/Web/API/Notification/requireInteraction）。
- `BalloonTipClicked` 事件存在；`NIN_BALLOONUSERCLICK` 经回调消息投递：Microsoft Learn（https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon.balloontipclicked、https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyicona）。
- Toast 激活需订阅 `Activated`/AUMID；后台激活需本地 COM server；打包才支持后台通知与 COM 激活：Microsoft Learn（https://learn.microsoft.com/en-us/windows/win32/shell/quickstart-sending-desktop-toast、https://learn.microsoft.com/en-us/previous-versions/windows/desktop/win32_tile_badge_notif/respond-to-toast-activations、https://learn.microsoft.com/en-us/windows/apps/develop/notifications/）；toast 可携带 `Launch`/`arguments` 字符串（https://docs.microsoft.com/en-us/windows/apps/design/shell/tiles-and-notifications/toast-schema）。
- **未找到官方依据**（如实声明）：Chrome 无手势时自动拒绝权限的明文；denied 后再请求；Win10+ balloon 转 toast 的官方说明；Chrome/Edge 点击通知后窗口激活的官方说明；操作中心留存与浏览器关闭关系；多窗口 focus 选择规则。

## 6. 可实现范围与未验证项

已可交付（本分支原型）：已打开页面场景的点击跳转 + 聚焦（浏览器默认）+ session 被删降级 + 冷启动 `?session=` URL 契约 + 多 tab 隔离，均已带测试。

未验证 / 未实现：
- 真机 OS 级点击（页面通知与 balloon）与焦点行为（含最小化、多显示器、Focus Assist）；
- 浏览器关闭后的 SW 冷启动（SW 路线未实现）；
- 同 origin 多窗口时浏览器聚焦哪个窗口（官方无明文）；
- tunnel/tailscale 域名下 secure context/权限的真机表现（仅验证 URL 构造与外部地址无关性）；
- 移动端真机（规范已表明必须 SW）；
- 通知正文上锁屏的隐私取舍（现状 body=结果文本，未改）；
- 多 tab 重复通知的去重（未做）。

建议后续（若产品化）：先落 A 路线的 SW 最小闭环（冷启动/浏览器关闭）；Windows 原生路线单独立项做常驻 helper 真机验证；顺手可考虑把 `notification.reminder` 接入前端消费（当前事件被丢弃）。

## 7. 产物索引

- `audit/notification-click/experiment-log.md`：实验日志（命令、时间、结果）
- `audit/notification-click/dependency-hashes.txt`：三方依赖哈希证据
- `audit/notification-click/validation-env-output.txt`：prepare_validation_env 运行日志（`*.log` 被仓库 gitignore，以 .txt 留存）
- `audit/notification-click/vitest-full-output.txt`、`vitest-baseline-full-output.txt`：全量对照
- `audit/notification-click/e2e-notification-deeplink.json`：隔离浏览器 E2E 结果
- `audit/notification-click/notifyicon-click-prototype.ps1`、`native-simulate-result*.txt`：原生路线模拟
- `audit/notification-click/prototype.patch`：本次提交的完整源码补丁（便于评审）
- 产品原型改动：`packages/web/src/utils/session-deeplink.ts`（新）、`packages/web/src/hooks/useWebSocket.ts`、`packages/web/src/views/ChatRoute.tsx`；测试 `packages/web/src/utils/session-deeplink.test.tsx`、`packages/web/src/hooks/useWebSocket.test.tsx`；E2E `packages/web/e2e/notification-deeplink.mock.mjs`
