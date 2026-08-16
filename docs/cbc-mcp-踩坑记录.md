# cbc MCP 踩坑记录与方案选型

> 记录 Pan 接入 MCP 的完整试错过程与最终方案。避免后人踩重复的坑。
> 合并自原「cbc-mcp-踩坑记录.md」+「cbc-mcp-e2e-调通记录.md」+ 2026-08-15 Meta-Agent 集成新发现。

## 相关文档

- [阶段计划与进度](./阶段计划与进度.md) — 任务进度跟踪（前端集成、性能、错误处理）
- [CodeBuddy MCP defer 机制](./references/cbc-mcp-defer-机制.md) — 加载路径与 defer 决策机制（原 system_prompt/CMD 转义内容已并入本文件 #17）

## 决策总结

> 2026-08-16 更新：`--mcp-config` 显式传 `.codebuddy/mcp.json` 时工具为 **direct connected（non-defer）**，无需 ToolSearch（受控试验确认，cbc 2.136.0）。原记录"工具均为 deferred"已被修正。
>
> **2026-08-16 勘误（cbc 2.137.0）**：踩坑 #5 的"`--input-format stream-json` 与 `--mcp-config` 不兼容"结论仅在 **cbc 2.136.0** 成立，**2.137.0 已修复**。实测：stream-json 模式 + `--mcp-config`（stdio）下 `init` 事件 `mcp_servers: [{name: pan, status: connected}]`，且同一长驻进程**连续 3 轮**对话均成功调用 MCP 工具（`mcp__pan__session_list`/`model_list`，返回真实数据）。即 **stream 长驻 + MCP 在 2.137.0 下可行**，详见实验脚本 `streamjson_probe.py`/`multiround_probe.py`。

| 方案 | 结论 |
|------|------|
| `--mcp-config` + `--input-format stream-json` | **2.136.0 不兼容**（MCP 不加载）；**2.137.0 已兼容**（实测 3 轮多轮对话 MCP 正常） |
| `--mcp-config` 文件路径（无 `--input-format`） | **最终方案**；工具 direct connected（non-defer，2026-08-16 实测），进程一问一答后退出 |
| `-d` 自动发现 `.codebuddy/mcp.json`（无 `--mcp-config`） | **不生效**（2026-08-16 实测 MCP 未连接），需显式 `--mcp-config` |
| 项目级 `.mcp.json` 发现 | 工具 **deferred**（需 ToolSearch，2026-08-16 实测） |
| One-shot MCP 模式 + `--resume` | **2.136.0 起用的兜底方案**；每次 task 新开 cbc 进程（冷启动 ~19s） |
| `--acp`（Agent Client Protocol）长连接 | **2.137.0 支持 MCP**，但 `session/new` 的 `mcpServers` schema 只接受 `http/sse/acp` 类型（**不支持 stdio**），需远程 SSE/HTTP 常驻 server |

## 踩坑时间线

### 1. manifest.json 中的 MCP 配置解析

- `${PLUGIN_DIR}` 需要 `Path.resolve()` 标准化路径（不能用简单 `replace`）
- 只替换含 `${PLUGIN_DIR}` 的值，不影响其他参数（`args: ["-m", "src.server.mcp"]` 不能被路径化）

### 2. python 虚拟环境路径

- RuleWhisper 的 `.venv` 在 `ai_coc/` 根，不在 `pan_plugin/` 里
- manifest 中需用 `${PLUGIN_DIR}/../.venv` 指向正确位置

### 3. Session adapter_config 中 mcp_servers 丢失

- `if char.mcp_servers:` 对空列表 `[]` 为 `False`
- 修复：`if char.mcp_servers is not None and len(char.mcp_servers) > 0`

### 4. `--mcp-config` JSON 字符串 vs 文件路径

- `cbc --help` 说支持 `<fileOrString>`
- 测试结果：**只有文件路径有效**，JSON 字符串不生效
- 文件路径格式：`--mcp-config D:/path/to/.mcp.json`

### 5. `--mcp-config` + `--input-format stream-json` 不兼容【2.137.0 已修复，勘误】

**2026-08-16 勘误**：本坑在 **cbc 2.136.0** 实测成立；**2.137.0 复测已不成立**——

