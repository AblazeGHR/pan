# Pan MCP 工具 defer 机制与去 defer 方案

> 记录 Pan MCP 工具为何是 deferred（延迟加载）的、CodeBuddy 的 defer/NoDefer 决策机制，以及让工具在新会话中直接进入活跃工具列表的方案。
> 相关文档：[cbc-mcp-踩坑记录](./cbc-mcp-踩坑记录.md) — MCP 接入的完整试错过程与最终方案。

## 现象

Pan 的 MCP 工具（`mcp__pan__*`）**默认不会出现在模型的活跃工具列表里**，而是以 deferred 状态存在：

- 必须先用 `ToolSearch`（查询词 `"pan"` / `"mcp"`）发现工具 schema
- 再用 `DeferExecuteTool` 实际调用
- 发现并激活后，该工具**在当前会话内保持活跃**（无需重复搜索）

这不是连接失败。`init` 事件的 `mcp_servers: []` 也不代表 MCP 失败——deferred 工具本来就不展示在 init 元数据里。曾因此误判"MCP 未连接"（2026-08-15 教训，详见踩坑记录 #9）。

## 为什么 Pan 工具是 deferred

当前 Pan 的 cbc worker 通过 `-d workdir` 启动，cbc 自动发现项目级 MCP 配置。加载路径的差异决定了工具是否 deferred：

| 配置文件位置 | 加载方式 | 工具状态 |
|-------------|---------|---------|
| `.codebuddy/mcp.json`（在 workdir 内） | cbc 经 `-d` 自动发现 | fully connected（注释声称不 defer，实测仍走 deferred） |
| `.mcp.json`（fallback，adapter 同时写入） | 项目根发现 | **deferred only** |

`packages/core/adapters/cbc/adapter.py:190-233` 的 `mcp_args()` 目前两个文件都写：

- `<workdir>/.codebuddy/mcp.json` → 通过 `--mcp-config <path>` 显式传入
- `<workdir>/.mcp.json` → fallback

代码注释（`adapter.py:195`）明确说明："Writing to .mcp.json instead makes MCP tools deferred only." 当前实际状态是 deferred，说明 cbc 经 `-d` 走的是 `.mcp.json` 发现路径，`--mcp-config` 的 fully-connected 路径并未优先采用。**这是加载路径的必然结果，不是 bug。**

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

## 让新会话不 defer 的方案

### 方案 A：改 cbc adapter，spawn 参数注入 NoDefer（推荐，永久生效）

在 `packages/core/adapters/cbc/adapter.py` 的 `build_spawn_args()`（`adapter.py:173`）中注入参数：

```python
# 在 args 列表里追加
args.extend(["--tools", "default,NoDefer(mcp__pan__*)"])
```

效果：每个新 spawn 的 cbc worker 启动时，pan 整组 MCP 工具直接进入模型活跃工具列表，无需 ToolSearch。

注意点：

- 该参数经 `cbc` CLI 透传，`base_args()`（`adapter.py:125`）与 `base_args_stream()`（`adapter.py:130`）两条路径都要覆盖
- 若未来有多个 MCP server，可改为从 `s.adapter_config["mcp_servers"]` 动态生成 `NoDefer(mcp__<server>__*)`，而不是硬编码 `pan`
- 改完需要重启 Pan 服务，已存在的 worker 不受影响（spawn 时才读参数）

### 方案 B：worker_spawn extra_args 传参（不改代码）

`worker_spawn` / `worker_handoff` 若支持透传 extra args 到 cbc 命令，可在创建会话时带上 `--tools "default,NoDefer(mcp__pan__*)"`。需要确认 worker API 是否透传该字段。

### 方案 C：MCP 静态配置 defer_loading: false

在 `.mcp.json` 的 `pan` 条目显式写 `"defer_loading": false`。

⚠️ 文档称默认值就是 `false`，且当前 deferred 的根因是 `.mcp.json` fallback 发现路径，所以**这条单独设置大概率不生效**——真正决定当前状态的是加载路径与 `NoDefer` 修饰符。建议不要作为主要手段。

## 排查要点

- 想确认工具是否真的可用：先 `ToolSearch("pan")`，搜得到就是连接正常，只是 deferred
- `session_list` 等工具被 `ToolSearch` 加载后，**当前会话内一直保持活跃**，不会反复需要搜索
- 修改 defer 相关配置后需重启 CodeBuddy / Pan 服务才生效（argv.json 等启动参数改动需重启）

## 相关链接

- CodeBuddy MCP 文档：`docs/cn/cli/mcp.md`（安装目录 `dist/web-ui/docs` 下）
- 工具延迟加载覆盖：`docs/cn/cli/tool-defer-overlay.md`
- [cbc-mcp-踩坑记录](./cbc-mcp-踩坑记录.md)
