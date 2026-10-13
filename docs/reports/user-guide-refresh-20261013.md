# 用户文档核对记录（2026-10-13）

审计基线：`a1351d113a4a26e8ae1521d63ae4ce6e277f13c4`。工作树 `D:/project/pan-worktrees/user-guide-refresh-20261013`，分支 `docs/user-guide-refresh-20261013`；开始时 HEAD 与基线一致、工作树干净。已阅读 canonical `.workflow/constraints.md` 与 overview；实现事实以本工作树已合入代码为准，不依据 overview 的历史状态推定可用性。

## 用户入口与结论

- 更新 [中文 README](../../README.md)、[English README](../../README.en.md)、[中文手册](../USER_MANUAL.md)、[English manual](../USER_MANUAL.en.md) 和 [MA 面向用户的标准回答](../skills/pan/references/user-guide.md)。安装/入门、帮助及 FAQ 位于这些入口的快速开始、首次 Session 与排障章节；未发现独立跟踪的 FAQ/help/getting-started 文件。
- QQ/SnowLuma 中英文指南、MCP 远程指南和 Tailscale 高级说明已查看；保留其专门配置说明，通过用户手册链接高级内容。无关历史报告与未跟踪设计不整理、不改写。
- 过时内容：Session 右键 Pin 已迁到行快捷操作；Memory 不应作为当前可启用功能；标准回答不应承诺无配置、无资源限制的自动团队和无条件主动汇总。
- 缺失内容：Session/Workspace 搜索与状态筛选、Read all 范围、批量 Actions、队列暂停与报告停止区别、Send/Steer 即时反馈及未知结果、Job pin、通知默认 QQ 联系人与搜索、未读策略覆盖、Codex reimport 确认、移动入口/Terminal TUI、手动报告可证明窗口、self queue 用途边界。现已在中英文手册以简短操作说明补齐。
- 三份通知调查增加历史标识和当前手册链接，保留其当时基线、实验与未验证记录。

## 代码核对依据

| 主题 | 基线中的依据 |
|---|---|
| 搜索、筛选、Workspace、Read all | `packages/web/src/utils/sessionFilters.ts`；`components/layout/WorkspaceRail.tsx`；`components/session/SelectedSessionActions.tsx` |
| 队列、Stop reports、Send/Steer | `components/chat/SendQueuePanel.tsx`、`InputRow.tsx`；`components/session/ManageModal.tsx`；`packages/core/worker.py` |
| Job pin 与群发 | `packages/web/src/views/JobsView.tsx`；`components/session/SelectedSessionActions.tsx` |
| Notification、QQ 默认、未读覆盖 | `components/layout/AppSettingsModal.tsx`、`CompletionBridgeDefaultsPanel.tsx`、`UnreadRecordingPanel.tsx`；`packages/core/notifications.py` |
| 移动入口与 Terminal TUI | `components/layout/ViewEntryGroups.tsx`、`TopBar.tsx`；`components/chat/SettingsPopover.tsx` |
| Tailscale | `components/layout/TailscaleSettings.tsx`；[高级说明](../tailscale-fallback-access.md) |
| Codex reimport | `components/session/ReimportConfirmationModal.tsx`；`packages/web/src/stores/sessionStore.ts` |
| 手动报告与 self queue | `components/chat/manualTaskReport.ts`；`packages/core/worker.py`；`packages/mcp/server.py` |
| 安装、命令、默认端口 | `main.py`、`config.example.json`、`packages/web/package.json`；启动脚本及依赖清单 |

表中省略前缀的 `components/` 均在 `packages/web/src/` 下。这些是静态源码依据，不是浏览器或真实 provider/OS 实测。

## 验证与未验证边界

文档静态检查通过：9 个 Markdown 文件、72 个本地链接/锚点、9 个安装/启动/终端依赖路径、8 个中英文重点名称；核对修改文件均为 Markdown、UTF-8 无替换字符、代码围栏成对、本地链接/锚点存在、安装启动及终端依赖路径存在、中英文重点名称一致，并执行 `git diff --check`。正常提交 hook 不绕过。

未运行产品测试、build、浏览器、provider 或服务；未访问 8767/8768、凭证或 native 数据，未改依赖/产品代码。Windows 通知仅有 60 秒 helper 点击窗口，真实 OS 点击及浏览器关闭后的原生激活未验证，不承诺持久通知中心激活；browser 失败才 system 选项未实现。Tailscale 界面运行状态不证明跨设备可达。Memory/Character 搁置；审批 Inbox、user login conditional job16452601 未合入；统一 Inbox/Event Bus 仍是设计，均不写成当前功能。

提交后交 MA 验收和整合；本 TA 不 push、不移动 main/practical、不操作服务。