- 2.136.0：`--input-format stream-json` 让 cbc 忽略 `--mcp-config`；cbc 只读 `~/.codebuddy/mcp.json`（user-level），且 stream-json 模式下 user-level 也不加载
- 2.137.0：`--input-format stream-json` + `--mcp-config <stdio 配置>` 正常加载。`init` 事件返回 `mcp_servers: [{name: pan, status: connected}]`；同一长驻进程连续 3 轮对话均成功调用 `mcp__pan__*` 工具（返回真实数据）。**stream 长驻 + MCP 可行，无需 one-shot 兜底**

> 版本差异是行为变化主因（可能 2.137.0 修复），也可能 2.136.0 当时受 `.mcp.json` fallback 干扰（见 #15）。若将来升级 cbc 需用 `streamjson_probe.py` 复测。
>
> 注：`streamjson_probe.py` / `multiround_probe.py`（及 `acp_probe.py`、`acp_pan_mcp.json`）已归档到 `docs/archive/cbc-mcp-experiments/`。

### 6. `enableAllProjectMcpServers` 需要项目注册【已废弃】

> ⚠️ 该方案已被 `--mcp-config` 显式传入取代（见决策表），本坑仅作历史记录。

- `--settings '{"enableAllProjectMcpServers":true}'` 只在 cbc "认识的项目"中查找 `.mcp.json`
- Pan 的 workdir 是运行时创建的，cbc 不认识
- 手动 `cbc -d workdir` 后 cbc 会记录该目录，下次生效
- 但 Pan spawn 的 cbc 用 `cwd=` 而不是 `-d`，不会被记录

### 7. `--input-format stream-json` 改为纯文本

- 去掉后 MCP 正常工作
- 但 cbc 进入 one-shot 模式，处理完 prompt 就退出
- 无法维持长连接对话

### 8. `--resume` 携带过期 session ID

- Worker 重启后 `cli_session_id` 未清除
- cbc 尝试 resume 不存在的 session → 立即退出（exit 0）
- 修复：`create_worker` 中杀死旧 worker 后清除 `s.cli_session_id`

### 9. MCP 工具的 deferred 状态（必须两步调用）【关键】

**【2026-08-16 修正】** MCP 工具是否 deferred **取决于加载路径**，不是固定行为：

- `--mcp-config` 显式传 `.codebuddy/mcp.json` → 工具**直接进活跃列表**（direct connected），无需 ToolSearch（实测）
- 项目级 `.mcp.json` 发现 → 工具 deferred，需 `ToolSearch("pan")` → `DeferExecuteTool` 调用
- `-d` 自动发现 `.codebuddy/mcp.json` → **不生效**（MCP 未连接，工具既不可见也搜不到）
- `init` 事件的 `mcp_servers: []` 可能是"未连接"或"deferred"，两者都不展示。区分：`ToolSearch` 搜得到 = deferred；搜不到 = 未连接
- 2026-08-15 教训：meta-agent worker 因工具列表无 `session_list` 误判"MCP 未连接"；2026-08-16 实测该路径实为 direct connected（`_consumer_mcp` 显式传 `--mcp-config`）

### 10. MCP server 的 cwd 必须指向包根【2026-08-15 新增】

- `cwd: "${PLUGIN_DIR}"`（= `packages/mcp`）时，`python -m packages.mcp.server` 报 `ModuleNotFoundError: No module named 'packages'`
- 修复：`cwd: "${PLUGIN_DIR}/../.."` 指向项目根（`Path.resolve()` 后正确）
- 验证：`python -m packages.mcp.server` 从项目根启动，MCP 协议握手正常

### 11. MCP server 连接自身 API 的端口【2026-08-15 新增】

- Pan MCP server（`packages/mcp/server.py`）默认连 `PAN_API_URL`（默认 `http://127.0.0.1:8768`）
- 若 Pan 服务跑在别的端口（如 8769），worker 调用工具时 `[WinError 10061] 连接被拒`
- 修复：主服务用默认端口 8768，或用 `PAN_API_URL` 环境变量覆盖（manifest 的 `env` 字段）

### 12. 带 character 的 session 首次任务被 memory 阻塞【2026-08-15 新增】

- `_maybe_inject_memory` 对带 `character_id` 的 session 触发 embedding 搜索
- 首次加载 bge-base-zh 模型 + huggingface 网络重试可阻塞数分钟，`asyncio.to_thread` 无超时 → worker 卡 `queued`
- 修复：`memory.enabled` 配置开关（`config.json -> memory.enabled`，默认 true）+ 15s 超时降级为原文本

