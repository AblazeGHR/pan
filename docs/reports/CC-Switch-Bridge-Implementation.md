# CC Switch Bridge 与插件 Panel 基础设施交付

2026-10-18。实现仅位于 `D:\project\pan-worktrees\plan-plugin-manager-20261017`，当前分支 `feature/plan-plugin-manager-20261017`。本轮未合并 main/practical，未构建 practical，未重启生产服务。main/practical 期间被其他任务推进，不将其内容自动引入本轮。

## 结果

实现了 [插件 Panel 协议](../design/Pan-Plugin-Panels-v1.md)、共享导航入口、插件数据目录与版本归属、声明式 action 分发和面板操作去重。Panel 与 Session MCP 开关独立，不生成持久 Session 或 Worker。首个 renderer 为 `account-switcher/v1`，后续其他类型通过带版本 renderer 扩展，不加载任意远程 JS。

独立插件 **cc-switch-bridge** 只通过原 cc-switch CLI 的公开命令接入；没有改动或打包原 cc-switch、没有直接操作其数据库。Pan 中保存自己的设置、额度缓存和操作台账。使用本机 CLI 源码核对接口，未复制 GUI 代码。

MCP 暴露两个能力 `switch`、`quota`；三个 MCP 模式均按 `agent_capabilities` 过滤。Panel 还可以读取列表和切换 ChatGPT 默认账号，这些内部能力不额外成为 MCP 工具。quota 不传 id 时仅返回 ID、当前选择和缓存，传 id 才查询该配置的远程额度。

Panel 提供 Codex/Claude/Gemini 配置页签、名称/账号/地址搜索、当前配置、ChatGPT 默认账号、手动额度查询、百分比/余额、缓存时间和额度重置时间、切换确认及错误处理。默认账号和特定 provider 绑定的语义分开显示；默认账号改变后清除 bridge 的 Codex 额度缓存。切换前后检查原 CLI 的当前状态，不自动重启 Worker。

## 验证证据

- 后端相关回归 **461 passed，4 warnings，134.38 秒**，覆盖原有插件、QQ、Session、MCP 上下文、持久化、队列 restart 等，以及 Panel/bridge 测试。见 [JUnit](../../audit/cc-switch-bridge-20261018/backend.xml)。四项为已有 Pydantic/第三方弃用提示。
- 最后的 helper 总时限及跨进程锁收尾之后，独立运行 **14 项桥接专项测试通过**；与前述回归有重叠，不相加。见 [最终专项](../../audit/cc-switch-bridge-20261018/panel-tests.xml)。覆盖真实 stdio framing/中文、三个模式的两工具过滤、Panel API、内部能力不可由 Agent 直接调用、目录版本、状态过期拒绝、持久化请求去重、取消后的结果落盘、锁冲突和 CLI 错误脱敏。
- 前端 **105 项相关测试通过**，TypeScript 与变更文件 ESLint 通过。工作树 build 通过并验证 **70 个压缩资源**；保留已有 router 导入/大 chunk 提示。见 [前端记录](../../audit/cc-switch-bridge-20261018/frontend.log.gz)。
- 真实 Chromium 使用生产 Panel/导航组件，后端为有状态拦截 fixture；完成显示、手动查询、取消确认、模拟配置/默认账号切换、搜索、390px 宽度与内部滚动宽度检查。见 [浏览器结果](../../audit/cc-switch-bridge-20261018/browser-result.json)、[桌面](../../audit/cc-switch-bridge-20261018/desktop.png)、[移动](../../audit/cc-switch-bridge-20261018/mobile.png)、[移动账号区](../../audit/cc-switch-bridge-20261018/mobile-accounts.png)。它不是原 cc-switch 的真实业务端到端验收。

**所有切换均为假 CLI / fixture 操作；未运行真实 cc-switch 命令，没有真实账号或配置切换。** 查询虽已获用户允许，本轮仍未借此触碰真实 CLI 数据。原 CLI 命令兼容性按本机源码核对，真实交互由用户执行 [真机步骤与预期清单](CC-Switch-Bridge-Manual-Acceptance.md)。

## 实用边界

原始 cc-switch 同时操作不参加 bridge 文件锁，因此仍需以刷新结果为准。面板去重是单 Pan 实例、有限记录窗口的机制，不宣称永久 exactly-once。MCP 切换不经过面板 requestId 台账，不能自动重放未知结果。

新增/删除/编辑账号、登录、兑换额度重置券和 cc-switch 代理启停未实现。原产品自身的查询可能刷新其凭据/缓存，本桥接不承诺未来真实查询完全不产生原产品内部写入。Panel 首批为宿主管理 renderer，未提供任意第三方 UI bundle 安装或市场。
