# Pan 冷启动 Agent 上手 Skill — 立项

> 背景：让一个没有对话上下文、只有 MCP 工具 + skill 的新 agent 快速掌握 Pan 编排（尽量用 MCP，灵活用 HTTP API）。
> 状态：立项阶段（仅记录考量，**不改代码**） | 创建：2026-08-16

---

## 一、背景与问题

2026-08-16 协调 5 个并行 worker 时，编排知识来自**会话积累 + 读代码**（`server.py` 路由、`mcp/server.py:_api`、踩坑记录、立项文档）。冷启动 agent（只有 MCP + skill）缺这些知识：

| 缺失知识 | 冷启动 agent 能否获得 |
|---------|---------------------|
| MCP 工具参数 | ✅ 工具自带 schema |
| **HTTP API 用法**（URL/格式/批量操作）| ❌ 不知道要看代码 |
| **编排工作流**（session→spawn→assign→盯梢→收尾）| ❌ 无文档 |
| **坑与约定**（workdir、mcp-config 收敛、watchdog、handoff 幂等、`////by agent` 前缀）| ❌ 不知道文档存在 |
| **盯梢模式**（worker 完成感知：/ws/agent 订阅 + monitor 脚本）| ❌ 无模板 |
| **MCP 局限**（请求-响应，不能主动推送 → 需配 WS/轮询）| ❌ 会用 MCP 但不知道要配 |

核心问题：**MCP 工具只回答"每个工具怎么调"，没回答"整套编排怎么做"**。

## 二、方案（三个，互补关系见下）

### A. 扩展现有 `pan` skill（`.codebuddy/skills/pan/SKILL.md`）为完整操作手册

SKILL.md 内容规划：

1. **编排工作流**：`session_create → worker_spawn → worker_assign/handoff → 盯梢 → 查结果 → 收尾`
2. **HTTP API 速查**：MCP 覆盖不到的端点（批量删除、PATCH 配置、session_list、轮询），含 URL/参数/返回
3. **坑与约定**（从踩坑记录/立项摘录）：
   - workdir 机制（默认 `data/workdirs/<name>`，绝对路径可指定 Pan 外目录）
   - mcp-config 收敛到 `data/mcp-configs/<session_id>.mcp.json`
   - watchdog 空闲回收（idle 超时 worker 被回收，重 spawn）
   - handoff/assign/send 语义 + taskId 幂等
   - `////by agent` 前缀（worker_send 自动加，用于区分 MA 编排消息）
4. **监督模板**：`monitor_worker.py`（WS 订阅 /ws/agent）用法 + Monitor 配置
5. **安全边界**：API 无鉴权、绑 loopback、workdir 允许范围

### B. MCP 工具 description 操作化

每个工具 description 补**调用链引导**：
- `worker_assign` 描述加"完成信号经 /ws/agent 的 `worker.result` 推送，或轮询 `session_get`"
- `session_create` 描述加"workdir 默认 data/workdirs/<name>；Pan 外目录用绝对路径"
- `worker_send` 描述加"会拼 `////by agent` 前缀（来源标记）"
- 每个工具 description 末尾指向 `/pan` skill（"完整编排流程见 pan skill"）

### C. 可选：`pan_handbook` MCP 工具

MCP 内返回操作手册全文，供"只认 MCP 习惯、不主动调 skill"的 agent 查询。
**实现方式：直接读 SKILL.md 文件返回**（单一事实源，不复制内容），SKILL.md 是唯一手册。

## 二·5. 最终定位：skill 是项目维护的重点内容

**skill（`.codebuddy/skills/pan/`）不是文档，而是随代码演进的活资产**，应作为一等维护对象：

| 维护项 | 要求 |
|--------|------|
| **单一事实源** | SKILL.md 是手册唯一源；`pan_handbook` MCP 工具读它返回，不维护第二份 |
| **附属脚本入库** | `monitor_worker.py` 等脚本随 skill 目录维护（`.codebuddy/skills/pan/scripts/`），随版本控制 |
| **内容与代码同步** | MCP 工具/API/workdir 约定变化时，skill 同步更新（列为 MCP 工具改动的必改项）|
| **触发词/命令封装** | skill 承载 slash 命令（如 `/pan`、`/pan-monitor`），脚本封装在 skill 内 |
| **验证** | 冷启动 agent 测试：仅 MCP+skill，无上下文完成一次编排 |
| **变更流程** | skill 变更走 git PR/提交，与代码变更同轨 |

## 三、方案互补关系

| 方案 | 定位 | 关系 |
|------|------|------|
| A（skill 手册）| 知识源/全景地图 + 脚本/命令载体 | ←→ B 互补 |
| B（description 引导）| 执行层路标（调工具时看参数+后续）| ←→ A 互补 |
| C（pan_handbook）| 手册的 MCP 出口（读 SKILL.md）| 与 A 不重叠（单一源），补可发现性 |

- **A+B 核心**；C 是 A 的 MCP 侧出口（读同一文件），零重复维护。
- **skill 是主载体**（含脚本/命令），MCP handbook 是辅助入口。
- 结论：**单一源（SKILL.md）+ skill/MCP 双出口**，skill 作为重点维护内容。

## 四、待决策

1. **C 做不做**：价值在可发现性，实现成本极低（读 SKILL.md）。建议做。
2. **skill 触发词**：`/pan`（编排手册）与 `/pan-monitor`（监督）是否合适。
3. **附属脚本位置**：`.codebuddy/skills/pan/scripts/` 还是 `packages/` 下由 skill 引用。
4. **B 的改动面**：改 MCP 工具 description 属代码改动，需同步测试/文档。

## 五、任务拆解（若立项通过）

- [ ] A：扩写 `.codebuddy/skills/pan/SKILL.md`（编排工作流 + API 速查 + 坑与约定 + 监督模板）
- [ ] A：**完成通知二选一说明**——skill 手册明确 `Monitor + /ws/agent`（外部协调）与 `report_subscribe`（meta-agent 内部）互斥，同用会重复通知，指导按场景选一
- [ ] A：监督脚本 `monitor_worker.py` 迁入 skill 目录并文档化
- [ ] B：逐工具补 description 调用链引导（`packages/mcp/server.py`），指向 `/pan` skill
- [ ] C：`pan_handbook` MCP 工具（读 SKILL.md 返回）
- [ ] 验证：冷启动 agent 测试（无上下文，仅 MCP+skill，完成一次编排）
- [ ] 文档更新（踩坑记录/Worker监督文档引用 skill）

---

## 关联文档

- `docs/plans&overviews/Worker监督与事件驱动模式.md` — 监督/盯梢实战与模板
- `docs/cbc-mcp-踩坑记录.md` — MCP 接入过程、/ws/agent 订阅协议
- `.codebuddy/skills/pan/SKILL.md` — 现有 pan skill（将扩展）
