# Phase 2 收尾功能计划

> 基于 2026-08-13 对 `目标与范围.md` §7、`RuleWhisper联动方案.md` §6/§11、`Meta-Agent与Worker通信机制设计立项.md` 三份文档与当前代码库的对照梳理，记录 Phase 2 收尾阶段的功能缺口、排序与实施要点。
>
> 状态：路线图 | 创建：2026-08-13

---

## 一、当前状态盘点

### 1.1 已落地（Phase 2 主要交付）

| 模块 | 状态 | 关键文件 |
|------|------|---------|
| Core 清洁化 | ✅ | `packages/core/` API 稳定 |
| QQ Channel | ✅ | `packages/qq/`，群级 session 绑定已实现 |
| Web Channel | ✅ | React SPA + Vanilla 双前端共存 |
| Remote Channel | ✅ | Cloudflare Tunnel |
| MCP（Meta-Agent） | ✅ | `packages/mcp/` 标准 MCP 工具 |
| Memory | ✅ | `packages/core/memory/` SQLite+FTS5+embedding |
| Manifest Loader | ✅ | `packages/core/manifest_loader.py` 已加载 profiles/mcp_servers/command_routes |
| Adapter（cbc/kimi） | ✅ | `--mcp-config` 注入已实现 |
| Character/Profile | ✅ | profile → character → memory |
| Session `game_id` 字段 | ✅ | `packages/core/session.py:37, 110, 137, 163` |

### 1.2 React 前端

`frontend/react` 已合入 main（`cbae3db`），双前端模式稳定。React 路线图见 `docs/react-refactor-plan.md`，剩余项（Monaco 编辑器）属未来扩展，**不在本计划范围**。

### 1.3 与既有文档的偏差

`RuleWhisper联动方案.md` §6 标注"QQ 前缀命令路由 + game_id 绑定未实现"。实际代码核查发现：
- `session.py` 的 `game_id` 字段**已实现**（创建/序列化/读取全链路就绪）
- `manifest_loader.py` 的 `command_routes` **已加载**（`ManifestConfig.command_routes` 已填充）
- 真正未完成的只是"接上"：server 暴露端点 + plugin.py 接入路由 + game_id 反查入口

文档描述滞后于代码。本计划按代码现状为准。

---

## 二、功能缺口清单与排序

按"功能价值 × 改动成本"排序。价值维度看是否完成 Phase 2 既定目标、是否补齐已立项但未实施的能力；成本维度按预估改动量。

| 优先级 | 项 | 价值 | 成本 | 触发条件 |
|--------|----|------|------|---------|
| **P0** | QQ 前缀命令路由 + game_id 反查 | 完成 RuleWhisper 联动最后一块；确定性指令毫秒级响应 | 低（~80 行） | 立即可做 |
| P1 | Worker 状态机补全（spawning/zombie/queued） | Meta-Agent 调度准确性前提 | 中 | 启动 Meta-Agent 智能调度前 |
| P2 | SDK 模块立项 | Phase 2 最后一块；Worker 包装为专用 Agent | 高（先立项） | 需先明确形态 |
| P3 | Memory P2+：Session transcript 索引 | 让记忆从 .md 扩展到对话历史 | 中 | 已有 P1+ Memory 基建 |
| P4 | Worker 间通信（收件箱/三原语） | 链式工作流前提 | 高 | 出现具体协作场景 |
| P5 | QQ Bot 轮询改 WebSocket 推流 | 延迟从 1.5s → 实时 | 中 | 单人场景够用，低优 |
| P6 | Monaco 编辑器（前端） | Dashboard 内编辑 session workdir | 中 | 非核心定位，最低优 |

---

## 三、P0 实施细化：QQ 前缀命令路由 + game_id 反查

### 3.1 现状

- `packages/qq/plugin.py:267` `handle_message` 对群内所有消息一律走 `_send_and_wait` → LLM 路径
- `manifest_loader.py` 已把 `command_routes` 加载进 `ManifestConfig`，但 `plugin.py` 拿不到（plugin 是独立 NoneBot 进程，不能 import Core）
- `server.py` 未暴露 `command-routes` HTTP 端点
- `session.py` 已有 `game_id` 字段，但无写入入口（KP 怎么把 group_id→game_id 绑定存进 session）

