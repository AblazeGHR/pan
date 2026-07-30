# 方案：利用 session 克隆避免 MCP 冷启动探索

## 问题

新 session 首条消息 ~90s（模型先探索 Pan 项目代码库 20+ 次再发现 MCP 工具）。探索是**必然的**——每个 cbc 进程从零开始，不继承任何项目知识。

## 思路

"已探索过的 character/session" 是一笔资产——它记住了项目结构、MCP 工具列表，后续消息不再探索代码。如果能**克隆**这种已完成的 session，新 session 继承其探索历史，首条消息就跳过探索。

## 核心假设

`_consumer_mcp` 每次调用都重放全部 history：`_format_history_for_context(s.history)`。如果 branching 能复制历史，新 session 的 cbc 会看到 "之前曾经成功调用了 game_list..."——模型可能直接进入 MCP 工具使用，而非从零探索。

## 需要确认的问题

1. **Branch API 目前做了什么？** 是复制 session state 还是只复制配置？
2. **历史重放格式**——`_format_history_for_context` 对 MCP tool_use/tool_result 的序列化效果如何？
3. **workdir 隔离**——每个 session 有自己的 workdir，但 cbc 用 `-d workdir` 指定项目目录，历史里可能包含硬编码路径

## 分阶段实施

### Phase 1：调研 branch 现有能力 (30min)

- 读 `/api/sessions/{id}/branch` handler
- 读 `_format_history_for_context` 实现
- 手工 branch 一个已探索 session，看继承了什么

### Phase 2：改造 branch 为 "克隆" (1h)

如果现有 branch 不完全复制历史，补充：
- clone 时复制完整 history + adapter_config + system_prompt
- 可选：同时复制 workdir 中的 `.codebuddy/mcp.json`
- 可选：标记 "isTemplate" / "isPrimed" 状态

### Phase 3：探索剪枝 (30min)

为了让克隆的 history 更紧凑：
- 去掉 codebase 探索消息（Grep/Bash/Agent），只保留 MCP tool_use/tool_result
- 把成功的 tool 调用作为 "范例" 前置
- 探索 `_format_history_for_context` 过滤能力

### Phase 4：验证 (30min)

- 创建 primed session（跑一次完整探索 + game_list 成功）
- branch 出新 session
- 发首条消息，对比冷启动耗时

## 不覆盖的范围

- system_prompt 优化（已做过）
- history 截断（已在计划中，独立解决）
- `--resume` 评估（独立议题）

## 关键文件

- `packages/web/server.py` — branch handler + session creation
- `packages/core/worker.py` — `_consumer_mcp`, `_format_history_for_context`
- `packages/core/session.py` — Session dataclass
