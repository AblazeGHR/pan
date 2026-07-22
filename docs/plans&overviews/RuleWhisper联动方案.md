# RuleWhisper 联动方案

> 从设计提案到 Phase 1 实施手册的统一文档。关联（RuleWhisper 仓库）：`docs/Pan联动实施方案.md`。

---

## 一、概念体系（Pan 侧四个核心层级）

| 概念 | 定义 | 谁管 |
|------|------|------|
| **profile** | Character 创建模板。声明 adapter / model / mcp_servers / system_prompt。 | manifest.json `profiles[]` |
| **character** | 独立机器人实例，由 profile 创建。有唯一记忆，可持多个 session。同一 profile 可创建多个同规格的 character，各自记忆独立。 | Pan 管理 |
| **session** | character 下的一个对话房间（绑 group_id），消息历史属于 session。session 内需要计算时才 spawn Worker，Worker 无状态无个性。 | Pan 管理 |
| **Worker** | 纯计算进程（cbc/kimi 子进程），依附于 session，仅承载 LLM 推理。**无状态、无个性**——identity/memory 在 character，上下文在 session。 | Pan spawn，透明 |

---

## 二、前置状态（RuleWhisper 侧，已交付并推送）

| 能力 | 形态 | 访问方式 |
|------|------|----------|
| P0 HTTP API | FastAPI，端口 **9731** | `http://127.0.0.1:9731` |
| P1 MCP Server | fastmcp，全部能力（规则/骰子/数据/game-char/版本管理，~15 个，rebuild_index 除外） | stdio（默认）或 SSE（端口 **9733**） |
| 插件声明 | `pan_plugin/manifest.json` | RuleWhisper 仓库根目录 |

**MCP 启动命令**（stdio，供 cbc/kimi 作为子进程 spawn）：

```bash
python -m src.server.mcp    # 从 RuleWhisper 仓库根运行
```

> ⚠️ 模块路径是 `src.server.mcp`（不是 `rulewhisper.server.mcp`）。stdio 模式不需要 `--port`。

**MCP 工具清单（全部能力，rebuild_index 除���）**：`query_rule`、`roll_dice`、`get_weapon`、`get_monster`、`get_spell`、`get_skill`。所有 tool 接受 `game_id` 参数（允许 `null`）。

**HTTP API 端点**：`GET /api/health`、`POST /api/query`、`POST /api/dice`、`GET /api/weapon|monster|spell|skill/{name}`、`GET /api/rule/{page}`。

---

## 三、设计背景与动机

