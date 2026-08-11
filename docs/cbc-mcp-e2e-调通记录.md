# cbc MCP 端到端调通记录

> 记录 2026-07-29 ~ 2026-07-30 Pan 接入 RuleWhisper MCP 的端到端测试过程、发现的问题与最终方案。
>
> **状态（2026-08-11）**：本记录为历史排障知识，cbc 行为总结与 prompt 设计经验仍有效。其中「system_prompt 内容优化」后续被 `coc-keeper-coldstart` profile（信任强化 prompt，`f9a28da`）进一步取代，首条冷启动 ~90s → ~16s；后续消息由 `--resume` 解决（见 [下一阶段优化计划](./下一阶段优化计划.md)）。

## 重要结论

| 发现 | 结论 |
|------|------|
| `mcp_servers: []` 在 init 事件中 | **正常**，不代表 MCP 失败。deferred 工具通过 ToolSearch 暴露 |
| `ToolSearch` → `DeferExecuteTool` 两步调用 | cbc 设计如此，MCP 工具均为 deferred，需 LLM 主动搜索 |
| `.codebuddy/mcp.json` vs `.mcp.json` | 写 `.codebuddy/mcp.json` 让 cbc 自动发现（deferred），写 `.mcp.json` 也会被读取但优先级可能不同 |
| `type: "stdio"` 字段 | mcp.json 中建议显式添加，帮助 cbc 正确识别 server 类型 |
| `-d <workdir>` (arg) vs `cwd=` (subprocess) | cbc 将 `-d` 路径标记为项目目录，`cwd=` 不会触发项目注册 |
| MCP 工具在 -p 模式下的可见性 | 手工 `cbc -p` 和 asyncio spawn 行为一致 |
| System prompt 注入时机 | 两步调用(先 system_prompt 再 user)导致模型陷入 roleplay，跳过工具发现 |

## 调试时间线

### 第一轮：验证 MCP 是否真的加载

**误判**：看到 init 事件 `mcp_servers: []` 就认为 MCP 未加载。

**真相**：
- 手工 `cbc -p --mcp-config ... "ToolSearch query mcp"` → `mcp__rulewhisper` 出现
- ToolSearch 返回 3 tools with full details + 15 candidates，rulewhisper 在其中
- `mcp_servers: []` 只代表 init 元数据中不展示 deferred 工具，不代表工具不可用

**证据**：
```json
// init 事件
{"type":"system","subtype":"init","mcp_servers":[],...}
// 但后续 ToolSearch("mcp") 返回
"Found 3 tool(s) with full details and 15 additional candidate(s)"
// LLM 调用 DeferExecuteTool → 真实数据返回
{"ok": true, "games": [{"game_id": "...", "label": "dual-test"}, ...]}
```

### 第二轮：发现两步调用导致的 roleplay 陷阱

**现象**：
- Worker 创建后立即注入 system_prompt 作为独立消息
- LLM 看到 "你是 COC 守秘人(KP)..." → thinking: "This is a roleplay task, not a coding task"
- 后续用户消息被 roleplay 上下文污染，LLM 继续扮演 KP 而不搜索工具

**修复**：
```python
# worker.py create_worker 中
if s.system_prompt and not use_mcp:
    # Stream 模式：保持原有行为
    await send_task(worker_id, s.system_prompt, source="system_prompt")
elif s.system_prompt and use_mcp:
    # MCP 模式：跳过，改为前置到首次用户消息
    _log.info("MCP mode: system_prompt will be prepended to first user message")
```

### 第三轮：system_prompt 前置格式探索

尝试了 3 种格式：

| 格式 | 效果 | 原因 |
|------|------|------|
| `[System]: prompt\n[User]: text` | 失败 | cbc 不识别 `[System]:` 标记，视为普通文本 |
| `prompt\n---\ntext` (system 在前) | 不稳定 | hy3 模型优先处理 roleplay 指令 |
| `text\n---\nprompt` (user 在前) | 较好 | 模型更关注尾部内容 |

