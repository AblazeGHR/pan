# 冷启动历史实测（2026-08）

以下保留历史观察、旧环境与旧命令，不是当前行为准则。当前工具契约见 [共用操作](../SKILL.md)，派发与等待以 [MA 行为准则](ma.md) 为准；历史中的轮询、自动删除与共享目录示例不构成当前授权。

## 10. 冷启动实测记录与待补充清单（D5）

> 立项 D5 要求同时验证 **HTTP 路径** 与 **MCP 路径**。下表 G1–G7 为 `docs/archive/冷启动Agent编排实测报告.md` 中 HTTP 路径实测结果；**MCP 路径实测（2026-08-17）见下方「MCP 路径实测」小节**，并据此更新 G2/G5、新增 G8–G12。

### 10.1 HTTP 路径实测缺口（原 D5）

| # | 缺口 | 建议补充位置 | 状态 |
|---|------|-------------|------|
| G1 | `POST /api/sessions`、`POST /api/spawn`、`POST /api/assign`、`DELETE /api/sessions/{id}` 的**请求体字段未记录**（原 §5 只说"见 server.py"）——纯 HTTP 冷启动在第 1 步就需读代码 | http-api.md 补核心端点 body 表 | ✅ |
| G2 | **MCP server 接线步骤缺失**：实测 ToolSearch 搜不到 `mcp__pan__` 工具时，手册无"如何启动/注入 MCP server 使工具可见"的动作序列 | §0.1/§5（MCP 路径实测） | ✅ §0.1/§5 |
| G3 | **workdir 相对基准未明确**：默认落点实测为 Pan server 数据根 `D:\project\pan-test\data\workdirs\<name>`，非当前项目目录 | §7.1 | ✅ §7.1 |
| G4 | **Windows curl 内联 UTF-8 JSON 报 body 解析错误**（实测 `{"detail":"There was an error parsing the body"}`）；应改用 `--data-binary @file` 或 urllib/requests | http-api.md | ✅ |
| G5 | `report_subscribe` 前置 `PAN_AGENT_SESSION_ID` 的**来源/注入方式未说明**（谁注入、何时有） | §3 | ✅ MCP 路径实测 |
| G6 | **字段映射未说明**：create 返回 `id`，spawn/assign 入参用 `sessionId`；`sessionId` vs `session_id` 命名不一致 | http-api.md | ✅ |
| G7 | 轮询**放弃/超时策略缺失**（多久、几次轮询算失败） | http-api.md 轮询兜底策略 | ✅ |

### 10.2 MCP 路径实测（2026-08-17，MA `mcp__pan__` 直连）

**实测链路（一次走通）**：
`session_create`(ses_d66c08611936941b) → `worker_assign`(queued/worker-2) → 轮询 `session_get`(limit=30) → `lastResult.status="done"`, `result="391"`（子 worker 算 `17×23`）→ `session_delete`(deleted)。
**第二次**覆盖 `worker_spawn` 显式路径：create → `worker_spawn`(idle) → assign(queued) → 轮询 → `143÷11=13` done → delete。参数名、状态判断、轮询兜底均充分，**MCP 路径主链路顺畅**。

**G2 / G5 处理结论（已覆盖，更新原描述）**：

- **G2（MCP 接线）已解决且写入 §0/§5**：`mcp__pan__` 工具由 Pan adapter 在 spawn worker 时**自动注入**——通过 adapter 生成的 `data/mcp-configs/<session_id>.mcp.json`（含 `{"mcpServers":{"pan":{"command":"...python","args":["-m","packages.mcp.server"],"cwd":"...","type":"stdio"}}}`），经 `--mcp-config` 显式传入，**直接 connected 可见，无需 ToolSearch/DeferExecuteTool**。`ToolSearch`+`DeferExecuteTool` **仅**用于项目级 `.mcp.json` 路径发现的工具（见 §7.7）。手册原 §0/§5 "MCP 工具是 deferred 的"表述对 `--mcp-config` 路径不准确，已修正。

- **G5（`PAN_AGENT_SESSION_ID` 来源）已查实（2026-08-17）**：该变量**由 Pan adapter 注入到 MCP server 进程环境**（写入上述 mcp-config 的 `env` 段：`PAN_AGENT_SESSION_ID=<manager session id>`、`PAN_AGENT_SESSION_TITLE=<title>`）。**来源**：manager（MA 自身）session 被创建并启用 MCP 时，adapter 生成 mcp-config 时填入；**何时**：MCP server 以 stdio 拉起的那一刻就带在环境里，对 MA 透明（其亲 shell `env` 里查不到这些变量，属正常）。