### 13. MCP 模式 system_prompt 注入时机（roleplay 陷阱）

- MCP 模式若像 stream 模式那样把 system_prompt 作为**独立首条消息**发送，LLM 会陷入角色扮演（"你是 KP..." → thinking "this is a roleplay task"），跳过工具发现
- 修复：MCP 模式**不单独注入**，改为前置到首次用户消息：`f"{user_text}\n\n---\n{system_prompt}"`
- `_consumer_mcp` 中：`if s.system_prompt and not s.cli_session_id: text = f"{text}\n\n---\n{s.system_prompt}"`

### 14. 历史重放污染（`--resume` 替代）

- 早期 `_consumer_mcp` 每次把全部 history 重放进 prompt → 上下文爆炸，模型退回到 Bash/Grep 探索
- 修复：用 `--resume <cli_session_id>` 让 cbc 原生维持对话连续性，不再手动重放

### 15. `.mcp.json` fallback 会阻断 `--mcp-config` 连接【2026-08-16】

- 现象：移除 `-d` 后，`--mcp-config` 显式传配置但 MCP 未连接（`init` 的 `mcp_servers: []`，模型报工具 not found）
- 定位：`cbc mcp list` 显示 `pan: Needs approval`，`cbc mcp get pan` 显示 `Scope: project`、`Failed to connect`
- 根因：cbc 项目发现 `<cwd>/.mcp.json`，把 pan 注册为 **project-scope** MCP 并持久化（`cbc mcp remove "pan" -s project` 确认它存在 `.mcp.json` 里）；该注册干扰 `--mcp-config` 的显式连接。带 `-d` 时 cbc 项目发现不读 `.mcp.json`，所以此前一直没暴露
- 修复：`mcp_args()` 不再写 `<workdir>/.mcp.json` fallback，只写 `.codebuddy/mcp.json` + `--mcp-config`

**精确结论（2026-08-16 补充验证）**：

`.mcp.json` 与 Pan 的 MCP 注入**无关且不必要**——注入唯一通道是 `--mcp-config`。它是否与注入冲突，**完全取决于内部那条 server 的启动状态**：

| `.mcp.json` 内 server 的 command | 注册状态 | 与 `--mcp-config` 是否冲突 |
|----------------------------------|---------|---------------------------|
| 绝对路径（能正常启动） | `Connected` | **不冲突**——同名时 `--mcp-config` 的 connected 路径胜出（组合 G 试验 + workdir=Pan 根端到端均验证） |
| 裸 `python`（启动失败） | `Failed to connect` | **冲突**——project-scope 注册阻断 `--mcp-config`（本坑场景） |

即：**只要 `.mcp.json` 内部的 server 能正常启动，它与注入并存没问题；一旦内部 server 启动失败，就会阻断注入。** 该失败条件已被 manifest 绝对路径化（见 #16）从源头消除。

注意：`.mcp.json` 仍有一项非注入用途——**项目根 `.mcp.json` 给开发环境的 CodeBuddy 会话提供 MCP 工具**（如本仓库根的 rulewhisper）。Pan worker 不需要它，但开发场景需要。

### 16. manifest 的 `command` 必须绝对路径【2026-08-16】

- 现象：pan MCP server 用 `command: "python"`（依赖 PATH）时 cbc 启动失败
- 修复：`packages/mcp/manifest.json` 改为 `"${PLUGIN_DIR}/../../.venv/Scripts/python"`（`${PLUGIN_DIR}` 解析为可移植绝对路径）

### 17. system_prompt 注入方式与 Windows .CMD 转义【2026-08-15，08-16 并入】

> 合并自原「cbc-mcp-system-prompt-注入与CMD转义.md」（2026-08-16 文档整理）。

**根因 1：system_prompt 注入方式（hy3 实测）**

| 方式 | 效果 |
|------|------|
| 拼接进用户消息 `f"{text}\n---\n{system_prompt}"` | **不生效**——hy3 当普通用户文本 |
| `--append-system-prompt <prompt>` | **不生效**——hy3 忽略追加语义 |
| `--system-prompt <prompt>`（覆盖式） | **生效**——cbc 注入真实 system message |

当前实现：one-shot `_consumer_mcp` / stream+MCP 都只在**首条消息**（`cli_session_id` 捕获前）注入 `--system-prompt`，之后靠 `--resume` 延续（系统提示随 cbc session 持久化）。

