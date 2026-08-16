# CodeBuddy MCP 工具 defer 机制参考

> 参考文档：CodeBuddy（cbc）MCP 工具的**加载路径**与 **defer/NoDefer 决策机制**。
> 相关：[cbc-mcp-踩坑记录](../cbc-mcp-踩坑记录.md) — MCP 接入的完整试错过程。
> 2026-08-16 整理：由原「pan-mcp-defer-机制与去defer方案.md」瘦身移入（去 defer 方案已证明不需要，删除）。

## 加载路径与 defer 的真相

MCP 工具是否 deferred（延迟加载）**取决于加载路径**，不是固定行为：

| 加载方式 | 工具状态 |
|---------|---------|
| `--mcp-config <workdir>/.codebuddy/mcp.json` **显式传**（worker 的 MCP spawn 路径） | **direct connected（non-defer）**，工具直接进活跃列表，模型直接调用 |
| 项目级 `.mcp.json` 发现（cwd 内） | **deferred**，需 `ToolSearch` 发现 → `DeferExecuteTool` 调用 |
| 仅 `-d` 自动发现 `.codebuddy/mcp.json`（无 `--mcp-config`） | **MCP 未连接**（工具不可见也搜不到） |

要点：
- **决定 non-defer 的是 `--mcp-config` 显式传入**，不是文件位置、也不是 `-d`。
- `-d` **不会**自动发现 `.codebuddy/mcp.json`（2026-08-16 实测）——能连上全靠 `--mcp-config`。
- `<workdir>/.mcp.json` fallback 已移除（cbc 项目发现它会注册 project-scope 阻断连接，见踩坑记录 #15）。
- cbc 2.137.0 起 stream+MCP 可行（stream-json + `--mcp-config`），长驻进程下工具同样 direct connected。

## CodeBuddy 的 defer 决策机制

官方文档：`tool-defer-overlay.md`（cn/cli）。

### defer_loading 静态配置

在 `.mcp.json` 的 server 条目（或 `tools.<name>` 工具条目）中设置：

```json
{
  "mcpServers": {
    "my-server": {
      "type": "stdio",
      "command": "...",
      "defer_loading": true
    }
  }
}
```

- 服务器级与工具级均可设置，工具级覆盖服务器级
- 默认值是 `false`（不 defer）

### Defer / NoDefer 运行时修饰符

在工具列表字段（`--tools` 参数 / 代理 frontmatter `tools`）中使用：

```bash
codebuddy --tools "default,Defer(mcp__github__*)"
codebuddy --tools "default,NoDefer(mcp__pan__*)"
```

| 修饰符 | 含义 |
|--------|------|
| `Defer(X)` | 强制 X 走延迟加载（不直接进工具列表，靠 ToolSearch 发现） |
| `NoDefer(X)` | 强制 X 不走延迟加载（直接进活跃工具列表） |

- `*` 是唯一支持的通配符，可整组匹配 MCP 工具（`mcp__pan__*`）
- 至少一条 `Defer(...)` 时，CodeBuddy 自动附加 `ToolSearch` + `DeferExecuteTool`
- 修饰符不能写进权限字段（`--allowed-tools` / `settings.permissions.allow`），只用于工具列表字段

### 优先级（从高到低）

| 优先级 | 来源 | 说明 |
|:-:|------|------|
| 0a | 任一层 `NoDefer(X)` | **强制非 defer** |
| 0b | 任一层 `Defer(X)` | 强制 defer |
| 1 | MCP 工具级 `tools[name].defer_loading` | 静态配置 |
| 2 | MCP 服务器级 `defer_loading` | 静态配置 |
| 3 | 环境变量 `CODEBUDDY_DEFER_TOOL_LOADING` | 全局开关 |
| 4 | 用户设置 `settings.deferToolLoading` | 全局开关 |
| 5 | 内置默认 | 兜底 |

**关键规则：`NoDefer` 永远胜过 `Defer`。** 即使某层配了 `Defer(X)`，只要任一工具列表里写了 `NoDefer(X)`，本次会话内 X 仍然直接可用——运行时表态优先。

## 排查要点

- **工具不可用 ≠ deferred，先分清三种状态**：
  1. **未连接**：`init` 的 `mcp_servers: []` + `ToolSearch` 也搜不到 → MCP server 启动/配置有问题（多半是 `--mcp-config` 没传或 cwd 错）
  2. **deferred**：`init` 的 `mcp_servers: []` + `ToolSearch` 能搜到 → 配置来自 `.mcp.json` 项目发现路径
  3. **direct connected**：`init` 的 `mcp_servers` 非空（`[pan connected]`）+ 工具直接可见 → `--mcp-config` 路径，正常
- `ToolSearch("pan")` 搜得到只能证明**已连接或已注册**，不代表 direct connected
- 修改 defer 相关配置后需重启 CodeBuddy / Pan 服务才生效

## 相关链接

- CodeBuddy MCP 文档：`docs/cn/cli/mcp.md`（安装目录 `dist/web-ui/docs` 下）
- 工具延迟加载覆盖：`docs/cn/cli/tool-defer-overlay.md`
- [cbc-mcp-踩坑记录](../cbc-mcp-踩坑记录.md)
