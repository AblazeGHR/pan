# MCP 启用单一事实源收敛 — 立项

> 背景：`mcp_enabled`（布尔）是冗余的"纸面开关"，`mcp_servers` 才是真正决定 MCP 启用的字段。
> 状态：立项阶段（仅记录设计考量，**不改代码**）| 创建：2026-08-17
>
> **关联更新（2026-08-17）**：`mcp_mode`/`mcp_servers` 在 `Character概念分层重构立项.md` 中从 character 迁移到 `session_template`。本文「character → mcp_mode/mcp_servers」的现状描述，迁移后改为「session_template → mcp_mode/mcp_servers」，决定链语义不变。

---

## 一、背景与动机

Session 当前用三个字段决定 MCP 行为：

| 字段 | 实际作用 | 冗余度 |
|------|---------|--------|
| `mcp_servers` | **真正的事实源**：非空 → MCP 启用；空 → 无 MCP | — |
| `mcp_enabled` | 纸面开关：`true + servers=[]` 无效；`false + servers 非空` 也需 `_mcp_configured` 再判 | **冗余** |
| `output_mode` | 独立维度（stream 长驻 vs one-shot）| 不冗余 |

证据（2026-08-17 实测）：
- 普通 session `mcp_enabled=true`（config 默认）但 `mcp_servers=[]` → **实际无 MCP**（`_mcp_configured` 为 False）
- meta-agent `mcp_enabled=true` + `mcp_servers=["pan"]` → 真正启用
- 决定矩阵本质是 `mcp_servers` 非空与否 + `output_mode`，`mcp_enabled` 不提供额外信息

**目标**：删除 `mcp_enabled`，`mcp_servers` 非空 = 语义 enable，收敛为单一事实源。

## 二、现状决定链

```
创建：
  普通 session → mcp_enabled = data.mcpEnabled | MCP_DEFAULT_ENABLED（config mcp.enabled_default, true）
  character     → mcp_enabled = mcp_mode（always/never/optional）
                → mcp_servers = char.mcp_servers（非空才注入）
  PATCH         → _apply_mcp_enabled / _apply_mcp_servers

执行（_mcp_configured = mcp_enabled && mcp_servers 非空）：
  _use_oneshot_mcp：有 MCP + output_mode==oneshot → one-shot；其余 → stream+MCP
```

## 三、方案：删 `mcp_enabled`，servers 即 enable

**新决定链**：
```
创建：
  普通 session → mcp_servers = 空（无 MCP）
  character     → mcp_servers = char.mcp_servers（非空即启用）
  PATCH         → 注入/移除 mcp_servers（原 mcpEnabled 开关语义改为 servers 增删）

执行（_mcp_configured = mcp_servers 非空）：
  _use_oneshot_mcp：有 MCP + output_mode==oneshot → one-shot；其余 → stream+MCP（不变）
```

**决定矩阵简化为 2 因素**：

| `mcp_servers` | `output_mode` | 执行 |
|---------------|---------------|------|
| 空 | 任意 | stream 无 MCP |
| 非空 | 未设置 / `"stream"` | stream 长驻 + MCP |
| 非空 | `"oneshot"` | one-shot MCP |

## 四、兼容点处理

| 兼容点 | 处理 |
|--------|------|
| `mcp_mode=optional`（默认不启用可切换）| 切换 = 注入/移除 servers（默认不注入 → 无 MCP；开启时注入）|
| `mcp_mode=never` | 不注入 servers（现状已如此）|
| `mcp_mode=always` | 注入 servers（现状已如此）|
| API `mcpEnabled` 字段 | 变**只读派生值**（`len(mcp_servers)>0`），不再可写 |
| 前端 Settings 开关 | 由切换 mcpEnabled 改为切换 servers（注入/移除默认 pan server）|
| PATCH `mcpEnabled` | deprecated，改为 `mcpServers` 增删 |
| `config.json mcp.enabled_default` | 废弃（普通 session 无 servers 即无 MCP；如需默认开，改为默认注入 servers）|

## 五、影响面

| 模块 | 改动 |
|------|------|
| `packages/core/worker.py` | `_mcp_configured` 删 `mcp_enabled` 检查 |
| `packages/core/session.py` | 无（mcp_enabled 在 adapter_config，非 Session 字段）|
| `packages/web/server.py` | `_build_session_params`（普通 session 不再设 mcp_enabled）；`_apply_mcp_enabled` 删除/改造；`_session_to_api` mcpEnabled 派生 |
| `packages/mcp/server.py` | `session_create` 的 `mcp_enabled` 参数 deprecated |
| 前端（React + legacy）| Settings 开关改 servers 切换；mcpEnabled 显示改派生 |
| 测试 | 更新 mcp_enabled 相关断言 |

## 六、待决策

1. **PATCH 语义**：`mcpEnabled` 开关保留为"快捷方式"（内部转 servers 增删）还是直接废弃？
2. **默认行为**：普通 session 是否默认注入 pan server（让默认 mcp_enabled=true 的效果保留）？还是普通 session 一律无 MCP（更纯的单一事实源）？
3. **mcp_enabled 字段**：完全删除（Session 存储），还是保留但不再读取（兼容旧数据）？

## 七、任务拆解（若立项通过）

- [ ] `_mcp_configured` 只查 `mcp_servers` 非空
- [ ] `_build_session_params`：普通 session 不再设 mcp_enabled（或不注入 servers）
- [ ] `_apply_mcp_enabled` 改造/删除（PATCH 语义）
- [ ] `_session_to_api` mcpEnabled 派生
- [ ] manifest/`mcp_mode` 语义确认（optional 的切换）
- [ ] 前端 Settings + 测试同步
- [ ] 验证：e2e（普通/character/optional 三路径）

---

## 关联文档

- `docs/阶段计划与进度.md` — 遗留待办 L2（config.example mcp 段）
- `docs/archive/Profile权限字段与MetaAgent管理Session立项.md` — mcp_mode/manifest 语义
- `docs/plans&overviews/立项任务执行顺序规划.md` — 执行规划
