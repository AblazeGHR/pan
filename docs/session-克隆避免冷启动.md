# 方案：利用 profile 模板化避免 MCP 首条消息冷启动

> **状态更新 2026-07-30**：后续消息性能已通过 `--resume` 解决(commit `80d5f7c`)。
> 本方案专注于**首条消息冷启动**优化（新 session = 新 cbc 进程 = 零上下文）。

## 问题

新 session 首条消息 ~90s（模型先探索 Pan 项目代码库 20+ 次再发现 MCP 工具）。
后续消息已通过 `--resume` 优化至 ~35s。但首条仍慢。

## 简化设计

不实现独立的 session 克隆 API，而是利用现有的 **profile → character** 管线：

- **Profile** 除了 `mcp_servers`、`system_prompt` 等字段外，可选携带 `bootstrap_session`（引用一个已探索过的 session 的 key history）
- **Character** 创建时继承 profile 的 `bootstrap_context`
- **Worker** 首条消息时，将 `bootstrap_context` 注入 prompt（在 system_prompt 之前），模型看到 "之前曾成功调用过 MCP 工具" 后跳过探索

## 数据流

```
Profile (manifest.json)
  ├── system_prompt: "你是 COC 守秘人..."
  ├── mcp_servers: [rulewhisper]
  └── bootstrap_session: "ses_abc123"        ← 新增：模板 session ID

Character (创建时)
  ├── system_prompt (from profile)
  ├── mcp_servers (from profile)
  └── bootstrap_context (from profile.bootstrap_session)  ← 新增：编译后的上下文

Worker._consumer_mcp (首条消息)
  text = f"[Bootstrap: 之前已成功通过 ToolSearch 找到 RuleWhisper MCP 工具...]\n{text}\n\n---\n{s.system_prompt}"
```

## Bootstrap Session 的生命周期

1. **手动创建模板 session**：用 CLI/API 跑一次完整探索 + game_list
2. **编译 bootstrap_context**：从 session history 提取关键信息，压缩为文本摘要
3. **绑定到 profile**：profile 的 `bootstrap_session` 指向该 session
4. **新 character 继承**：create_character 时从 bootstrap session 提取上下文注入 character

## 实施步骤

### Step 1：Profile 增加 bootstrap_session 字段 (15min)

manifest.json 中 profile 增加：
```json
{
  "name": "coc-keeper",
  "mcp_mode": "always",
  "bootstrap_session": "ses_template_coc"  // 模板 session ID
}
```

manifest_loader 解析该字段到 Profile dataclass。

### Step 2：Character 继承 bootstrap_context (15min)

Character dataclass 增加 `bootstrap_context: str | None` 字段。
create_character 时从 profile 的 bootstrap_session 读 session JSON，提取 key history 编译为文本。

### Step 3：Worker 注入 bootstrap_context (15min)

`_consumer_mcp` 首条消息时（`not s.cli_session_id`），若 `char.bootstrap_context` 存在：
```
{bootstrap_context}
---
{user_text}
---
{system_prompt}
```

### Step 4：验证 (15min)

- 创建模板 session → 编译 bootstrap_context
- 从 profile 创建新 character → start session → spawn worker
- 首条消息 "list games"，对比耗时

## 不覆盖的范围

- system_prompt 优化（已做过）
- `--resume`（已解决）
- 自动编译 bootstrap context（先手动编辑）
- 多个模板 session（先支持一个）

## 关键文件

- `manifest.json` / `manifest_loader.py` — profile 字段扩展
- `packages/core/character.py` — Character 增加 bootstrap_context
- `packages/core/worker.py` — `_consumer_mcp` 注入逻辑
- `data/sessions/ses_template_*.json` — 模板 session 数据