**根因 2：Windows .CMD shim 参数转义崩溃**

- `shutil.which("cbc")` 解析到 npm shim `cbc.CMD`；`create_subprocess_exec` 直接执行 .CMD 经 cmd.exe
- 766 字中文 system_prompt（含引号/逗号/换行）被 cmd.exe 转义截断 → cbc 进程 28ms 退出
- 修复：`_resolve_cbc_argv()` 把 `.CMD` 解析为 `node <entry.js>`，参数直传 node 绕开 cmd.exe

**附带发现**：one-shot 首次 MCP server 冷启动 ~107s，后续 `--resume` ~19s；**已由 stream+MCP（cbc 2.137.0）消除**（长驻进程 server 只启一次）。

**关联**：#13（system_prompt 注入时机 / roleplay 陷阱）。

## 最终方案：双模式 Worker

```python
# worker.py _consumer
if s.adapter_config.get("mcp_servers"):
    await _consumer_mcp(w, text, source, s)   # one-shot 模式
else:
    await _consumer_stream(w, text, source, s) # stream-json 模式
```

### Stream 模式（无 MCP）

- 维持原有的 `--input-format stream-json` 长连接
- 进程常驻，stdin/stdout 双向通信
- `_read_stdout` 实时解析响应

### Stream 模式（带 MCP）【2.137.0 新增可行，已实现】

- **不再需要 one-shot 兜底**：stream-json + `--mcp-config` 在 2.137.0 下可直接加载 MCP，且长驻进程多轮对话 MCP 工具持续可用（实测 3 轮）
- 进程常驻 + watchdog 回收照旧，消除 one-shot 每任务 ~19s 冷启动
- 工具为 **direct connected**（2026-08-16 实测：stream+MCP 下直接调用 `mcp__pan__session_list` 成功，无需 ToolSearch；模型可直接访问）
- **已实现（2026-08-16）**：`adapter_config.output_mode="stream"` 时 `create_worker`/`_consumer` 走 stream 分支（`use_mcp=False`），长驻进程 spawn 时经 `build_spawn_args`→`mcp_args()` 自动带 `--mcp-config`；system_prompt 以 `--system-prompt` 注入（避免角色扮演陷阱）。测试：`tests/test_worker_output_mode.py`

### One-shot MCP 模式（有 MCP）

- 每次 task 新开 cbc 进程
- `--mcp-config <file>` 正常加载 MCP server
- prompt 作为 CLI 最后一个参数
- `--output-format stream-json` 解析输出（无 `--input-format`）
- `--resume <cli_session_id>` 保持对话上下文
- 阻塞等待进程完成，解析 stream-json 输出
- `w.status` 正确暴露（running → idle）
- **2.137.0 起已非必要**，仅作兼容降级保留（或依赖 `--resume` 的场景）

## cbc 参数速查

```
cbc --help 输出中 MCP 相关参数：

--mcp-config <fileOrString>      加载 MCP 服务器（cbc ≤ 2.136.0 仅在无 --input-format stream-json 时生效；2.137.0 起与 stream-json 兼容）
--strict-mcp-config              仅使用 --mcp-config 的服务，忽略其他 MCP 配置

cbc mcp add-json <name> <json>  添加 MCP 服务器
cbc mcp list                     列出 MCP 服务器
cbc mcp get <name>               查看服务器详情
cbc mcp remove <name>            移除服务器

--settings '{"enableAllProjectMcpServers":true}'  自动批准项目级 MCP 服务器
```

## `.mcp.json` 文件格式

```json
{
  "mcpServers": {
    "server-name": {
      "command": "path/to/python",
      "args": ["-m", "module.name"],
      "cwd": "D:/project/working/dir"
    }
  },
  "disabledMcpServers": []
}
```

- `command` 必须是可以直接执行的绝对路径
- `cwd` 是进程工作目录，MCP server 的 `python -m` 从这里解析模块
- `args` 是纯参数列表，不会被进一步解析
- **`type: "stdio"` 建议显式添加**，帮助 cbc 正确识别 server 类型（缺失时可能影响发现）
- `.codebuddy/mcp.json` + **`--mcp-config` 显式传入** → 工具 direct connected（non-defer）
- ⚠️ 2026-08-16 实测：cbc 经 `-d` **不会**自动发现 `.codebuddy/mcp.json`（MCP 未连接）；必须 `--mcp-config` 显式传
- 项目级 `.mcp.json`（workdir 根）发现的工具为 **deferred**（需 ToolSearch）