最终格式：
```python
if s.system_prompt and len(s.history) <= 1:
    text = f"{text}\n\n---\n{s.system_prompt}"
```

### 第四轮：system_prompt 内容优化

**原始 prompt** (roleplay-only，约 150 chars)：
```
你是 COC 守秘人(KP)，用中文回复。
所有规则查询...都通过 RuleWhisper 工具进行...
```

模型解读："RuleWhisper" 是 COC 宇宙中的虚构工具 → 不影响行为

**优化后 prompt** (explicit tool usage，275 chars)：
```
你是 COC 守秘人(KP)，用中文回复。

你将通过 ToolSearch 和 DeferExecuteTool 调用 RuleWhisper 工具的
mcp__rulewhisper__game_create、mcp__rulewhisper__game_list、
mcp__rulewhisper__char_create、mcp__rulewhisper__roll_dice、
mcp__rulewhisper__query_rule 等。
绝不自编数据，检定结果需展示公式与最终值。
```

**效果对比**：
- 原始：首条消息 roleplay → 回复 KP 开场白
- 优化：首条消息探索代码后发现 RuleWhisper MCP → 调用 ToolSearch → 成功返回真实数据

### 第五轮：历史重放污染问题

**现象**：
- 第 1 条消息成功调用 game_list (耗时 ~90s，含探索阶段)
- 第 2+ 条消息退化：模型不再调 MCP，改为读文件系统探索

**原因**：
```python
# _consumer_mcp 每次都重放全部历史
if s.history and len(s.history) > 1:
    history_context = _format_history_for_context(s.history)
    text = f"{history_context}\n[User]: {text}"
```
- 61 条历史重放进 cbc → 上下文爆炸
- 模型在大量历史中迷失，退回到本地工具(Grep/Bash/Agent)
- 每个新 cbc 进程没有对话连续性

## 最终验证的完整链路

```
curl → Pan API (/api/task)
    → Worker._consumer_mcp
        → asyncio.create_subprocess_exec(cbc, -p, --output-format stream-json, 
              --mcp-config <workdir>/.codebuddy/mcp.json, -d <workdir>, <prompt>)
        → cbc init: mcp_servers: [] (deferred)
        → LLM: thinking → ToolSearch("mcp") → found mcp__rulewhisper__*
        → LLM: DeferExecuteTool(mcp__rulewhisper__game_list)
        → RuleWhisper MCP server: 返回真实数据
        → Pan: 解析 stream-json, 保存 history, 更新 session
```

## Prompt 设计经验

| 原则 | 说明 |
|------|------|
| 显式命名工具 | 列出 `mcp__rulewhisper__game_create` 等完整名称，模型才会去 ToolSearch |
| 显式说明调用方式 | "通过 ToolSearch + DeferExecuteTool 两步调用" |
| 告知工具真实性 | "以下工具是真实可用的(非角色扮演)" |
| 用户指令前置 | `user_text\n---\nsystem_prompt` 比反向更有效 |
| 避免过长 | 911 chars 版本导致进程卡死，275 chars 正常 |

## cbc 行为总结

| 行为 | 说明 |
|------|------|
| `--mcp-config <path>` | 文件路径有效，JSON 字符串无效 |
| `--input-format stream-json` | **不兼容** `--mcp-config`，MCP 不加载 |
| `--output-format stream-json` | 输出格式，与 MCP 兼容 |
| `-p` (one-shot) | 线程安全，每次独立 session |
| `-d <dir>` | 注册为项目目录，启用 `.codebuddy/mcp.json` 发现 |
| `--resume <sessionId>` | cli_session_id 不匹配时失败 ("No conversation found") |
| `--settings` | some settings 与 MCP 冲突(如 alwaysThinkingEnabled) |
| MCP 工具均为 deferred | 必须 ToolSearch + DeferExecuteTool 两步调用 |
| `~/.codebuddy/mcp.json` (user) | `cbc mcp list` 注册表，`-p` 模式下**不加载** |
| `.codebuddy/mcp.json` (project) | `-d` 指定后自动发现，工具标记为 deferred |
