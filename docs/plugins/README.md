# Pan 插件开发者入口

为已有 MCP、CLI 或 API 写一个薄适配插件，再按需增加 Pan Panel。插件注册、Agent 工具和面板是三个独立概念：声明插件不会自动为所有 Session 启用工具；Panel 是全局入口，不属于当前 Session。

| 你的目标 | 从这里开始 |
| --- | --- |
| 已有标准 MCP，接入 Pan | [开发指南：MCP](DEVELOPING.md#已有-mcp) |
| 已有 CLI/API，写 bridge | [开发指南：API 与 CLI](DEVELOPING.md#api-与-cli-bridge) |
| 将固定内部 API 或命令组合成 MCP 插件 | [核心 Actions 与 Group](ACTIONS.md) |
| 外部程序发布/订阅自动化事件 | [事件协议与生命周期](EVENTS.md)、[Node 示例](../../examples/event-plugin/manifest.json) |
| 增加类似 Chat/Jobs 的入口 | [开发指南：Panel](DEVELOPING.md#panel-与数据目录)、[本地自由 Panel（web-app/v1）](PANELS.md) |
| 复制一个可本地构建的自由界面示例 | [Local Console 示例](../../examples/plugin-webapp/README.md) |
| 复制一个无外部副作用的例子 | [可运行 API 示例](../examples/plugin-capabilities/README.md) |
| 检查发布前是否准备好 | [交付检查表](CHECKLIST.md) |
| 不显示、调用失败、Schema 不刷新 | [排错与常见问题](TROUBLESHOOTING.md) |
| 让编码 Agent 帮你开发 | [可复制的任务说明](AGENT_BRIEF.md) |
| 查看精确字段与边界 | [能力协议 v1](../design/Pan-Plugin-Capability-v1.md)、[Panel 协议 v1](../design/Pan-Plugin-Panels-v1.md) |

仓库还提供只读静态检查：

```powershell
python tools/validate_plugin.py docs/examples/plugin-capabilities/manifest.json
python tools/validate_plugin.py manifest.json packages/mcp/manifest.json --json
python tools/validate_plugin.py --help
```

检查器不运行 provider、CLI、网络查询或账号切换，也不创建插件 data 目录。静态通过不代表外部程序已安装、认证可用或真实业务通过。

本指南以当前实现为准。`web-app/v1` 允许插件提供本地构建的自由界面，但仓库仍未提供插件市场、远程网址 Panel、插件权限沙箱或自动数据迁移。需要这些能力时应另行设计，不能只在 manifest 中写一个不存在的字段。
