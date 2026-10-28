# Remote 设置页视觉与信息层级统一 — 候选报告

- 分支：`fix/remote-settings-ui-20261028`
- 基线：`511784fe43107a3a290236841ddb9b618c1cf709`（main / practical）
- 代码候选：`0f3f0589`
- 工作树：clean；未 push，未改动 main / practical，未部署或重启任何既有服务。

## 改动范围

- 新增 [RemoteSettingsSection.tsx](../../packages/web/src/components/layout/RemoteSettingsSection.tsx)：Remote 分区外壳（标题 / 简短说明 / 状态摘要 / 键盘可折叠）、统一状态行、字段标签、开关和输入控件样式。
- [AppSettingsModal.tsx](../../packages/web/src/components/layout/AppSettingsModal.tsx)：仅 remote 分支的 `Remote / Tunnel` 区块改为同一分区外壳并保留 Restart 行为；新增两处局部 import。
- [LanSettings.tsx](../../packages/web/src/components/layout/LanSettings.tsx)、[TailscaleSettings.tsx](../../packages/web/src/components/layout/TailscaleSettings.tsx)、[McpRemoteSettings.tsx](../../packages/web/src/components/layout/McpRemoteSettings.tsx)：重排信息层级、字段/开关/动作分组，保留全部原术语、说明、禁用条件和请求逻辑。
- [RemoteSettingsSections.test.tsx](../../packages/web/src/components/layout/RemoteSettingsSections.test.tsx)、[AppSettingsModal.test.tsx](../../packages/web/src/components/layout/AppSettingsModal.test.tsx)：聚焦交互与分区回归测试。
- [remote-settings-ui.e2e.mjs](../../packages/web/e2e/remote-settings-ui.e2e.mjs)：独立 Chromium 验证 harness（真实源码 dev server + 同源生产构建 + 记录型 API mock）。

未修改后端 API、配置 schema、默认值、权限、网络启停逻辑或其他设置页行为。折叠只切换显示（`hidden`），子组件保持挂载，因此未保存草稿不丢失、不重复 GET、无副作用。

## 验证

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| TypeScript | `pnpm exec tsc --noEmit` | exit 0 |
| 聚焦测试 | `pnpm exec vitest run src/components/layout/RemoteSettingsSections.test.tsx src/components/layout/AppSettingsModal.test.tsx` | 70 passed（5 + 65） |
| 生产构建 | `pnpm build` | 成功；仅既有 chunk 体积警告 |
| 浏览器 | `node e2e/remote-settings-ui.e2e.mjs` | exit 0 |

浏览器验证覆盖：

- 四个分区顺序：Remote / Tunnel → MCP 远程连接 → Tailscale 备用访问 → 普通局域网访问（LAN）。
- 四个分区标题、说明、状态摘要可见；折叠/展开；键盘 Tab 焦点环（2px solid）、Enter/Space 切换。
- 折叠前后未保存的 MCP 域名与 LAN 端口保留，`/api/remote/mcp`、`/api/remote/lan` 无重复 GET。
- 原有动作端点与载荷：`PUT /api/remote/lan`、`POST /api/remote/lan/start|stop`、`PUT /api/remote/tailscale`、`POST /api/remote/tailscale/start|stop`、`POST /api/remote/mcp/start`、`POST /api/remote/restart`。
- MCP 加载 500 与 LAN 状态错误均显示在对应分区内，状态仍保留原含义。
- 1280px 与 390px 无横向溢出；长 LAN/公网地址在网格列内换行（`scrollWidth == clientWidth`），复制按钮可用且剪贴板内容正确。
- 生产构建 harness（同一源码）重复分区顺序与折叠断言；主 `packages/web/dist` 的 `AppSettingsModal-ijfGjV5E.js` 含新分区标记。
- 未访问 8767/8768、真实 Tunnel / Tailscale / MCP；`protectedRequests` 为空，page errors 为空，唯一 console error 是刻意制造的 MCP 500。

截图与原始证据：

- [desktop-1280-expanded.png](desktop-1280-expanded.png)
- [desktop-1280-collapsed.png](desktop-1280-collapsed.png)
- [desktop-1280-status-error.png](desktop-1280-status-error.png)
- [desktop-1280-load-error.png](desktop-1280-load-error.png)
- [mobile-390-expanded.png](mobile-390-expanded.png)
- [mobile-390-collapsed.png](mobile-390-collapsed.png)
- [dist-1280-remote.png](dist-1280-remote.png)
- [browser-verification.json](browser-verification.json)

## 未验证 / 说明

- 未启动真实 Tunnel、Tailscale、MCP 远程服务，也未使用真实 `data`、profile、8767/8768。
- 完整安装态 App 的 dist 启动需要真实后端；本次生产构建验证使用同一源码、同一 Vite 插件构建出的 harness，并额外校验主 dist chunk 含新分区标记。
- 既有构建警告（动态/静态混合导入、>500 kB chunk）未变化，不属于本任务范围。
