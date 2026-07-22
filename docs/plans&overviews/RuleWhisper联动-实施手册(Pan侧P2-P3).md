# RuleWhisper 联动实施手册（Pan 侧 P2 / P3）

> 配套设计文档：`RuleWhisper联动与框架优化建议.md`（同目录）。本手册聚焦**落地步骤**，使用 RuleWhisper 已交付（P0/P1）的**真实接口**，供 Pan 侧按步执行。
> 关联（RuleWhisper 仓库）：`docs/Pan联动实施方案.md`。

---

## 〇、前置状态（来自 RuleWhisper 侧，已交付并推送）

| 能力 | 形态 | 访问方式 |
|------|------|----------|
| P0 HTTP API | FastAPI，端口 **9731** | `http://127.0.0.1:9731` |
| P1 MCP Server | fastmcp，6 个工具 | stdio（默认）或 SSE（端口 **9733**） |

**MCP 启动命令（从 RuleWhisper 仓库根目录运行）**：

```bash
# stdio（推荐，供 cbc/kimi 作为子进程 spawn）
python -m src.server.mcp

# SSE（若 Worker 走 HTTP MCP）
python -m src.server.mcp --transport sse --port 9733
```

> ⚠️ 与早期提案不同：模块路径是 `src.server.mcp`（不是 `rulewhisper.server.mcp`），stdio 模式**不需要 `--port`**。RuleWhisper 仓库根须加入 `PYTHONPATH` 或在该目录下执行。

**MCP 工具清单（6 个）**：`query_rule`、`roll_dice`、`get_weapon`、`get_monster`、`get_spell`、`get_skill`。

**HTTP 端点清单**：
`GET /api/health`、`POST /api/query`、`POST /api/dice`、`GET /api/weapon|monster|spell|skill/{name}`、`GET /api/rule/{page}`。

---

## 一、P2：MCP 透传（Pan 侧改动）

目标：让 Pan 管理的 cbc/kimi Worker 能调用 RuleWhisper 的 MCP 工具。RuleWhisper 代码零改动。

### 1.1 `config`（cbc / kimi adapter 或全局）

```jsonc
{
  "cbc": {
    "model": "deepseek-v4-flash",
    "permission_mode": "bypassPermissions",
    "mcp_servers": [
      {
        "name": "rulewhisper",
        "command": "python",
        "args": ["-m", "src.server.mcp"],
        "cwd": "/abs/path/to/RuleWhisper",
        "env": { "PYTHONPATH": "/abs/path/to/RuleWhisper" }
      }
    ]
  }
}
```

- `cwd` / `env.PYTHONPATH`：确保 `python -m src.server.mcp` 能在 RuleWhisper 仓库根解析到 `src` 包。
- 全局 fallback：也可放顶级 `mcp_servers`，per-adapter 为空时继承。

### 1.2 adapter 注入 `--mcp-config`

参照现有 `model_args` / `permission_mode_args` 模式，新增 helper：

```python
def _mcp_args(self, servers: list[dict]) -> list[str]:
    args = []
    for srv in servers:
        args.extend(["--mcp-config", json.dumps(srv, ensure_ascii=False)])
    return args
```

在 `build_spawn_args` 中 `args.extend(self._mcp_args(s.mcp_servers))`。（详见提案文档「二、改一动议：MCP 透传」2.3 改动点 2–4。）

### 1.3 Worker Profile（coc-keeper）

```jsonc
{
  "profiles": {
    "coc-keeper": {
      "adapter": "cbc",
      "model": "deepseek-v4-pro",
      "permission_mode": "bypassPermissions",
      "mcp_servers": ["rulewhisper"],
      "system_prompt": [
        "你是 COC 守秘人(KP)，用中文回复。",
        "所有规则查询和骰子检定都通过 RuleWhisper 工具进行，绝不自己编数据。",
        "检定结果需展示公式与最终值。"
      ]
    }
  }
}
```

