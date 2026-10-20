# 开发 Pan 插件

## 选择接入方式

| 来源 | 适用情况 | 需要实现 |
| --- | --- | --- |
| 标准 MCP | 产品已有 MCP server | 连接描述符和插件声明 |
| pan-capability/v1 + stdio | 产品只有 CLI，或希望隔离内部能力与 Agent 工具 | JSON 能力文档和 JSON-RPC helper |
| pan-capability/v1 + HTTP | 产品已有 HTTP API | 固定 endpoint、路由、方法及输入 Schema |

本地新增 helper 使用 stdio，不为它再搭本地 HTTP 执行服务。已有 HTTP/SSE MCP 或 HTTP API 可继续使用原协议。面板浏览器调用的是已有 Pan Web API，与 helper 的本地传输是不同层。

插件 ID 是持久身份，采用小写字母开头、后接小写字母/数字/下划线/连字符，最多 48 字符；`pan`、`pan-tools` 保留。不要用显示名称、账号列表序号或临时路径当 ID。API capability ID 为 `<plugin-id>.<export>`，`export_key` 是稳定的工具导出键。

## 已有 MCP

以下可作为独立 manifest。command 由开发者配置，不能来自模型输入。

```json
{
  "mcp_servers": [{
    "name": "weather-server",
    "command": "weather-mcp",
    "args": [],
    "env": {}
  }],
  "plugins": [{
    "protocol": "pan-plugin/v1",
    "id": "weather",
    "display_name": "Weather",
    "version": "1.0.0",
    "source": {"kind": "mcp", "server_ref": "weather-server"},
    "exposure": {
      "supported_modes": ["standalone", "pan_native", "gateway"],
      "default_mode": "standalone"
    },
    "tool_exports": {"get_weather": "lookup"}
  }]
}
```

独立 MCP 保持上游原工具名；原生投影为 `pan_weather_lookup`；gateway 使用固定 `api_discover` / `api_call` 调用 `weather.lookup`。`tool_exports` 用于稳定 canonical ID 与原生名称，不能使上游未提供的工具凭空出现。

需要 resources、prompts、sampling、elicitation 或上游登录交互时，先只声明 standalone；聚合当前代理 tools 和结果。标准 MCP 独立模式直接连接上游，不能用 `agent_capabilities` 承诺宿主过滤；要过滤内部工具，使用 API bridge。

## API 与 CLI bridge

从 [无网络示例](../examples/plugin-capabilities/README.md) 复制三个文件。manifest 中的 helper 用 `command_ref` 引用，随后不会作为独立插件显示。`capabilities_file` 相对它所在的 manifest 目录解析；`plugin_manifests` 配置中的相对路径则以 Pan 仓库根解析。

helper stdout 只输出 UTF-8、逐行 JSON-RPC，日志写 stderr。Windows Python 显式设置 stdin/stdout UTF-8。初始化应答 `protocol_version: pan-capability/v1`；调用方法是 `capability.invoke`，参数含 `operation` 与 `arguments`。完整示例见能力协议，单帧上限 2 MiB，调用有超时，不要把它当无限时后台任务通道。

默认 result 为业务 JSON；对象同时形成 structuredContent。需要返回图片、富内容或可恢复的业务错误时，execution 使用 `result_format: mcp` 并返回完整 CallToolResult。普通业务拒绝返回 `isError: true`；协议 error 可能使当前 helper 连接进入失败状态，适合协议级故障。输出 Schema 存在时，成功结果的 structuredContent 必须符合它。

CLI bridge 只调用固定命令和参数数组，不使用 shell 拼接。桥接原产品时默认不修改、打包或另写它的数据库；业务数据和凭据继续由原产品管理。若必须扩展外部程序的机器接口，应另行获得明确授权并独立维护 Fork，不能把这种修改藏在 bridge 中。当前实战参见 [CC Switch GUI Fork 接入记录](../reports/CC-Switch-GUI-Fork-Integration-20261018.md)；[最初 bridge 实现报告](../reports/CC-Switch-Bridge-Implementation.md)为历史阶段记录。

