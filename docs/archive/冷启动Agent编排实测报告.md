# 冷启动 Agent 编排实测报告（D5）

> 对应立项：`docs/plans&overviews/Pan冷启动Agent编排skill立项.md` D5
> 分支：`test/coldstart`（基于 pan-test）｜日期：2026-08-17
> 验证目标：一个只有 MCP + skill（无对话上下文）的 agent，能否仅凭 `.codebuddy/skills/pan/SKILL.md` + MCP 工具完成一次完整 Pan 编排。

## 1. 实测环境

- Pan server：`http://127.0.0.1:8767`（本分支默认端口，已确认 `/api/adapters` 正常返回）
- MCP pan 工具：**本会话 ToolSearch 搜不到任何 `mcp__pan__` 工具**（精确名与关键词都试过，返回无关工具）
- 执行方式：按需求用 Pan HTTP API 直调（curl 中文 payload 失败，改用 E 盘 python urllib）

## 2. 实测步骤与结果

只依据 SKILL.md 提供的信息（流程、MCP 参数名、§5 HTTP 速查表），推断 HTTP 请求体执行：

| # | 步骤 | 依据（SKILL.md） | 执行 | 结果 |
|---|------|-----------------|------|------|
| 1 | 创建 session | §2.1 `session_create(name, adapter, model)` | `POST /api/sessions {"name":"coldstart-probe-1","adapter":"cbc","model":"hy3"}` | ✅ `ses_f400d59534fb87e3` |
| 2 | spawn worker | §7 `worker_spawn(session_id,...)` | `POST /api/spawn {"sessionId":...}` | ✅ `worker-2` idle |
| 3 | 指派任务 | §7 `worker_assign(session_id,text)` | `POST /api/assign {"sessionId":...,"text":"请计算 17 x 23..."}` | ✅ `{"status":"queued"}` |
| 4 | 等结果 | §8 状态判断表 / §5 轮询模式 | 等 8s → `GET /api/sessions/{id}` | ✅ `lastResult.status=done`，答案 **391**（17×23，正确） |
| 5 | 删除 session | §2.5 `session_delete` | `DELETE /api/sessions/{id}` | ✅ `{"status":"deleted"}`，已清理无残留 |

**主链路一次走通**，worker 执行结果正确。说明 SKILL.md 对"流程编排"的叙述是充分的：顺序、参数名、状态判断、轮询兜底都够用。

## 3. 卡点记录

### 卡点 1：HTTP 请求体无文档（未真正卡死，但靠推断）

§5 明说"其余端点 HTTP 形态见 `packages/web/server.py`"，冷启动 agent 按"只读 SKILL.md"的纪律在第 1 步就应该卡住。本次是靠 MCP 工具参数名（§7）推断出 body 字段才走通——**但参数名→HTTP body 的映射本身不是 SKILL.md 提供的信息**。属信息缺口（见待补充清单 G1、G6）。

### 卡点 2：Windows curl 内联 UTF-8 JSON 解析失败

`curl -d '{"text":"请计算 17 × 23..."}'` 直接返回 `{"detail":"There was an error parsing the body"}`。改用 `--data-binary @file` 或 python urllib 后成功——纯编码/命令行问题，非 API 问题。SKILL.md 未提示（G4）。

### 卡点 3：MCP 工具在当前会话不可用

SKILL.md §0/§7 说用 ToolSearch 发现 `mcp__pan__` 工具，但实测本会话搜不到。§9.7 只给了"搜不到=未连接"的判定，**没有给接线步骤**（如何启动 MCP server / 如何注入 `--mcp-config` 让工具可见）。冷启动 agent 若真只有 MCP 一条路，在此会卡死（G2）。

### 卡点 4：workdir 落点与直觉不符（非阻塞）

默认 workdir 实测落在 `D:\project\pan-test\data\workdirs\coldstart-probe-1`（Pan server 数据根），不是当前项目目录。SKILL.md §9.1 说"默认 `data/workdirs/<name>`"，但未指明相对基准（G3）。本测试未用 workdir 写文件，故不阻塞。

## 4. 冷启动可行性结论

**基本可行（带前置条件）**：

1. **MCP 工具可用时**：SKILL.md 已足以完成 create → spawn → assign → 盯梢 → 查结果 → 清理 全链路。§2 流程、§7 参数、§8 状态、§5 轮询兜底、§2.5 清理——覆盖完整，无流程性缺口。
2. **MCP 工具不可用 / 走 HTTP 直调时**：SKILL.md 的 HTTP 信息不完整，第 1 步 create 的请求体就需要去读 `server.py`，违背"只读 SKILL.md"的冷启动前提。需补 §5 核心端点 body。
3. **冷启动 agent 的第一反应链**：读手册 → 发现 MCP 工具 → 若搜不到 → 需要接线指引。当前手册缺"搜不到时怎么办"的动作序列。

## 5. SKILL.md 待补充清单（G1–G7）

见 SKILL.md 新增 §12 清单（与本文档同步）。摘要：

- G1：§5 补 `POST /api/sessions`、`POST /api/spawn`、`POST /api/assign`、`DELETE /api/sessions/{id}` 的请求体字段表
- G2：新增"MCP server 接线"小节（启动命令、`--mcp-config` 注入、工具可见性验证）
- G3：§9.1 明确 workdir 相对基准（Pan server 数据根）
- G4：§9 补 Windows curl 内联 UTF-8 的坑
- G5：§3.2 补 `PAN_AGENT_SESSION_ID` 的来源与注入时机
- G6：补 create 返回 `id` vs 入参 `sessionId` 的字段映射
- G7：补轮询超时/放弃策略建议