`SpawnRequest` 增加 `profile` 参数，Core 解析后合并默认值并注入 MCP。

### 1.4 安全

- MCP Server 随 Worker 子进程生命周期启动/回收（需验证 Pan 进程管理会递归 kill 子进程树）。
- `config.json` 保持 gitignored，仅受信用户配置；非信任 MCP Server 是指令注入入口。

### 1.5 P2 验收

- [ ] 启动一个 `coc-keeper` profile 的 cbc Worker，日志可见 `src.server.mcp` 子进程拉起。
- [ ] 在 Worker 对话中要求「查短剑属性」「掷 .rc 侦察 60」，Worker 调用 `get_weapon` / `roll_dice` 并返回真实数据（非编造）。

---

## 二、P3：联调（QQ 群内全链路）

目标：群内消息按前缀路由到 RuleWhisper 确定性接口，自然语言走 LLM（自动用 RuleWhisper MCP）。

### 2.1 QQ 命令路由表（配置 `config.json` 的 `qq.command_routes`）

| 前缀 | 目标 | 说明 |
|------|------|------|
| `.rc` `.ra` `.rb` `.rp` `.rs` `.sc` `.dam` | `POST http://127.0.0.1:9731/api/dice` | 骰令，毫秒级，不走 LLM |
| `.coc` `.rule` | `POST http://127.0.0.1:9731/api/query` | 规则/数据库查询 |
| （无前缀 / 其他） | 现有 `_send_and_wait` LLM 路径 | 自然语言，Worker 经 MCP 调 RuleWhisper |

请求体建议：`{"text": "<原始消息去掉前缀后的内容>"}`（RuleWhisper 的 `/api/dice`、`/api/query` 接收 `{"text": "..."}`）。

> 注：提案文档示例用 `{"raw": text}`，而 RuleWhisper 实际接收字段是 `text`。以本手册为准。

### 2.2 plugin.py 改动要点

在 `handle_message` 开头插入前缀匹配：命中 → `POST` 对应端点，取返回文本回复，不创建 Worker；未命中 → 现有 LLM 路径。（详见提案文档「三、改动二：QQ Bot 命令路由层」。）

### 2.3 冒烟测试清单

**确定性链路**
- [ ] 群内发 `.rc 1d100 侦察检定` → 毫秒级收到形如 `[60/60] 常规成功！` 的结果（来自 RuleWhisper，非 LLM）。
- [ ] 群内发 `.coc 短剑` → 收到武器/规则数据。
- [ ] 群内发 `.dam 1d6` → 收到伤害掷骰结果。

**自然语言链路**
- [ ] 群内问「短剑的伤害是多少？如果恐怖猎手用短剑捅我，我要怎么闪避？」→ LLM 调用 `get_weapon` / `get_monster` / `query_rule` 后综合回复（可在 Worker 日志确认 tool call）。

**回归**
- [ ] 联调期间 `GET http://127.0.0.1:9731/api/health` 持续返回 `{"status":"ok"}`。

### 2.4 P3 验收

- [ ] 确定性指令 0 LLM token 消耗、延迟 < 50ms。
- [ ] 自然语言问答引用的是 RuleWhisper 真实数据，无编造。
- [ ] 群级 session 绑定（提案「四、群级 Session」）下多人 at 同一 KP Worker，状态共享。

---

## 三、关联文档

| 文档 | 位置 | 用途 |
|------|------|------|
| RuleWhisper 联动 & 框架优化建议 | Pan `docs/plans&overviews/`（本目录） | 设计提案与改动点详述 |
| Pan 联动实施方案 | RuleWhisper `docs/Pan联动实施方案.md` | RuleWhisper 侧计划与进度 |
| PLAN | RuleWhisper `docs/PLAN.md` | 整体路线图（P6 已改为联动 Pan） |

---

*创建：2026-07-22 · 适用：Pan 侧执行 P2（MCP 透传）+ P3（联调）*