CLI 的“查询”也可能刷新凭据或写查询缓存。把查询与切换分别测试；未获授权不要执行真实切换、发送、删除或兑换。结果不确定时先读实际状态，不自动重试。并发 helper 如共享外部配置，应实现自己的跨进程协调；Pan 的每 Session provider 不等于全局互斥。

## 注册与启用

1. 把 manifest 路径加入 `config.json` 的 `plugin_manifests`。这是替换列表，保留原列表中的根 manifest、MCP manifest 和用户私有项；不要盲目覆盖整个配置。
2. 运行静态检查器，建议连同现有 manifest 一起检查别名、ID 与注册关系。
3. 通过现有 manifest reload 操作或正常重启加载新的声明。运行服务时应使用对应实例的地址，避免向生产实例试验。
4. 在测试 Session 的设置中勾选插件并选择一种模式。不要直接编辑派生的 `adapter_config.mcp_servers` 或生成的 MCP JSON。
5. 用无副作用能力验证目录、输入输出和 Session 上下文，再按授权验证业务。

同一 Session 的同一插件只能选一个模式，不同 Session 可独立选择。更新 API 用 `pluginBindings`；MCP 编排工具参数为 `plugin_bindings`。保存时带 `expectedPluginRevision` 防止旧窗口覆盖；不能同时提交新绑定与旧 `mcpServers`。

原生和独立连接配置变化沿用空闲后 Worker restart。已有 gateway 连接内目录变化在下一次发现/调用时更新；首次增删物理连接仍需 restart。CLI 已记住的旧工具 Schema 不会因后台 JSON 改动自动改变。gateway 调用可带 `expected_contract_digest`，过期后重新 discover。

## Panel 与数据目录

Panel 声明使用 `panels`，自动成为现有 View Entry 导航的一部分。v1 只支持宿主实现的 `account-switcher/v1`，不是任意 HTML/React 插件容器。必须提供 `list`、`quota` 两个 read 动作及 `switch`、`default_account` 两个 write_external 动作，并满足 [AccountSnapshot/Quota 契约](../design/Pan-Plugin-Panels-v1.md#account-switcherv1-数据契约)。通用新界面需要扩展 renderer、版本和测试，不能仅换 renderer 字符串。

面板调用只能引用自己插件的能力；API 能力还须在文档中实际存在。`agent_capabilities` 对 API 来源提供 Agent 白名单，三个模式一致执行，Panel 内部能力可比 MCP 多。Panel 是全局入口，不创建 Worker，不受当前 Session MCP 开关控制。

托管 provider 通过 `PAN_PLUGIN_DATA_DIR` 获取 `<Pan data>/plugins/<plugin-id>/`。声明 `data.version`，宿主 `.pan-plugin.json` 校验 owner/version；不符会拒绝，需要明确迁移方案。不要修改宿主 metadata 或面板台账，不扫描其他插件目录，不把原产品凭据复制为缓存。Session 上下文通过 `PAN_AGENT_SESSION_ID` 传入；它是路由标识，不是认证凭据。

独立标准 MCP 是客户端直连，宿主 Runtime 的 data 目录注入不覆盖这条直连路径。若它需要路径，应通过固定 env/cwd 配置，或采用 API shim；不要假定任何第三方独立进程都有 PAN_PLUGIN_DATA_DIR。API shim/聚合托管链路由 Runtime 注入。

write_external 面板请求需要稳定 requestId；宿主先持久化 pending，再调用，不因断开自动重放。同 ID 不同参数拒绝；记录窗口有限，具体边界见 Panel 协议。MCP 调用不经过该面板台账，外部产品也不受其事务控制。

## 交付与版本

改显示名可保留 ID；改 ID 等于新插件身份和新数据归属。输入/输出、导出名称或业务语义变化要更新 contract_version，并验证旧目录/旧调用如何失败。不要把 version 字段当自动迁移器。卸载移出 manifest 后数据保留，清理应另行授权。

按 [检查表](CHECKLIST.md) 交付：源码与静态检查、模拟集成、真实只读查询、用户执行的真实写操作分别记录，不能互相替代。合并前先对齐当时最新 main、复验 practical 实际数据的隔离副本，再按本次授权决定是否合回 main/practical；写插件本身不自动授权部署。