[RuleWhisper](https://github.com/AblazeGHR/RuleWhisper) 是 COC 跑团助手（Python CLI），能力：191 条规则全文检索、98 武器 / 88 怪物 / 109 法术结构化查询、COC7 骰子引擎。**不自建 LLM 和 QQ Bot**。

Pan 已有 QQ Bot 通道（`packages/qq/plugin.py`）和 Worker 管理能力（cbc/kimi CLI 子进程），两条腿都是 RuleWhisper 需要但还没长的。

联动策略：**RuleWhisper 不自己造 bot 和 LLM 接入，而是通过 HTTP API + MCP Server 成为 Pan 的工具提供方。Pan 的 QQ Bot 承担消息入口，Pan 管理的 Worker 承担推理调度。**

---

## 四、核心原则：Pan 通用化

Pan 的 `config.json` / `plugin.py` **不出现任何 RuleWhisper 的字面量**（`.rc`、`coc-keeper`、`src.server.mcp` 等）。一切领域约定由 RuleWhisper 的 `manifest.json` 声明，Pan 只是一个通用加载器，负责 profile → character → session → Worker 的生命周期管理。

**接入一个新项目只需**：项目方写一份 `manifest.json` → Pan 的 `config.json` 加一行引用。未来 DND 骰娘、Fate 规则引擎同理——写 manifest、加引用，不改 Pan 代码。

---

## 五、Phase 1：通用 Manifest Loader（Pan 侧改动）

目标：Pan 实现一个通用 loader，读取各插件的 `manifest.json`，合并 `profiles`、`mcp_servers`、`command_routes`。

### 5.1 RuleWhisper 的 manifest（已交付，不需动）

RuleWhisper 仓库 `pan_plugin/manifest.json` 已包含 `_concepts`（概念定义）、`command_routes`（骰令/规则前缀路由）、`mcp_servers`（`${PLUGIN_DIR}` 占位）、`profiles`（coc-keeper，model=hy3）。

> `${PLUGIN_DIR}` 由 Pan loader 启动时替换为 manifest 所在目录的绝对路径。

### 5.2 Pan 侧 `config.json` — 只加一行引用

```jsonc
{
  "plugin_manifests": [
    "../RuleWhisper/pan_plugin/manifest.json"
  ]
}
```

Pan 自身的 `profiles`、`mcp_servers`、`command_routes` 不再手写��—全部从 manifest 加载并合并。

### 5.3 Loader 实现要点

**启动时（Pan Core 初始化）**：
1. 遍历 `plugin_manifests`，逐个读取 JSON。
2. 解析 `${PLUGIN_DIR}` → manifest 所在目录的真实绝对路径。
3. 合并 `mcp_servers` 到全局池（按 name 去重）。
4. 合并 `profiles` 到全局池（按 name 去重）。
5. 把 `command_routes` 注入 QQ Bot 的消息处理器前缀匹配环。

**Session 需要计算时（spawn Worker）**：
- 若 profile 引用被选中，Core 解析其 `adapter`/`model`/`permission_mode`/`mcp_servers` 并合并默认值。
- adapter 的 `build_spawn_args` 调用 `_mcp_args(self.mcp_servers)`，将 MCP 配置注入 `--mcp-config`。

### 5.4 adapter 注入 `--mcp-config`

参照现有 `model_args` / `permission_mode_args` 模式，新增 helper：

```python
def _mcp_args(self, servers: list[dict]) -> list[str]:
    args = []
    for srv in servers:
        args.extend(["--mcp-config", json.dumps(srv, ensure_ascii=False, separators=(',', ':'))])
    return args
```

在 `build_spawn_args` 中 `args.extend(self._mcp_args(s.mcp_servers))`。

### 5.5 Manifest Loader 验收

- [ ] Pan `config.json` 只含 `plugin_manifests`（不含硬编码的 RuleWhisper 相关项）。
- [ ] 用 `coc-keeper` profile 创建一个 character，其 session 内 Worker 日志可见 `src.server.mcp` 子进程拉起。
- [ ] session 中要求「查短剑属性」「掷 .rc 侦察 60」，LLM 调用 `get_weapon` / `roll_dice` 返回真实数据。

---

## 六、Phase 1：联调（QQ 群内全链路）

目标：群内消息按 manifest 声明的 `command_routes` 路由，自然语言走 LLM（自动用 RuleWhisper MCP）。

### 6.1 QQ 命令路由（均来自 manifest，Pan 侧零硬编码）

| 前缀 | 目标 | 来源 |
|------|------|------|
| `.rc` `.ra` `.rb` `.rp` `.rs` `.sc` `.dam` | `POST /api/dice` | manifest.command_routes[0] |
| `.coc` `.rule` | `POST /api/query` | manifest.command_routes[1] |
| 无前缀 / 其他 | `_send_and_wait` → LLM 路径 | 保持不变 |

Pan 的 `plugin.py` 在 `handle_message` 开头遍历从 manifest 加载的 `command_routes` 列表：命中 → `POST` 对应 `target`，取返回文本回复，不经过 character/Worker；未命中 → 走现有 LLM 路径。

请求体：`{"text": "<原始消息去掉前缀后的内容>"}`。

### 6.2 两条链路

**确定性指令**（毫秒级，不走 LLM）：
```
群消息 ".rc 1d100 侦察检��"
  → Pan plugin.py 遍历 command_routes，命中前缀 ".rc"
  → POST http://127.0.0.1:9731/api/dice
  → RuleWhisper 骰子引擎返回 [60/60] 常规成功！
  → Pan 直接回复群内
```

**自然语言问答**（走 LLM，数据来自 RuleWhisper）：
```
群消息 "短剑伤害多少？恐怖猎手怎么闪避？"
  → Pan 未命中前缀 → 走 LLM 路径
  → 用 coc-keeper profile 的 character session
  → LLM 调 get_weapon / get_monster / query_rule 取真实数据
  → 综合回复群内
```

### 6.3 冒烟测试清单

**确定性链路**
- [ ] 群内发 `.rc 1d100 侦察检定` → 毫秒级收到 `[x/y] 等级` 结果。
- [ ] 群内发 `.coc 短剑` → 收到武器/规则数据。
- [ ] 群内发 `.dam 1d6` → 收到伤害掷骰结果。

**自然语言链路**
- [ ] 群内问「短剑的伤害是多少？恐怖猎手怎么闪避？」→ LLM 调用 MCP 后综合回复（日志可见 tool call）。

**回归**
- [ ] 联调期间 `/api/health` 持续返回 `{"status":"ok"}`。

### 6.4 联调验收

- [ ] 确定性指令 0 LLM token 消耗、延迟 < 50ms。
- [ ] 自然语言问答引用 RuleWhisper 真实数据，无编造。
- [ ] session 管理按 character/session 模型正确隔离。

---

## 七、后续阶段

### 7.1 群级 Session 绑定

**现状**：`plugin.py` 按 `qq_user_id` 绑定 session。A at bot 建一个，B at 又建一个——character state 互相独立，PL 各自看到的东西不一样。

**方案**：`_ensure_session` 按 `scope: "user" | "group"` 区分：

```python
scope = "group" if isinstance(event, GroupMessageEvent) else "user"
scope_id = str(event.group_id) if scope == "group" else str(event.user_id)
prefix = "qqg" if scope == "group" else "qq"
name = f"{prefix}-{scope_id[-6:]}"
```

全群共享一个 character 的 session，跑团多人场景的状态（角色卡、检定结果）天然共享。

**注意事项**：
- 单 Worker 并发：多人同一群几乎同时 at bot → Pan 需确认任务队列是先进先出。
- game_id 传递：Pan session metadata 存储 `game_id`，MCP tool 调用时从 session 取并传入。game_id 来源：KP 通过 RuleWhisper CLI 手动创建 game 并绑定 group_id，Pan 根据 group_id 反查。

### 7.2 轮询改 WebSocket（低优先级）

**现状**：`plugin.py` 1.5 秒定时轮询 `/api/sessions/{id}`。Pan 已有 `/ws` 和 `/ws/agent` WebSocket 端点。

**方案**：QQ Bot 插件在 `on_startup` 建立 WS 连接，通过 `/ws/agent` 订阅活跃 session 的事件流。Core push `new_result` 时 bot 立即响应。风险：断线重连、core 重启后重订阅。优先级低——当前 1.5s 轮询在单人场景够用。

---

## 八、安全考量

- MCP Server 进程随 Worker 子进程生命周期启动/回收（需验证 Pan 进程管理会递归 kill 子进程树——可配合跨平台 `psutil` 方案）。
- `config.json` 保持 gitignored，仅受信用户配置；非信任 MCP Server 是指令注入入口。
- 外部项目**不 import Pan Core 内部**，仅通过 HTTP/WS 或 MCP 协议与 Pan 通信。

---

## 九、优先级与工作量

| # | 改动 | 优先级 | 改动量 | 说明 |
|---|------|--------|--------|------|
| Manifest Loader | 高（Phase 1） | ~80 行 | config 引用 + loader 核心逻辑 + adapter mcp_args |
| QQ 命令路由 | 高（Phase 1） | ~30 行 | plugin.py 遍历 manifest 声明的 command_routes |
| Session 绑定 game_id | 高（Phase 1） | ~30 行 | session metadata 增加 game_id + group_id 反查 |
| 群级 Session 绑定 | 中（后续） | ~30 行 | plugin.py scope 区分 + 确认任务排队 |
| 轮询→WS | 低（后续） | ~120 行 | WS 推流 + 断线重连 |

---

## 十、关联文档

| 文档 | 位置 | 用途 |
|------|------|------|
| RuleWhisper Plugin Manifest | RuleWhisper `pan_plugin/manifest.json` | 插件的全部声明（profiles/routes/mcp） |
| Pan 联动实施方案 | RuleWhisper `docs/Pan联动实施方案.md` | RuleWhisper 侧计划与进度 |
| Game Layer 设计方案 | RuleWhisper `docs/game-layer-设计方案.md` | Game/Character 持久化层设计 |
| PLAN | RuleWhisper `docs/PLAN.md` | 整体路线图 |
| 目标与范围 | Pan `docs/plans&overviews/` | Pan 全局定位 |
| 跨平台适配计划 | Pan `docs/plans&overviews/` | Win/Linux/macOS 改造 |

---

## 十一、实施计划与关键注意点

### 11.1 实施顺序

**第 1 步：Manifest Loader**
新建 `packages/core/manifest_loader.py`。Pan 启动时遍历 `plugin_manifests` → 逐行读 manifest.json → 合��� profiles/mcp_servers/command_routes。

关键逻辑：
- `${PLUGIN_DIR}` 解析为 manifest 所在目录的绝对路径
- 同名 profile/mcp_server 后加载覆盖先加载
- 加载失败打 warning，继续加载其他 manifest，不崩

**第 2 步：Pan config.json**
增加 `plugin_manifests` 引用，去掉硬编码的 profiles/mcp_servers/command_routes。

**第 3 步：adapter 注入 MCP config**
在 cbc/kimi adapter 的 `build_spawn_args` 中注入 `--mcp-config`。

**第 4 步：QQ Bot 命令路由**
plugin.py 遍历 manifest command_routes，前缀匹配 → HTTP 直发；未匹配 → LLM 路径。

**第 5 步：Session 绑定 game_id**
session metadata 增加 `game_id`。KP 手动创建 game 并绑定 group_id，Pan 反查。

**第 6 步：联调验证**
按 6.3 冒烟清单逐项验收。

### 11.2 关键注意点

#### ���️ command_routes 的前缀匹配顺序

`.rc` 和 `.rca` 同时存在时，`.rca` 应排前面。Pan loader 加载后按 prefix 长度降序排列。

#### ⚠️ MCP config JSON 的序列化

`json.dumps(srv, ensure_ascii=False, separators=(',', ':'))`——紧凑格式、保留中文、避免空格干扰参数解析。

#### ⚠️ Worker 进程回收

MCP Server 由 Worker 子进程 spawn，Worker kill 时需确保 MCP 子进程也被递归回收（配合 `psutil` 方案）。

#### ⚠️ 请求体字段名

RuleWhisper HTTP API 接收 `{"text": "..."}`，不是 `{"raw": "..."}`。

#### ⚠️ 不同 plugin manifest 的同名冲突

后加载覆盖先加载（按 manifest name 去重）。

#### ⚠️ game_id 的传递链

```
QQ 消息 "短剑伤害"
  → Pan 未命中 command_routes → 走 LLM 路径
  → 从 session metadata 取 game_id
  → spawn Worker（通过 mcp_servers 注入 RuleWhisper MCP）
  → LLM 调 get_weapon(game_id="xxx", name="短剑")
  → RuleWhisper MCP 根据 game_id 读取规则版本和武器数据
```

`game_id` 不需要在 spawn Worker 时传入——它是 MCP tool 的参数，调 tool 时从 session 取并传入。

### 11.3 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `packages/core/manifest_loader.py` | 新建 | loader 主逻辑 |
| `config.json` / `config.example.json` | 改造 | 增加 plugin_manifests，移除硬编码 |
| `packages/core/adapters/cbc/adapter.py` | 改造 | mcp_args helper + build_spawn_args 注入 |
| `packages/core/adapters/kimi/adapter.py` | 改造 | 同上 |
| `packages/qq/plugin.py` | 改造 | 前缀路由 + session game_id |
| `packages/core/session.py`（或等效） | 改造 | session metadata 增加 game_id |

---

*创建：2026-07-22 · 最后更新：2026-07-22 · 状态：Phase 1 待实施*