## MCP Server 开发注意事项

- 当前兼容版本：`mcp==1.28.1`（Anthropic SDK）
- 不要用 `mcp>=2.0`（API 已变更，`FastMCP` 导入路径不同）
- `from mcp.server.fastmcp import FastMCP`
- 同步工具函数可直接用 `@mcp.tool()` 装饰
- stdio transport：`mcp.run(transport="stdio")`
- SSE transport：`mcp.run(transport="sse")` + `--port`
- **依赖自身 API 的 server 用 `PAN_API_URL` 环境变量配端口**（默认 8768）

## Prompt 设计经验

| 原则 | 说明 |
|------|------|
| 显式命名工具 | 列出 `mcp__pan__session_create` 等完整名称（`--mcp-config` 路径下工具已直接可见，命名是保险） |
| 显式说明调用方式 | "通过 ToolSearch + DeferExecuteTool 两步调用"（仅 `.mcp.json` deferred 路径需要） |
| 告知工具真实性 | "以下工具是真实可用的(非角色扮演)" |
| 用户指令前置 | `user_text\n---\nsystem_prompt` 比反向更有效 |
| 避免过长 | 911 chars 版本导致进程卡死，275 chars 正常 |
| **显式声明 deferred** | "MCP 工具不在工具列表里，必须先 ToolSearch"（08-16 修正：仅对 `.mcp.json` deferred 路径必要；`--mcp-config` 路径下模型直接可见）【2026-08-15，08-16 修正】 |

## cbc 行为总结

| 行为 | 说明 |
|------|------|
| `--mcp-config <path>` | 文件路径有效，JSON 字符串无效 |
| `--input-format stream-json` | **2.136.0 不兼容** `--mcp-config`（MCP 不加载）；**2.137.0 已兼容**（实测连接+多轮调用成功） |
| `--output-format stream-json` | 输出格式，与 MCP 兼容 |
| `-p` (one-shot) | 线程安全，每次独立 session |
| `-d <dir>` | 注册为项目目录；⚠️ 2026-08-16 实测**不触发** `.codebuddy/mcp.json` 自动发现（需 `--mcp-config` 显式传） |
| `--resume <sessionId>` | cli_session_id 不匹配时失败 ("No conversation found") |
| `--settings` | some settings 与 MCP 冲突(如 alwaysThinkingEnabled) |
| MCP 工具是否 deferred | 取决于加载路径：`--mcp-config` 显式传 → **direct connected**；项目级 `.mcp.json` → **deferred** |
| `~/.codebuddy/mcp.json` (user) | `cbc mcp list` 注册表，`-p` 模式下**不加载** |
| `.codebuddy/mcp.json` (project) | 仅配合 `--mcp-config` 显式传入生效，工具 direct connected |

## 关键教训

1. **`--input-format stream-json` 曾是 MCP 杀手（≤2.136.0）** — 2.137.0 已兼容，升级后需用 `streamjson_probe.py` 复测再决策；不要沿用旧结论
2. **先手动测试后集成** — 用 `cbc -p --mcp-config ...` 验证 MCP 连接
3. **`bool([])` = `False`** — 检查列表时用 `len(list) > 0` 或 `list is not None`
4. **`--resume` 的 session ID 要清理** — 杀死旧 worker 时同步清除 `cli_session_id`
5. **`${PLUGIN_DIR}` 解析需要 `Path.resolve()`** — 简单的 `replace` 会留下 `../` 路径
6. **MCP 工具不出现 ≠ 未连接** — 先 `ToolSearch("mcp")` 区分：搜得到 = **deferred**（`.mcp.json` 路径）；搜不到 = **未连接**（多半 `--mcp-config` 没传或 cwd 错）。`--mcp-config` 路径下工具应直接可见【2026-08-15，08-16 修正】
7. **MCP server cwd 要指向包根** — `${PLUGIN_DIR}/../..` 而非 `${PLUGIN_DIR}`【2026-08-15】
8. **带 character 的 session 首次任务会被 memory 加载阻塞** — 配 `memory.enabled: false` 或依赖 15s 超时【2026-08-15】
