# RuleWhisper 联动实施手册（Pan 侧 P2 / P3）

> 配套设计文档：`RuleWhisper联动与框架优化建议.md`（同目录）。本手册聚焦**落地步骤**，使用 RuleWhisper 已交付（P0/P1）的**真实接口**，供 Pan 侧按步执行。
> 关联（RuleWhisper 仓库）：`docs/Pan联动实施方案.md`。

---

## 〇、前置状态（RuleWhisper 侧，已交付并推送）

| 能力 | 形态 | 访问方式 |
|------|------|----------|
| P0 HTTP API | FastAPI，端口 **9731** | `http://127.0.0.1:9731` |
| P1 MCP Server | fastmcp，6 个工具 | stdio（默认）或 SSE（端口 **9733**） |
| 插件声明 | `pan_plugin/manifest.json` | 见 RuleWhisper 仓库根目录 |

**MCP 启动命令**（stdio，供 cbc/kimi 作为子进程 spawn）：

```bash
python -m src.server.mcp    # 从 RuleWhisper 仓库根运行
```

**MCP 工具清单（6 个）**：`query_rule`、`roll_dice`、`get_weapon`、`get_monster`、`get_spell`、`get_skill`。

**HTTP API 端点**：`GET /api/health`、`POST /api/query`、`POST /api/dice`、`GET /api/weapon|monster|spell|skill/{name}`、`GET /api/rule/{page}`。

---

## 一、概念体系（Pan 侧四个核心层级）

| 概念 | 定义 | 谁管 |
|------|------|------|
| **profile** | Character 创建模板。声明 adapter / model / mcp_servers / system_prompt。 | manifest.json `profiles[]` |
| **character** | 独立机器人实例，由 profile 创建。有唯一记忆，可持多个 session。同一 profile 可创建多个同规格的 character，各自记忆独立。 | Pan 管理 |
| **session** | character 下的一个对话房间（绑 group_id），消息历史属于 session。session 内需要计算时才 spawn Worker，Worker 无状态无个性。 | Pan 管理 |
| **Worker** | 纯计算进程（cbc/kimi 子进程），依附于 session，仅承载 LLM 推理。**无状态、无个性**——identity/memory 在 character，上下文在 session。 | Pan spawn，透明 |

## 二、核心原则：Pan 通用化

Pan 的 `config.json` / `plugin.py` **不出现任何 RuleWhisper 的字面量**（`.rc`、`coc-keeper`、`src.server.mcp` 等）。一切领域约定由 RuleWhisper 的 `manifest.json` 声明，Pan 只是一个通用加载器。

**接入一个新项目只需**：项目方写一份 `manifest.json` → Pan 的 `config.json` 加一行引用。

---

## 三、P2：通用 Manifest Loader（Pan 侧改动）

目标：Pan 实现一个通用 loader，读取各插件的 `manifest.json`，合并 `profiles`、`mcp_servers`、`command_routes`。

### 3.1 RuleWhisper 的 manifest（已交付，不需动）

RuleWhisper 仓库 `pan_plugin/manifest.json` 已包含：

```json
{
  "name": "rulewhisper",
  "health_check": "http://127.0.0.1:9731/api/health",
  "command_routes": [
    { "prefixes": [".rc", ".ra", ".rb", ".rp", ".rs", ".sc", ".dam"],
      "target": "http://127.0.0.1:9731/api/dice", "method": "POST", "strip_prefix": true },
    { "prefixes": [".coc", ".rule"],
      "target": "http://127.0.0.1:9731/api/query", "method": "POST", "strip_prefix": true }
  ],
  "mcp_servers": [
    { "name": "rulewhisper", "command": "python", "args": ["-m", "src.server.mcp"], "cwd": "${PLUGIN_DIR}" }
  ],
  "profiles": [
    { "name": "coc-keeper", "adapter": "cbc", "model": "hy3",
      "permission_mode": "bypassPermissions", "mcp_servers": ["rulewhisper"],
      "system_prompt": ["你是 COC 守秘人(KP)，用中文回复。","所有规则查询和骰子检定都通过 RuleWhisper 工具进行，绝不自己编数据。","检定结果需展示公式与最终值。"] }
  ]
}
```

> `${PLUGIN_DIR}` 由 Pan loader 启动时替换为 manifest 所在目录的绝对路径。

### 3.2 Pan 侧 `config.json` — 只加一行引用

```jsonc
{
  "plugin_manifests": [
    "../RuleWhisper/pan_plugin/manifest.json"
  ]
}
```

