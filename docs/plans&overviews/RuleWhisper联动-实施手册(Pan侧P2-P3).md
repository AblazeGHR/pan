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


---

## 六、实施计划与关键注意点

### 6.1 实施顺序

**第 1 步：Manifest Loader**

新建 `packages/core/manifest_loader.py`：

```
Pan 启动时遍历 config.json 的 plugin_manifests → 逐行读 manifest.json →
合并 profiles/mcp_servers/command_routes 到全局池。
```

关键逻辑：
- `${PLUGIN_DIR}` 解析为 manifest 所在目录的绝对路径（用于 MCP Server 的 cwd）
- 同名 profile/mcp_server 后加载的覆盖先加载的（或报冲突）——定一种策略即可
- 加载失败（JSON 解析错、文件不存在）→ 打 warning log，继续加载其他 manifest，不崩

**第 2 步：Pan config.json 增加 plugin_manifests**

```jsonc
{
  "plugin_manifests": [
    "../RuleWhisper/pan_plugin/manifest.json"
  ]
}
```

去掉原先硬编码的 profiles/mcp_servers/command_routes。Pan 自己的 config 里不再出现 RuleWhisper 任何字面量。

**第 3 步：adapter 注入 MCP config**

在 `cbc/kimi` adapter 的 `build_spawn_args` 中：
- 从 spawn settings 读取 `mcp_servers` 列表（由 loader 从 manifest 灌入）
- 调用 `_mcp_args(servers)` 生成 `--mcp-config` 参数串
- 追加到 spawn args

**第 4 步：QQ Bot 命令路由**

在 `packages/qq/plugin.py` 的 `handle_message` 函数开头插入前缀匹配：

```
遍历 manifest_loader 提供的 command_routes 列表
  → 命中前缀 → POST target（HTTP 直发）
  → 未命中 → 走现有 LLM 路径（_send_and_wait）
```

请求体格式注意：RuleWhisper 的 `/api/dice` 和 `/api/query` 接收 `{"text": "..."}`，不是 `{"raw": "..."}`。`strip_prefix: true` 时去掉前缀再发给 API。

**第 5 步：Session 绑定 game_id**

Pan 的 session metadata 增加 `game_id` 字段。每次调 RuleWhisper MCP tool 时从 session 取 game_id 并传入。

game_id 的来源：
- 初期：KP 在群内手动创建 game（`python -m src.cli game new ...`）并绑定 group_id
- Pan 根据 session 的 group_id 反查 game_id（遍历 game/ 下各 game.json 的 group_id 字段匹��）

**第 6 步：联调验证**

按 P3 冒烟清单逐项验收（确定性指令 + 自然语言链路 + health 回归）。

### 6.2 关键注意点

#### ⚠️ command_routes 的前缀匹配顺序

`manifest.command_routes[]` 是数组，匹配顺序有讲究：
- `.rc` 和 `.rca` 同时存在时，`.rca` 应排在前面（长前缀优先）
- Pan loader 加载后按 `strip_prefix` 长度降序排列，避免短前缀误吞长前缀

#### ⚠️ MCP config JSON 的序列化

cbc 的 `--mcp-config` 接收 JSON 字符串。`_mcp_args` 中 `json.dumps(srv)` 时注意：
- `ensure_ascii=False`（保留中文）
- `separators=(',', ':')`（紧凑格式，避免空格干扰参数解析）
- 整个 JSON 值需要被 shell 安全包裹（加引号）

#### ⚠️ Worker 进程回收

MCP Server 由 Worker 子进程 fork（或 spawn），Worker kill 时需要确保 MCP 子进程也被回收：
- 验证 Pan 的进程管理是否递归 kill 进程树
- 如果只 kill 父进程不 kill 子进程，会导致 MCP Server 僵尸进程残留

#### ⚠️ 请求体字段名

RuleWhisper HTTP API 接收 `{"text": "..."}` 而不是 `{"raw": "..."}`。Pan 的前缀路由转发时需确保字段名正确。

POST body 示例：
```python
payload = {"text": stripped_message}
resp = await client.post(route["target"], json=payload)
```

#### ⚠️ 不同 plugin manifest 的同名冲突

如果两个 manifest 都定义了 `name: "rulewhisper"` 的 mcp_server：
- 策略 A：后加载覆盖先加载（简单）
- 策略 B：报错退出（安全）
- 建议先实现 A，manifest name 本身是去重键

#### ⚠️ game_id 的传递链

完整链路：
```
QQ 消息 "短剑伤害"
  → Pan 未命中 command_routes → 走 LLM 路径
  → 从 session metadata 取 game_id
  → spawn Worker（通过 mcp_servers 注入 RuleWhisper MCP）
  → LLM 调 get_weapon(game_id="xxx", name="短剑")
  → RuleWhisper MCP 根据 game_id 读取对应的规则版本和武器数据
  → 返回
```

关键：`game_id` 不需要在 spawn Worker 时传入——它只是 MCP tool 的参数，LLM 调 tool 时 Pan 从 session metadata 取并传递。

### 6.3 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `packages/core/manifest_loader.py` | 新建 | loader 主逻辑 |
| `config.json`/`config.example.json` | 改造 | 增加 plugin_manifests，移除硬编码的 profiles/mcp/routes |
| `packages/core/adapters/cbc/adapter.py` | 改造 | 增加 mcp_args helper + build_spawn_args 注入 |
| `packages/core/adapters/kimi/adapter.py` | 改造 | 同上（kimi 适配器的 MCP 支持） |
| `packages/qq/plugin.py` | 改造 | 前缀路由匹配（command_routes） |
| `packages/core/session.py`（或等效文件） | 改造 | session metadata 增加 game_id |
| `docs/plans&overviews/RuleWhisper联动-实施手册(Pan侧P2-P3).md` | 本文件 | 实施手册 + 计划 |

---

*创建：2026-07-22 · 最后更新：2026-07-22 · 状态：待实施*