### 3.2 目标

群内消息按 manifest 声明的 `command_routes` 路由：
- **确定性指令**（`.rc`/`.coc`/`.dam` 等前缀）→ 毫秒级直发 HTTP API，0 LLM token
- **无前缀/其他** → 走现有 LLM 路径（自动用 RuleWhisper MCP）
- LLM 路径调 MCP tool 时，`game_id` 从 session metadata 取

### 3.3 改动文件清单

| 文件 | 改动 | 预估行数 |
|------|------|---------|
| `packages/web/server.py` | 新增 `GET /api/manifest/command-routes` 端点（返回 `ManifestConfig.command_routes`） | ~10 |
| `packages/qq/plugin.py` | `_startup` 拉取并缓存 command_routes；`handle_message` 开头前缀匹配环（按 prefix 长度降序）；命中 → POST `target` → 回复 | ~50 |
| `packages/qq/plugin.py` | `_ensure_session` 增 game_id 反查：群级 session 创建后，调 RuleWhisper HTTP 拿 game_id 存入 session metadata | ~20 |
| `packages/core/session.py` | （已就绪）`update` API 确认能写 `game_id` 字段 | 0~5 |
| `packages/web/server.py` | `PATCH /api/sessions/{id}` 或专用端点支持写 `game_id` | ~10 |

**总改动量：~80-100 行**

### 3.4 关键实施注意点

1. **前缀匹配顺序**：`command_routes` 加载后按 `prefix` 字符串长度**降序**排列，避免 `.rc` 吃掉 `.rca`（详见 `RuleWhisper联动方案.md` §11.2）
2. **请求体字段名**：RuleWhisper HTTP API 接收 `{"text": "..."}`，**不是 `{"raw": "..."}`**
3. **target 字段**：manifest 里 `target` 是绝对 URL（如 `http://127.0.0.1:9731/api/dice`），plugin.py 直接 POST 即可
4. **MCP config JSON 序列化**：`json.dumps(srv, ensure_ascii=False, separators=(',', ':'))` —— 紧凑、保留中文（adapter 已实现，仅作记录）
5. **game_id 来源链**：KP 通过 RuleWhisper CLI 手动创建 game 并绑定 group_id → Pan plugin.py 根据 `scope_id`（即 group_id）反查 → 存入 session `game_id` 字段。**依赖 RuleWhisper HTTP API 提供 `GET /api/games?group_id=xxx`**（实施前需确认 RW 侧已就绪）
6. **Worker 进程回收**：MCP Server 由 Worker 子进程 spawn，Worker kill 时需确保递归回收（已用 `psutil`，需端到端验证）

### 3.5 验收清单

**确定性链路**
- [ ] 群内发 `.rc 1d100 侦察检定` → 毫秒级收到 `[x/y] 等级` 结果，0 LLM token 消耗
- [ ] 群内发 `.coc 短剑` → 收到武器/规则数据
- [ ] 群内发 `.dam 1d6` → 收到伤害掷骰结果
- [ ] 前缀路由延迟 < 50ms

**自然语言链路**
- [ ] 群内问"短剑的伤害是多少？恐怖猎手怎么闪避？" → LLM 调用 MCP 后综合回复（日志可见 tool call）
- [ ] LLM 调 `get_weapon` 等 tool 时 `game_id` 从 session 取并传入，返回真实数据无编造

**回归**
- [ ] `python -m pytest tests/ -q` 全绿（当前基线 88 passed）
- [ ] 联调期间 RuleWhisper `/api/health` 持续返回 `{"status":"ok"}`

### 3.6 实施步骤

1. `server.py` 加 `GET /api/manifest/command-routes` 端点 + `PATCH /api/sessions/{id}` 支持 `game_id` 字段写入
2. `plugin.py:_startup` 拉取 command_routes 缓存到模块级变量（带失败重试）
3. `plugin.py:handle_message` 在 `_send_and_wait` 前插入前缀匹配环；命中 → POST target → 回复 → return
4. `plugin.py:_ensure_session` 群级 session 创建/复用时，调 RW HTTP 反查 game_id 并 PATCH 到 session
5. 端到端联调（按 §3.5 验收清单逐项验证）

