# CC Switch Bridge：非手动切换门禁

2026-10-25。工作树 `D:/project/pan-worktrees/cc-switch-mcp-gates-20261025`，分支 `feature/cc-switch-mcp-gates-20261025`，基线 `399b86a9`。本轮只提交候选并自证：未移动 main/practical，未 push，未跑远端 CI，未启停服务，未触碰 8767/8768。

## 语义

只有一个区分：**手动切换**（CC Switch 面板里用户显式点击）与**非手动切换**（所有 Agent/MCP 路径，含未来自动换号）。存储上只有每个配置一个标记 `nonManualDisabled`，不拆分 MCP 与自动换号两种禁用。

- **非手动**：`list`/`quota` 完全隐藏被标记配置（不返回名称、origin、quota、notes、category 等任何字段，也不用 `disabled: true` 占位）；按 id 读取按“配置已不存在”拒绝；`switch`、`auto_switch` 拒绝被标记目标，不回退到其它配置，不重放未确认结果。
- **手动**：面板的 `panel_switch` 允许显式切换到任何配置，包括已标记的；面板列表始终返回全部配置、标记值和设置版本。
- 被标记配置的名称、来源、额度等仍由面板展示和管理，但只通过不导出给 Agent 的 `panel_*` 能力。

## 能力与面板

| 能力 | 受众 | 说明 |
| --- | --- | --- |
| `cc-switch-bridge.list` | Agent | 隐藏被标记配置 |
| `cc-switch-bridge.quota` | Agent | 隐藏；按 id 读被标记配置拒绝 |
| `cc-switch-bridge.switch` | Agent | 非手动切换；拒绝被标记目标 |
| `cc-switch-bridge.auto_switch` | Agent | 自动换号唯一入口；同一门禁，不静默回退 |
| `cc-switch-bridge.panel_list` | 面板 | 全部配置 + `nonManualDisabled` + 应用级 `revision/updatedAt` |
| `cc-switch-bridge.panel_quota` | 面板 | 可查被标记配置的额度 |
| `cc-switch-bridge.panel_switch` | 面板 | 手动切换，允许被标记目标 |
| `cc-switch-bridge.panel_config` | 面板 | 保存标记；携带 `expectedRevision`，冲突返回 `settings_conflict` |
| `cc-switch-bridge.default_account` | 面板 | 原有托管账号默认切换，语义不变 |

面板 action 为 `list/quota/switch/config/auto_switch/default_account`；`auto_switch` 保留给未来的自动换号机制，界面按钮不会调用它。Agent 只看到 `agent_capabilities` 中的四项，`panel_*` 不出现在任何 MCP 投影。

## 持久化

`<plugin data>/provider-flags.json`，按 app 保存 `revision`、`updatedAt` 与各配置的 `nonManualDisabled`。写入使用临时文件 + `fsync` + `os.replace` 原子替换，读改写复用既有 `.bridge.lock` 跨进程锁；不读写 cc-switch CLI 数据库，也不改变 CLI 行为。设置文件损坏时显式报 `settings_invalid`，不静默丢弃门禁。

## 验证

- 后端专项（`tests/test_plugin_panels.py`、`tests/test_cc_switch_bridge_gates.py`、`tests/test_plugin_panel_builds.py`）：**42 passed**。覆盖门禁往返、revision/updatedAt、两配置互不影响、并发写入无丢失且无残留临时文件、旧 revision 冲突不改文件、Agent 列表/按 id 额度完全隐藏且不泄露字段、面板可见全部、非手动 `switch`/`auto_switch` 拒绝且不切换、手动 `panel_switch` 允许被标记目标、面板配置写入驱动真实 runtime 的 Agent 投影。
- 后端相关回归（`tests/test_plugin_*.py`、`tests/test_mcp_integration.py`、`tests/test_manifest_loader.py`、`tests/test_provider_launch_export.py`、`tests/test_quota_provider_source.py` 与本轮新文件）：**208 passed / 0 failed**，隔离运行（`scripts/run_tests_isolated.py`）。见 [backend.log](../../audit/cc-switch-non-manual-gate-20261025/backend.log) 与 [JUnit](../../audit/cc-switch-non-manual-gate-20261025/backend.xml)。
- 仓库全量后端为 **5714 项**，单轮耗时远超本任务预算，本轮未跑完；只运行上述相关回归。`tests/test_local_plugin_panels_http.py` 等浏览器型用例不在本轮范围。
- 前端：`PluginPanelView.test.tsx` **39 passed**；全量 **2454 passed / 3 skipped / 2 failed**，两项失败为分支基线已有的 `demo/mockBackend.test.ts`（演示模式文案本地化漂移）与 `hooks/reaudit.orderedEvents.test.tsx`（E1b tool 事件字段），均不涉及本轮改动文件。
- `tsc -b` 退出 0；`pnpm build` 退出 0，验证 90 个压缩资源。
- 真实 Chromium（生产面板组件 + 有状态拦截 API，未运行 cc-switch）：1440 与 390 截图、门禁保存请求、隐藏标识、revision 冲突提示与自动重读。见 [桌面 1440](../../audit/cc-switch-non-manual-gate-20261025/desktop-1440.png)、[窄屏 390](../../audit/cc-switch-non-manual-gate-20261025/mobile-390.png)、[浏览器结果](../../audit/cc-switch-non-manual-gate-20261025/browser-result.json)。

**所有切换均为 fixture；未运行真实 cc-switch 命令，未触碰真实账号或配置。**

## 边界

- 原始 cc-switch 不参与 `.bridge.lock`，外部同时操作仍需以刷新结果为准。
- Agent 的 `currentProviderId` 仍反映 CLI 当前选择；若用户手动切到被标记配置，该 id 会出现在 Agent 列表的当前值里，但被标记配置自身的行与字段仍然完全隐藏，按 id 的读取/非手动切换仍拒绝。
- `settings_invalid` 时 Agent 读取直接失败（显式），不会把损坏文件当成“无门禁”继续工作。
- 真机 cc-switch 交互与真实自动换号机制不在本轮范围；`auto_switch` 目前只提供受同一门禁约束的 API。