Pan 自身的 `profiles`、`mcp_servers`、`command_routes` 不再手写——全部从 manifest 加载并合并。

### 3.3 Loader 实现要点

**启动时（Pan Core 初始化）**：
1. 遍历 `plugin_manifests`，逐个读取 JSON。
2. 解析 `${PLUGIN_DIR}` → manifest 所在目录的真实绝对路径。
3. 合并 `mcp_servers` 到全局池（按 name 去重）。
4. 合并 `profiles` 到全局池（按 name 去重）。
5. 把 `command_routes` 注入 QQ Bot 的消息处理器前缀匹配环。

**Session 需要计算时（spawn Worker）**：
- 若 `SpawnRequest.profile` 引用了某 profile，Core 解析其 `adapter`/`model`/`permission_mode`/`mcp_servers` 并合并默认值。
- adapter 的 `build_spawn_args` 调用 `_mcp_args(self.mcp_servers)`，将 MCP 配置注入 `--mcp-config`（同提案文档「二、改一动议：MCP 透传」的方案）。

### 3.4 安全

- MCP Server 进程随 Worker 子进程生命周期启动/回收（需验证 Pan 的进程管理会递归 kill 子进程树）。
- `config.json` 保持 gitignored，仅受信用户配置；非信任 MCP Server 是指令注入入口。

### 3.5 P2 验收

- [ ] Pan `config.json` 只含 `plugin_manifests`（不含硬编码的 RuleWhisper 相关项）。
- [ ] 用 `coc-keeper` profile 创建一个 character，其 session 内 Worker 日志可见 `src.server.mcp` 子进程拉起。
- [ ] session 中要求「查短剑属性」「掷 .rc 侦察 60」，LLM 调用 `get_weapon` / `roll_dice` 返回真实数据。

---

## 四、P3：联调（QQ 群内全链路）

目标：群内消息按 manifest 声明的 `command_routes` 路由，自然语言走 LLM（自动用 RuleWhisper MCP）。

### 4.1 QQ 命令路由（均来自 manifest，Pan 侧零硬编码）

| 前缀 | 目标 | 来源 |
|------|------|------|
| `.rc` `.ra` `.rb` `.rp` `.rs` `.sc` `.dam` | `POST /api/dice` | `manifest.command_routes[0]` |
| `.coc` `.rule` | `POST /api/query` | `manifest.command_routes[1]` |
| 无前缀 / 其他 | `_send_and_wait` → LLM 路径 | 保持不变 |

Pan 的 `plugin.py` 在 `handle_message` 开头遍历从 manifest 加载的 `command_routes` 列表：命中 → `POST` 对应 `target`，取返回文本回复，不经过 character/Worker，未命中 → 走现有 LLM 路径。

请求体：`{"text": "<原始消息去掉前缀后的内容>"}`。

### 4.2 冒烟测试清单

**确定性链路**
- [ ] 群内发 `.rc 1d100 侦察检定` → 毫秒级收到 `[x/y] 等级` 结果。
- [ ] 群内发 `.coc 短剑` → 收到武器/规则数据。
- [ ] 群内发 `.dam 1d6` → 收到伤害掷骰结果。

**自然语言链路**
- [ ] 群内问「短剑的伤害是多少？恐怖猎手怎么闪避？」→ LLM 调用 `get_weapon`/`get_monster`/`query_rule` 后综合回复。

**回归**
- [ ] 联调期间 `/api/health` 持续返回 `{"status":"ok"}`。

### 4.3 P3 验收

- [ ] 确定性指令 0 LLM token 消耗、延迟 < 50ms。
- [ ] 自然语言问答引用 RuleWhisper 真实数据，无编造。
- [ ] 群级 session 绑定（提案「四、群级 Session」）下多人 at 同一 character 的 session，状态共享。

---

## 五、关联文档

| 文档 | 位置 | 用途 |
|------|------|------|
| RuleWhisper Plugin Manifest | RuleWhisper `pan_plugin/manifest.json` | 插件的全部声明（profiles/routes/mcp） |
| RuleWhisper 联动 & 框架优化建议 | Pan `docs/plans&overviews/` | 设计提案与改动点详述 |
| Pan 联动实施方案 | RuleWhisper `docs/Pan联动实施方案.md` | RuleWhisper 侧计划与进度 |
| PLAN | RuleWhisper `docs/PLAN.md` | 整体路线图 |

---

*创建：2026-07-22 · 最后更新：2026-07-22（改为 manifest loader 方案）· 适用：Pan 侧 P2（Loader）+ P3（联调）*