- **G5 关键约束（新发现，致命）**：`report_subscribe` 要求 **manager session 与被管 session 同处一个 Pan 服务实例**。本实测中 MCP server 默认连 **8768**（见 §7.7/§0），而 `PAN_AGENT_SESSION_ID=ses_8f7825d50d340dad` 实际只存在于 **8767**（pan-test）。该环境变量值指向 8767，与 MCP server 的默认目标 8768 **跨端口**，导致 §3 内部报告路径在本布局下失效。**对齐前提**：`--mcp-config` 的 `cwd`/启动端口、`PAN_API_URL`、**与 `PAN_AGENT_SESSION_ID` 所在服务**必须三者一致。

**MCP 路径新发现缺口（G8–G12；2026-08-27 核对：各项建议均已在主文对应位置补齐 ✅——G8 见 §5/§6、G9/G10 三对齐见 §0/§3、G11 见 §2.5/§7.1、G12 见 §7.1）**：

| # | 缺口 / 现象 | 后果 | 建议补充位置 |
|---|-------------|------|--------------|
| G8 | `session_list` **无过滤参数**，默认把全部 session 的**完整 history** 序列化返回（实测 310KB / 265906 chars，触发工具输出 token 上限溢出） | 冷启动第一步"先查后做"反而撑爆上下文；必须先 `Read` 落盘文件分段看 | §5/§6：改用 `session_get(limit=)` + `worker_list` 代替全量 `session_list` |
| G9 | **MCP server 默认端口（8768）与 `PAN_AGENT_SESSION_ID` 所在服务（8767）跨端口**（见 G5 约束） | `report_subscribe` 内部报告路径不可用 | §0/§3：明确"MCP 目标端口 = PAN_AGENT_SESSION_ID 所在端口"三对齐 |
| G10 | **`report_subscribe` 在本布局返回 `404 "Not Found"`**：实测 8768(main) 服务 `server.py` 无 `report-subscribe` 路由（0 次命中），而本工作树 `server.py` 有（1 次）——即**运行中的服务落后于 MCP 工具版本**（版本 skew） | §3 内部报告走不通；只能退回 http-api.md 的轮询兜底 | §3：标注"需运行含 `report-subscribe` 的服务端（本分支 server.py 已含）"；否则用轮询兜底 |
| G11 | `session_delete` **只 kill worker + 删 session 元数据，不删 workdir 磁盘目录**（实测删除后 `data/workdirs/mcp-cold-probe` 空目录残留） | 磁盘累积空目录，需另清理 | §2.5/§7.1：说明残留，通过 Data 孤立 workdir 候选手动清理 |
| G12 | workdir 默认基准 = **服务进程的数据根**，非固定 pan-test。本实测（8768 服务 cwd=`D:\project\Pan`）落点为 `D:\project\Pan\data/workdirs/<name>`，与 G3 记的 `D:\project\pan-test\...` 不同 | §7.1 的"Pan server 数据根"需指明是**实际运行实例的**数据根，可经 create 返回的 `workdir` 字段确证 | §7.1 |

**MCP 路径推荐操作顺序（据实测固化）**：
1. 确认 MCP server 目标端口 = `PAN_AGENT_SESSION_ID` 所在端口（否则 G9/G10 命中，退回 http-api.md 的轮询兜底）。
2. `session_create(name, adapter="cbc", model="hy3")` → 记下返回 `id`（= 后续 `session_id` 入参）。
3. `report_subscribe(session_id)`（§3；若可用）。
4. `agent_assign(session_id, text)` 立即返回 `queued`（自动 spawn，无需手动 `agent_spawn`；§2.1）。
5. 等 `queue_pending` 完成报告（兜底：轮询 `session_get(session_id, limit=15)` 直到 `lastResult.status=="done"`，≤30s 内完成，按 §6 不重发）。
6. 读 `lastResult.result`。
7. `session_delete(session_id)` 收尾（注意 §7.1 G11 workdir 残留）。
8. **避免**全量 `session_list`（G8）；状态巡检用 `agent_list` + 定向 `session_get`。

---