---

## 四、P1-P6 各项要点

### P1｜Worker 状态机补全

- **现状**：Worker 状态只有粗粒度"活/死"，无法区分 spawning（刚 spawn 未就绪）/ zombie（已死未回收）/ queued（排队中）
- **价值**：Meta-Agent 调度准确性前提；`Meta-Agent与Worker通信机制设计立项.md` Q2 的子问题之一
- **改动量**：中等（`packages/core/worker.py` 状态枚举 + 各处 spawn/kill 流转 + Dashboard 显示）
- **触发条件**：启动 Meta-Agent 智能调度前

### P2｜SDK 模块（Phase 2 最后一块）

- **现状**：README "SDK 规划中"、`目标与范围.md` §5.3 标注"⏳ 待启动"
- **功能定位**：让 Worker 快速包装为专用 Agent（接入新领域时不必每次手写 adapter）
- **形态未定**：代码生成 vs 配置模板 —— **需先立项再实施**
- **触发条件**：有第二个外部项目（非 RuleWhisper）想接入 Pan 时立项

### P3｜Memory P2+：Session transcript 索引

- **现状**：`phase1-memory-plan.md` §1 留的 P2+ 三件事之一。Memory 只索引 `.md` 知识文件，未索引 session 历史对话
- **价值**：让记忆从知识库扩展到"对话历史"，与 Pan 管理多 CLI Agent 的核心定位契合
- **改动量**：中等（`packages/core/memory/session_indexer.py` 已规划但未实现；接 Core WS 事件流增量索引 `assistant`/`tool` 消息）
- **触发条件**：Character 跨 session 复用历史对话的需求出现时

### P4｜Worker 间通信（收件箱/三原语）

- **现状**：`Meta-Agent与Worker通信机制设计立项.md` §落地进展明确："`packages/mcp/` 已落地，**Worker 间通信仍属规划**"
- **价值**：链式工作流（Worker A 完成 → Worker B 启动）的前提
- **改动量**：高（Core 引入消息路由层 + Worker 收件箱 + Meta-Agent 编排原语）
- **触发条件**：出现具体协作场景（如代码审查 → 修复 → 测试的链式编排）

### P5｜QQ Bot 轮询改 WebSocket 推流

- **现状**：`plugin.py` 1.5s 定时轮询 `/api/sessions/{id}`
- **价值**：延迟从 1.5s → 实时
- **改动量**：~120 行（WS 连接 + 断线重连 + Core 重启重订阅）
- **触发条件**：单人场景够用，低优先级

### P6｜Monaco 编辑器（前端）

- **现状**：`docs/react-refactor-plan.md` §"未来扩展"，目录已规划 `src/editor/`，后端 `/api/fs/*` 已就绪
- **价值**：Dashboard 内编辑 session workdir 文件
- **改动量**：中等（`@monaco-editor/react` 已在 package.json，但组件未实现）
- **触发条件**：非 Pan 核心定位（"不管理 Worker 的工作目录内容"——`目标与范围.md` §2），最低优先级

---

## 五、优先级结论

**P0 是当之无愧的下一步**：
- 功能价值明确（完成 RuleWhisper 联动最后一块，是 Phase 2 的重要验收项）
- 改动量小（~80-100 行，基建已就绪，只差接上）
- 文档已写好实施步骤（`RuleWhisper联动方案.md` §11）和验收清单（§6.3）
- 风险低（不影响现有 LLM 路径，前缀匹配是纯增量逻辑）

后续按 P1→P2→P3 推进；P4/P5/P6 视具体场景触发再做。

---

## 六、关联文档

- `docs/plans&overviews/目标与范围.md` §7 — Phase 2 待后续讨论清单
- `docs/plans&overviews/RuleWhisper联动方案.md` §6/§11 — QQ 命令路由实施步骤
- `docs/plans&overviews/Meta-Agent与Worker通信机制设计立项.md` — Worker 间通信调研
- `docs/plans&overviews/phase1-memory-plan.md` §1 — Memory P2+ 范围
- `docs/react-refactor-plan.md` — Monaco 编辑器未来扩展
