# Pan 插件声明与 API 能力协议 v1

实现入口：`packages/plugins`。本批不新增 Pan 鉴权；Session 上下文用于配置、目录和调用路由，既有业务检查与上游认证继续生效。

## 插件声明

在现有 `manifest.json` 的 `plugins` 数组声明插件；原 `mcp_servers` 继续作为连接描述符来源。旧 MCP 无需改造即可按历史 standalone 方式使用。新插件默认关闭。

```json
{
  "protocol": "pan-plugin/v1",
  "id": "example",
  "display_name": "Example",
  "version": "1.0.0",
  "aliases": [],
  "source": {"kind": "mcp", "server_ref": "example-server"},
  "exposure": {
    "supported_modes": ["standalone", "pan_native", "gateway"],
    "default_mode": "standalone"
  },
  "tool_exports": {"lookup_item": "lookup"}
}
```

另一种来源为 `{"kind":"pan_api","capabilities_file":"capabilities.json"}`，文件路径相对声明所在 Manifest 解析。只接受机器可读 JSON 与明确执行绑定；普通 Markdown 不可执行。`pan` 与 `pan-tools` 为保留对象，不能作为插件聚合来源；工具导出重名会报错，不静默覆盖。

同一插件每个 Session 只允许一种模式：

| 模式 | 实际接入 | 工具名称示例 |
|---|---|---|
| standalone | 原有 MCP；API 来源由 stdio shim 暴露 | MCP 保留原名；API 使用 capability ID |
| pan_native | 物理 `pan` MCP 中的真实工具 | `pan_example_lookup` |
| gateway | 单个派生 `pan-tools` MCP 的固定入口 | `api_discover`、`api_call(name="example.lookup", arguments={...})` |

并入 `pan` 不会自动开启 Pan 管理工具。插件 ID 与 canonical capability ID 稳定，显示名可变；QQ 的 `pan-qq` 为 `qq` 的旧名称 alias，旧独立工具名 `qq_*` 和模块命令继续保留。

## API 描述

可运行例子见 `docs/examples/plugin-capabilities/manifest.json`、`capabilities.json` 和 `provider.py`。

API 文档顶层必须包含 `protocol: pan-capability/v1`、`plugin_id`、`provider`、`capabilities`。每项能力包含 `id`、`export_key`、`description`、`contract_version`、`input_schema`、`execution`；`output_schema` 可选。Schema 使用标准 JSON Schema（推荐 Draft 2020-12），发布前验证。

执行绑定有两类：

- `stdio_jsonrpc`：`provider.command_ref` 引用 Manifest 中的固定 command/args/env/cwd 描述符；该 helper 不另外作为 MCP 插件展示。模型不能提交可执行文件或命令行。宿主给子进程传入 `PAN_AGENT_SESSION_ID`。
- `http`：`provider.endpoint` 为已配置的上游 HTTP(S) 地址；能力声明固定 `execution.method` 和 `execution.path`。参数放 query（GET）或 JSON body（其他方法），不允许调用者替换 URL、method 或 header。`headers_env` 可把固定 header 名映射到环境变量名。沿用已有 HTTP 来源，不新增本地 HTTP 执行层。

## stdio JSON-RPC framing

UTF-8、每行一个 JSON-RPC 2.0 请求/响应，单行不超过 2 MiB；stdout 只用于协议，诊断写 stderr。宿主串行调用并检查响应 ID。

初始化请求：

```json
{"jsonrpc":"2.0","id":1,"method":"provider.initialize","params":{"protocol_version":"pan-capability/v1"}}
```

响应：

```json
{"jsonrpc":"2.0","id":1,"result":{"protocol_version":"pan-capability/v1"}}
```

调用请求：

```json
{"jsonrpc":"2.0","id":2,"method":"capability.invoke","params":{"protocol_version":"pan-capability/v1","operation":"lookup","arguments":{"item_id":"one"}}}
```

响应 `result` 为业务 JSON；对象同时作为 structuredContent 和 JSON 文本返回。若 `execution.result_format` 为 `mcp`，result 必须是完整 `CallToolResult`，保留 content、structuredContent、isError 等字段。错误使用标准 JSON-RPC error。首期不支持主动取消协议，不会把中断、超时或断连视为“未执行”；未知结果禁止自动重发。

## Session API 与生效规则

创建/更新 Session 接受 `pluginBindings: [{plugin_id, enabled, mode}]`。更新可带 `expectedPluginRevision` 防止旧窗口覆盖新配置。不能同时提交 `pluginBindings` 与 `mcpServers`。旧 `mcpServers` 请求仍由兼容层解析，新增配置以逻辑 bindings 为真源，`adapter_config.mcp_servers` 为生成的接入描述符。

返回逻辑 `mcpServers`、`pluginBindings`、`pluginSelectionRevision`；设置请求另外返回 `pluginApplyStatus`。模板保存使用 `plugin_bindings`，与 `mcp_servers` 互斥；省略表示继承，`[]` 表示清空。复制、handoff、rewind、branch 保留逻辑配置。

已有 `pan-tools` 连接中的目录增删和 Schema 变化对下一次 discover/call 生效，不重启 Worker。固定入口不会改写模型已读到的历史；调用旧契约会明确报错，重新发现后再由调用者决定下一步。`api_call` 可带 `expected_contract_digest`。

原生/独立接入配置变化、模式切换及新增/移除物理连接沿用已有空闲后 restart。受管理 Provider 的原生目录变化会请求同一重启机制。第三方 standalone 的原生通知和缓存由其 CLI 自身处理；Pan 不为独立直连额外启动一个目录监控进程。

## 兼容范围

聚合首期支持 tools 与工具结果。prompts、resources、sampling、elicitation、资源订阅和完整第三方 OAuth 登录 UI 尚未代理；依赖这些交互的插件应声明仅 standalone。缺上游凭据时沿用上游拒绝，不复制 CLI 凭据或切换来源重发。

关闭 QQ 工具入口不停止既有 QQ 网关、订阅、定时 Job 和完成通知。旧 `send_qq` 动作、目标键、历史/inbox 路径与 outbox 状态不作重命名或重放迁移。
