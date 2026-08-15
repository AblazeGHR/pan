# Pan MCP 工具 defer 机制与去 defer 方案

> 记录 Pan MCP 工具的加载/defer 机制、CodeBuddy 的 defer/NoDefer 决策机制，以及让工具在新会话中直接进入活跃工具列表的方案。
> 相关文档：[cbc-mcp-踩坑记录](./cbc-mcp-踩坑记录.md) — MCP 接入的完整试错过程与最终方案。

## 2026-08-16 实测更新（重要）

> 对 cbc 2.136.0（hy3）做了一组受控试验，**修正了本文档 08-15 记录的核心结论**。
> 08-15 记录的"Pan worker 工具是 deferred、必须 ToolSearch"为**过时错误**，以本文为准。

### 试验事实矩阵

| # | 加载方式 | init `mcp_servers` | 工具状态 |
|---|---------|-------------------|---------|
| A | `.codebuddy/mcp.json` + **`--mcp-config` 显式传**（cwd=workdir） | `[pan connected]` | **direct connected（非 defer）**，工具直接进活跃列表 |
| B | 同 A + `--tools "default,NoDefer(mcp__pan__*)"` | `[pan connected]` | 同 A（修饰符有效但冗余） |
| C | 仅 `.codebuddy/mcp.json`，靠 `-d` 自动发现（无 `--mcp-config`） | `[]` | **MCP 未连接**（工具不可用，也不是 deferred） |
| D | 仅 workdir 根 `.mcp.json`，one-shot 模式 | `[]` | **deferred**（模型需 ToolSearch×3 才调用成功） |
| E | workdir 根 `.mcp.json`，stream-json 模式 | `[]` | **deferred**（模型需 ToolSearch×4） |
| F | **Pan worker 端到端**（meta-agent profile，`_consumer_mcp` 路径） | — | 模型**直接调用 `session_list` 成功**，未走 ToolSearch |

### 核心结论

1. **Pan worker 当前已经是 non-defer**——`_consumer_mcp` 通过 `mcp_args()` 显式传 `--mcp-config <workdir/.codebuddy/mcp.json>`，cbc 默认把 MCP 工具直接注入活跃列表，模型无需 ToolSearch（试验 F 端到端证实）。
2. **真正决定 non-defer 的是 `--mcp-config` 显式传入**，不是文件位置、也不是 `-d`。
3. **`-d` 自动发现 `.codebuddy/mcp.json` 不生效**（试验 C）：cwd=workdir、`-d workdir` 都试过，MCP 根本没连上。`adapter.py` 的注释有误（见下）。
4. **项目级 `.mcp.json` 发现的工具 → deferred**（试验 D/E），无论 one-shot 还是 stream 模式。
5. **方案 A（`--tools NoDefer`）有效但冗余**——不注入修饰符，`--mcp-config` 路径下也已经是 non-defer。

### 2026-08-16 代码修改记录（移除 -d 的完整配套）

试验确认 `-d` 对 MCP 连接 / resume / JSONL 路径均无作用（JSONL 项目目录由 **cwd** 派生，`--resume` 按 session_id 查找），故移除。但移除后暴露两个**隐藏问题**，必须配套修：

| 问题 | 现象 | 根因 | 修复 |
|------|------|------|------|
| `.mcp.json` fallback 阻断 MCP | 无 `-d` 时 `--mcp-config` 连接失败（`mcp_servers: []`，`cbc mcp list` 显示 pan `Needs approval` / `Failed to connect`，Scope: project） | cbc 项目发现 `<cwd>/.mcp.json`，把 pan 注册为 **project-scope** MCP 并持久化；该注册干扰 `--mcp-config` 的显式连接。带 `-d` 时 cbc 项目发现不读 `.mcp.json`，故此前从未暴露 | `mcp_args()` 不再写 `<workdir>/.mcp.json` fallback，只写 `.codebuddy/mcp.json` 并显式传 `--mcp-config` |
| `command: "python"` 启动失败 | cbc 启动 pan MCP server 失败（PATH 里 `python` 解析异常） | manifest 里 `command: "python"` 依赖 PATH，不可靠 | `packages/mcp/manifest.json` 改为 `"${PLUGIN_DIR}/../../.venv/Scripts/python"`（可移植绝对路径） |

**最终代码状态（已端到端验证）**：
- `adapter.py` `build_spawn_args()` + `worker.py` `_consumer_mcp()`：不再传 `-d`
- `adapter.py` `mcp_args()`：只写 `.codebuddy/mcp.json`，不写 `.mcp.json`
- `packages/mcp/manifest.json`：pan `command` 用 `${PLUGIN_DIR}` 解析的绝对 venv python

**验证结果**：stream 模式、meta-agent（pan MCP 直接调用 + resume）、coc-keeper-coldstart（rulewhisper 直接调用）三条链路全部通过；worker 写入无 `.mcp.json`、command 为绝对路径。

### 08-15 记录判定

08-15 记录的"Pan worker 实测 deferred、必须 ToolSearch"**为过时错误**，一律以本文 08-16 实测为准。其错误来源：当时的"deferred"观察实为"C 类未连接"（`init` 的 `mcp_servers: []` 被误读为 deferred，见踩坑记录 #9）。

## 现象（08-15 记录已废弃，见上文）

**当前事实**：工具是否 deferred 取决于**加载路径**，不是固定行为。见下节。

## 加载路径与 defer 的真相

`packages/core/adapters/cbc/adapter.py` 的 `mcp_args()`（约 222 行）写两个文件并显式传 `--mcp-config`：

- `<workdir>/.codebuddy/mcp.json` → 通过 `--mcp-config <path>` **显式传入**（worker.py `_consumer_mcp` 使用）→ **工具直接可用**
- `<workdir>/.mcp.json` → fallback（cbc 项目级发现路径）→ **工具 deferred**

**`adapter.py:225` 注释修正**：原注释声称"cbc 经 `-d` 自动发现 `.codebuddy/mcp.json`，工具 fully connected"。实测（试验 C）`-d` **不会**自动发现该文件——能连上全靠 `--mcp-config` 显式传入。注释与 `.mcp.json` 相关的半句（"Writing to .mcp.json instead makes MCP tools deferred only"）成立。

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

## 去 defer 方案（2026-08-16 结论：已不需要）

### 方案 A：改 cbc adapter，spawn 参数注入 NoDefer

在 `packages/core/adapters/cbc/adapter.py` 的 `build_spawn_args()` 中注入：

```python
args.extend(["--tools", "default,NoDefer(mcp__pan__*)"])
```

**实测结论（试验 B/F）：有效，但冗余。** `_consumer_mcp` 已通过 `--mcp-config` 显式传配置，工具默认就是直接可用的。**不建议实施**——除非未来某条路径让 MCP 走 `.mcp.json` 项目发现（会变 deferred），那时才需要。

### 方案 B：worker_spawn extra_args 传参（不改代码）

`worker_spawn` / `worker_handoff` 若支持透传 extra args 到 cbc 命令，可在创建会话时带上 `--tools "default,NoDefer(mcp__pan__*)"`。同方案 A，当前不需要。

### 方案 C：MCP 静态配置 defer_loading: false

在 `.mcp.json` 的 `pan` 条目显式写 `"defer_loading": false`。

⚠️ 默认值就是 `false`，所以这条单独设置基本无意义；真正决定状态的是加载路径。

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
- [cbc-mcp-踩坑记录](./cbc-mcp-踩坑记录.md)
