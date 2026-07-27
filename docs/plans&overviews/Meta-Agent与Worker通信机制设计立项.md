# Meta-Agent 与 Worker 通信机制 — 设计立项

> 基于当前方案讨论中发现的三项设计空白，立项调研。
> 状态：立项阶段 | 创建：2026-07-22

---

## 一、立项背景

在梳理 `目标与范围.md` 和 `重构方案评估_by_k3.md` 后，发现当前方案在三个关键问题上存在空白：

| 问题 | 当前状态 | 风险 |
|------|---------|------|
| **Worker 之间如何互相交流** | 方案中完全未涉及。当前是纯星型拓扑。 | 无法实现 Worker 协作、链式工作流 |
| **Meta-Agent 如何下达任务和管理** | 只有 `/ws/agent` 端点（`task`/`spawn`/`kill` 三个命令），缺乏 Agent 视图、状态反馈、超时/重试策略 | Meta-Agent 无法有效调度多 Worker |
| **Meta-Agent 如何理解 Pan 的使用方法** | 方案中完全未涉及。没有 system prompt 设计、API 工具定义、使用示例 | Meta-Agent 只是一个空壳 WebSocket 端点 |

本立项旨在通过外部调研，为这三个问题寻求参考方案。

---

## 二、核心研究问题

### Q1: Worker 间通信

> 在 Pan 的星型架构（Worker → Core → Channel）下，Worker 之间如何合理通信？
> 是走 Core 中转还是 Peer-to-Peer？是否需要引入消息队列？

**子问题**：
1. 同步 vs 异步通信：Worker A 需要等 Worker B 的结果时，协议层如何处理？
2. 消息路由：Worker 如何知道"给谁发消息"？是否需要一个 Worker 注册/发现机制？
3. 上下文传递：Worker A 的输出如何作为 Worker B 的输入？是否需要结构化中间格式？
4. 链式执行：Worker A 完成 → Worker B 启动，这个编排逻辑放在哪一层（Core？Meta-Agent？）？

### Q2: Meta-Agent 任务下达与管理

> Meta-Agent 作为"用 Agent 管 Agent"的调度者，需要怎样的协议和支持才能有效工作？

**子问题**：
1. **Agent 视图设计**：Meta-Agent 不能直接消费 Worker 原始输出流（context 会爆炸）。它需要什么样的"摘要视图"？
2. **任务分配原语**：只 spawn + task 两个操作是否足够？是否需要 handoff（同步等待）、assign（异步分派）、broadcast（广播）等更多原语？
3. **状态反馈**：Worker 何时"完成"？Meta-Agent 如何知道结果是成功/失败/超时？
4. **生命周期管理**：Worker 是短生命（一次任务即销毁）还是长驻？何时回收？
5. **故障处理**：Worker 崩溃、超时、无响应时，Meta-Agent 应如何感知和应对？

### Q3: Meta-Agent 理解 Pan 的使用方法

> 如何让一个 LLM 正确理解并能操作 Pan 的 API？它需要知道什么信息？

**子问题**：
1. **工具定义格式**：Pan 的 API（spawn worker、send task、kill worker、查看状态）如何以 LLM 可理解的格式描述？
2. **System Prompt 设计**：Meta-Agent 的 system prompt 应包含什么？工作流程指南？约束条件？使用示例？
3. **能力发现机制**：Meta-Agent 是静态知道所有能力，还是能动态发现新的 Worker 类型/能力？
4. **Skill/Playbook 注入**：是否需要类似 CAO 的 supervisor-protocols 机制，将"如何正确使用 Pan"封装为可注入的 skill？

---

## 三、同类产品全景扫描

调研覆盖了 **15+ 个 CLI Agent Orchestrator 项目**，按编排模式分类：

| 编排模式 | 代表项目 | 有无 Supervisor | Agent 间通信 |
|----------|---------|----------------|-------------|
| **Supervisor-Worker** | CAO, Bernstein, agent-orchestrator, Anthropic Platform, Edict | 有 | 有（各不相同） |
| **Hub-and-Spoke 协议通信** | CCB (Claude Code Bridge) | 有（daemon-per-provider） | 有（协议标记） |
| **Meta-Harness** | Ruflo | 有（Hook 系统） | 有（MCP + Hook） |
| **无 Supervisor** | Claude Squad, Emdash, Crystal, Baton, dmux, amux 等 | 无 | 无 |

关键发现：**大多数同类产品没有 Agent 间通信**（纯 Human-in-the-loop 并行调度）。只有少数项目（CAO、Bernstein、CCB、Edict）实现了任何形式的 Agent 间协调，各有特色。

---

## 四、参考资料深度分析

### 4.1 AWS CLI Agent Orchestrator (CAO) — Worker 通信 + Skill 注入 的完整参考

- **GitHub**: https://github.com/awslabs/cli-agent-orchestrator
- **匹配度**: 极高 — 与 Pan 同类型产品（Supervisor + CLI Worker）
- **Stars**: 活跃开发中

| 项目 | 链接 | 匹配度 |
|------|------|--------|
| **CAO** | https://github.com/awslabs/cli-agent-orchestrator | 极高 — 与 Pan 同类型产品 |

CAO 与 Pan 的核心概念高度相似：Supervisor Agent（对应 Pan 的 Meta-Agent）管理多个 Worker Agent（对应 Pan 的 Worker），每个 Worker 是真实的 CLI 进程（Claude Code / Kiro / Codex 等），运行在独立 tmux session 中。

**CAO 对三个问题的解决方案**：

#### 对 Q1（Worker 通信）的答案

CAO 使用 **通过本地 HTTP 服务器中转的消息机制**：

```
Worker A ──send_message(receiver_id=B)──→ cao-server ──→ Worker B 的收件箱
Worker A ──send_message()────────────────→ cao-server ──→ 分派方（Supervisor）
```

关键设计：
- 每个 Worker 有全局唯一的 `CAO_TERMINAL_ID`
- `send_message` 是 Worker 间通信的唯一原语
- 省略 `receiver_id` 时自动路由到创建它的终端
- 消息投递到目标终端的**收件箱**，在终端空闲时处理
- 支持 **eager delivery**（急切投递），在 agent 处理中也能投递

#### 对 Q2（Meta-Agent 任务管理）的答案

CAO 提供了**三种编排原语**：

| 原语 | 行为 | 适用场景 |
|------|------|---------|
| `handoff(profile, message)` | 同步阻塞，等待 Worker 完成并返回结果 | 串行依赖步骤 |
| `assign(profile, message)` | 异步分派，立即返回，Worker 完成后回调 | 并行 fan-out |
| `send_message(message, receiver_id)` | 向已有 Worker 发送消息 | 持续性多轮协作 |

配合 `idle-based delivery` 模式：Supervisor 分派完所有任务后 end turn，空闲时自动接收 Worker 回调，避免空转等待。

Worker 状态：`IDLE` / `PROCESSING` / `COMPLETED` / `ERROR`。

#### 对 Q3（理解 Pan 使用方法）的答案

CAO 使用 **Skill 注入机制**：

- **`cao-supervisor-protocols`**：注入到 Supervisor 的 skill，教它如何使用 `assign`/`handoff`/`send_message`、何时等待 vs 何时并行、如何合并 Worker 结果
- **`cao-worker-protocols`**：注入到每个 Worker 的 skill，教它如何接收任务、回报结果、使用 `send_message`

Skill 按 provider 的原生机制投递：
- Kiro CLI → `skill://` 资源
- Claude Code / Codex → 运行时 prompt 注入
- Copilot → baked-in `.agent.md`

Skill 内容本质是 **Markdown 格式的操作手册 + 约束规则**，例如 Supervisor Protocol 中的关键规则：

> "Dispatch all planned worker tasks first. Finish the turn after dispatching work. Do not poll manually in a loop. A busy terminal delays inbox delivery."

---

#### CAO 的核心设计（对 Pan 的 Q1/Q2/Q3 全覆盖）

| 问题 | CAO 的做法 |
|------|-----------|
| **Worker 通信** | `send_message(receiver_id)` 通过本地 HTTP（cao-server）中转，消息进入目标 Worker 的收件箱，在空闲时投递 |
| **任务管理** | 三个原语：`handoff`（同步阻塞） / `assign`（异步分派 + 回调） / `send_message`（直接消息） |
| **理解 Pan** | Skill 注入：`cao-supervisor-protocols` 教 Supervisor 如何使用编排能力，`cao-worker-protocols` 教 Worker 如何回传结果 |

---

### 4.2 Bernstein — 确定性调度 + 任务图 + 质量门控

- **GitHub**: https://github.com/sipyourdrink-ltd/bernstein
- **匹配度**: 高 — 作为 CLI Agent Orchestrator，调度模型值得借鉴
- **订阅量**: 2000+

**核心架构特征**：
- **调度决策是确定性逻辑**，而非 LLM 驱动。使用 `profile_hash`（SHA-256 内容寻址）进行能力匹配路由
- **任务图**：Goal → LLM Planner（仅运行一次） → Task Graph → Orchestrator（Python 代码调度，零 token 开销） → Parallel Agents → Janitor（质量验证） → Git Merge
- **Agent 通信**：通过本地 HTTP 任务服务器（agents 报告进度）
- **状态管理**：WAL + Lineage（签名血统） + HMAC 审计链 + 事件日志
- **文件隔离**：Git worktrees + 沙箱池（process/container/vm）
- **40+ CLI Agent 支持**：通过 `AdapterCapabilityProfile` 声明能力（protocol、execution、input、lifecycle、invocation 等维度）

**对 Pan 的关键启示**：
1. **Janitor 质量门控模式**：增加一个不消耗 LLM token 的确定性验证层（lint/type check/test），在合并 Worker 结果前做质量把关
2. **profile_hash 驱动的能力匹配**：Worker 的能力可以提前声明，调度时做哈希匹配而非 LLM 猜测

---

### 4.3 CCB (Claude Code Bridge) — Daemon-per-Provider + 协议通信

- **GitHub**: https://github.com/bfly123/claude_code_bridge
- **匹配度**: 高 — 唯一拥有完善 Agent 间通信协议的 CLI Orchestrator

**核心架构特征**：
- **daemon-per-provider 架构**：每个 AI provider（Claude/Codex/Gemini/Grok/Kimi/Qwen/Cursor/Copilot）有独立守护进程，通过 `ccbd` 后台守护进程保持状态
- **协议标记通信**：`CCB_REQ_ID` / `CCB_DONE` 标记实现 Agent 间协调（超低 Token 开销：50-200 tokens/call）
- **`/ask` 命令系统**：Agent 可以调用 `/ask reviewer review the latest parser changes` 进行跨 Agent 任务委派
- **共享内存**：`.ccb/ccb_memory.md` 作为项目级共享记忆文档，包含团队协作规则、长生命周期上下文、Agent 交接约定
- **通信拓扑**：支持链式（A→B→C）、汇聚（A,B→C）、分发（A→B,C）三种协作图
- **Agent Roles Spec**：专业 Agent 打包为可安装的角色包（Agent Roles）
- **Provider 通信日志**：`~/.ccb/logs/comm.log` 记录所有跨 Agent 通信事件

**对 Pan 的关键启示**：
1. **协议标记通信是最轻量的 Agent 间通信方式** — 在消息中注入 `PAN_REQ_ID` / `PAN_DONE` 标记，无需额外通信栈
2. **项目级共享记忆文件**（`.ccb/ccb_memory.md`）是 Worker 间上下文共享的简单但有效的方案
3. **daemon-per-provider 模式**值得关注：每个 CLI Adapter 可通过独立守护进程管理

---

### 4.4 Edict（三省六部制）— Review-Gate 审核层 + 通信权限矩阵

- **GitHub**: https://github.com/cft0808/edict
- **匹配度**: 中高 — 审核机制极具创新性，但面向的是 Agent 角色链而非 CLI Worker

**核心架构特征**：
- **12 个 Agent 角色**：太子（分拣） → 中书省（规划） → 门下省（**审核+封驳**） → 尚书省（调度） → 六部（执行） → 奏折（回报）
- **门下省 Review-Gate**：每个任务规划必须经过审核，不合格的**硬性驳回**（封驳），打回中书省重做，直到通过才放行
- **通信权限矩阵**：不是谁都能给谁发消息，有严格的 12×12 矩阵约束
- **状态机保护**：`_VALID_TRANSITIONS` 硬性拒绝非法状态跳转
- **通信底层**：Redis Streams 事件总线 + Outbox Relay 可靠投递

**对 Pan 的关键启示**：
1. **Review-Gate 是可选的增强模式** — 在 Worker 执行完毕后，增加一个"审核 Worker"检查产出质量，不合格则驳回重做
2. **通信权限矩阵** — 在 Pan 中可以通过 Worker Role 来实现"受限制通道不可 spawn、不可访问工作目录外"

---

### 4.5 Anthropic Claude Platform — 平台级 Multi-Agent 编排

- **文档**: https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration
- **匹配度**: 高 — 平台级产品，但设计理念可直接借鉴

**核心架构特征**：
- **Coordinator（协调者）** 声明 `multiagent.agents` 名单（最多 20 个），通过 `agent_toolset_20260401` 工具进行委派
- **Thread 模型**：每个 Agent 有独立的 Session Thread（上下文隔离的事件流），主 Thread 显示所有活动的摘要
- **三种委派模式**：Parallelization（并行 fan-out）、Specialization（按领域路由）、Escalation（复杂任务升级到更强模型）
- **上下文隔离**：Agent 间的 tools、MCP servers、context 完全隔离不共享
- **权限路由**：Sub-Agent 的工具确认请求自动转发到主 Thread，由统一确认
- **`self` 模式**：Coordinator 可以 spawn 自身副本（继承配置覆盖），支持 fork-join 模式

**对 Pan 的关键启示**：
1. **Thread 模型** + 主 Thread 只显示摘要事件，副 Thread 保留完整细节 — 这解决了 Meta-Agent context 爆炸问题
2. **Escalation 模式**（简单任务用小模型 Worker，复杂任务升级到大模型）—— 成本优化策略

---

### 4.6 Ruflo — Meta-Harness + Hook 系统

- **GitHub**: https://github.com/ruvnet/ruflo
- **匹配度**: 中 — 定位是 Claude Code 之上的 Meta-Harness

**核心架构特征**：
- **Hook 系统**：通过 `hooks.json` 定义 PreToolUse、PostCommand、SessionStart、UserPromptSubmit 等 hook，由 `node -e bootstrap` 跨平台执行
- **66+ Agent 可执行**，AgentDB 提供 8 个控制器 + 6 个 MCP 工具
- **优先级覆盖链**：`env > enterprise policy > user config > project config > defaults`
- **Witness 验证系统**：Ed25519 签名的安全清单，防止 drift

**对 Pan 的关键启示**：
- **Hook 作为编排层**：不改变 CLI Agent 的行为，而是在关键节点（PreToolUse、PostCommand）注入编排逻辑
- **优先级覆盖链**：多层级配置管理的参考模型

---

### 4.7 A2A 协议 — Open Standard for Agent Communication

- **规范**: https://a2a-protocol.org/latest/specification/
- **匹配度**: 中 — 标准化方向参考

**Agent Card 模型**：
```json
{
  "name": "Recipe Agent",
  "description": "...",
  "capabilities": { "streaming": true, "pushNotifications": false },
  "skills": [
    {"id": "recipe_search", "description": "...", "tags": ["cooking", "search"], "examples": ["Find a pasta recipe"]}
  ],
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain", "application/json"]
}
```

**Task 状态机**：SUBMITTED → WORKING → COMPLETED / FAILED / CANCELED（与 CAO 一致）

**对 Pan 的启示**：
- Agent Card 模型可直接借鉴为 Worker Profile 的数据结构
- `skills[].tags` 可被 Meta-Agent 用来做语义路由

---

### 4.8 15+ 个同类项目全景对比

来源：[AugmentCode - 9 Open-Source Agent Orchestrators](https://www.augmentcode.com/tools/open-source-agent-orchestrators) +
[awesome-ai-orchestration](https://github.com/LeoLin990405/awesome-ai-orchestration)

**关键发现：大多数同类项目没有 Agent 间通信，没有 Supervisor**

| 项目 | Supervisor? | Agent 间通信 | 编排方式 | 对 Pan 参考价值 |
|------|------------|-------------|---------|---------------|
| **CAO** | ✅ Supervisor | ✅ send_message | handoff/assign/send | 极高（最完整参考） |
| **Bernstein** | ✅ Orchestrator+Janitor | ✅ HTTP 任务服务器 | 确定性调度+任务图 | 高（确定性调度+Janitor） |
| **CCB** | ✅ daemon-per-provider | ✅ CCB_REQ_ID/DONE | /ask + 协议标记 | 高（协议通信最完整） |
| **Edict** | ✅ 三省六部层级 | ✅ 权限矩阵+Redis Streams | 角色链+Review-Gate | 中高（审核机制创新） |
| **Anthropic Platform** | ✅ Coordinator | ✅ agent_toolset | Parallel/Specialize/Escalate | 高（Thread模型） |
| **Ruflo** | ✅ Meta-Harness | ✅ MCP+Hook | Hook 注入 | 中（Hook 模式） |
| **Composio AO** | ✅ Lifecycle Manager | 间接 | 里程碑门控 | 中 |
| **Claude Squad** | ❌ | ❌ | 人工并行调度 | 低（无通信机制） |
| **Emdash** | ❌ | ❌ | 人工并行调度 | 低 |
| **Crystal** | ❌ | ❌ | Git worktree 并行 | 低 |
| **Baton** | ❌ | ❌ | Poll-Dispatch-Reconcile | 低 |
| **dmux** | ❌ | ❌ | tmux 并行管理 | 低 |
| **agent-deck** | ❌ | ❌ | 会话管理 | 低 |

**结论**：Pan 在 Worker 通信 + Meta-Agent 维度上有明确的差异化空间 — 大多数同类产品根本没有这些能力。

---

### 4.9 三种多 Agent 编排模式的深度对比

来源：[Multi-Agent Orchestration Patterns](https://qubittool.com/zh/blog/multi-agent-orchestration-patterns)

| 维度 | Supervisor（星型） | Swarm（网状 Handoff） | Hierarchical（树状） |
|------|-------------------|----------------------|---------------------|
| **控制流** | 中心化路由 | Agent 自主移交 | 分层委派 |
| **通信方式** | 所有消息经 Supervisor 中转 | Agent 间直接 handoff | 仅相邻层级通信 |
| **决策方式** | Supervisor LLM 判断 | 每个 Agent 自行判断 | 上层分解、中层分配 |
| **延迟** | 每步一跳额外延迟 | 低（直接 Agent-to-Agent） | 多级累计延迟 |
| **可追踪性** | 高（中心节点记录一切） | 低（需额外 Trace 注入） | 中（按层级追踪） |
| **Agent 数量** | 适合 3-8 个 | 适合 2-15 个 | 适合 10-50+ 个 |
| **是否适合 Pan** | ✅ 主要模式 | ⚠️ 可选增强 | ⚠️ 过度设计 |

Pan 的核心场景是 **Supervisor 模式**（Meta-Agent 作为中心调度的星型拓扑），但 Worker 间的直接通信可以借鉴 Swarm 的 lightweight handoff 概念（通过 Pan Core 中转，而非真正的 P2P）。

---

### 4.10 Tool/Skill 注入 — 如何让 Agent 理解平台能力

三种主流做法对比：

| 做法 | 代表 | 格式 | 优势 | 劣势 |
|------|------|------|------|------|
| **Markdown Skill 注入** | CAO | `.md` 文件 | 人类可读、易于迭代、LLM 友好 | 非结构化，依赖 LLM 语义理解 |
| **OpenAI Function Calling Schema** | CodeBuddy Sub-Agent | JSON Schema | 结构化、确定性路由、精确参数校验 | 每个 API 需手写 schema |
| **Agent Card 自描述** | A2A Protocol | JSON | 标准化、支持动态发现、能力版本化 | 较重，对简单场景过度设计 |

**对 Pan 的建议**：**Skill 注入 + Tool Schema 双管齐下**（详见第五章）

---

## 五、综合建议方案

基于对 15+ 个同类产品的调研，三条建议的对标选择如下：

### ��议 1：Worker 间通信 — 参考 CCB 的协议标��� + CAO 的收件箱

**首选参考：CCB (Claude Code Bridge)**

CCB 的做法最轻量、最接近 Pan 的场景：

```
Worker A ── 消息（含 PAN_REQ_ID）──→ Pan Core ──→ Worker B 的 stdin 管道
Worker A ── send_message() ──────────→ Pan Core ──→ 父 Worker 的收件箱
```

**不选 CAO 的原因**：CAO 需要每个 Worker ��� Agent 主动调用 `send_message` MCP 工具，这在 Pan 的 Worker 是通用 CLI 进���的架构下增加了适配复杂度。

**方案要点**：
- Core 维护 `worker_inbox: dict[str, asyncio.Queue]`（内存收件箱）
- 协议标记：`PAN_TO:{worker_id}` / `PAN_FROM:{worker_id}` / `PAN_DONE` — 超低 token 开销
- 默认路由：Worker 无显式目标时，消息自动发给创建它的 Worker（或 Meta-Agent）
- 共享记忆文件：`.pan/pan_memory.md`（参考 CCB 的 `.ccb/ccb_memory.md`）

### 建议 2：Meta-Agent 任务管理 — 参考 CAO 的三原语 + Anthropic 的 Thread 模型

**首选参考：CAO 的编排原语 + Anthropic Platform 的 Thread 设计**

```
无 Supervisor 的项目（15 个中的 10+ 个）只做人工并行调度
                                   ↓
              Pan 不应该走这条路——Meta-Agent 是核心差异
                                   ↓
参考 CAO（最完整的 CLI Orchestrator）+ Anthropic（平台级最佳实践）
```

**方案要点**：
- 三原语：`handoff`（同步等待+自动回收）、`assign`（异步分派+回调）、`send`（向已有 Worker 发消息）
- Thread 模型（参考 Anthropic）：主事件流只推摘要事件（`worker.status`/`worker.result`），副 Thread 保留完整细节
- 事件订阅过滤：`/ws/agent` 新增 `subscribe { event_types: [...] }` 命令
- 超时 10 分钟 + 空闲 5 分钟自动回收

### 建议 3：Meta-Agent 理解 Pan — Skill 注入 + Tool Schema 双层设计

**首选参考：CAO 的 Skill 注入 + CodeBuddy 的 Tool Schema**

| 层级 | 内容 | 投递方式 |
|------|------|---------|
| **Skill（Markdown）** | "你是一个 CLI Agent 编排器。可以用 handoff/assign/send 管理 Worker。不要空转等待。" | 注入到 Meta-Agent 的 system prompt |
| **Tool Schema（JSON）** | `{ "name": "spawn_worker", "parameters": {...} }` | 注册为 Meta-Agent 可用工具 |

**为什么双层**：
- Skill（Markdown）：提供"怎么做"的工作流指导（最适合 LLM 的语义理解）
- Tool Schema（JSON）：提供"能做什么"的精确参数定义（确保 LLM 正确调用 API）

**可选增强**：

| 增强项 | 来源 | 工作量 |
|--------|------|--------|
| Janitor 质量门控 | Bernstein | 中 — 在核心增加确定性验证层 |
| Review-Gate 审核层 | Edict | 高 — 增加审核 Worker 角色 |
| Worker Profile 声明 | A2A Agent Card | 低 — 数据结构扩展。已有落地参考：`RuleWhisper联动与框架优化建议.md` 第五章 (profiles + persona) |
| Hook 编排 | Ruflo | 高 — 需要协议层改动 |

---

## 六、优先级建议

| 阶段 | 内容 | 理由 |
|------|------|------|
| **Phase 1.5** | Worker 消息收件箱 + `worker.result` 事件 | Meta-Agent 和链式工作流的基础 |
| **Phase 2** | 三原语（handoff/assign/send）+ 事件订阅 + Skill/Tool 注入 + Worker Profile（绑定 RuleWhisper persona，`RuleWhisper联动与框架优化建议.md` 第五章） | 依赖 Phase 1.5 的消息机制 |
| **Phase 2.5（可选）** | Janitor 质量门控 + Worker Profile（config.json profiles 第一版已在 Phase 2 通过 RuleWhisper 联动需求落地） | 确定性质量保障 |
| **Phase 3（未来）** | Worker-to-Worker 通信 + Worker Profile 动态发现 | 上层抽象 |

---

## 七、待决策事项

- [ ] **Worker 通信是走 Core HTTP API 还是协议标记注入？** — 建议走协议标记注入（CCB 式，最轻量、零额外连接开销）
- [ ] **消息收件箱持久化吗？** — 建议不持久化。Worker 间消息是瞬态通信，丢了就重发
- [ ] **Meta-Agent Skill 的注入方式？** — cbc 适配走 `CLAUDE.md` / system prompt append；其他适配走各自原生机制
- [ ] **Tool Schema 格式？** — 用 OpenAI function calling 兼容格式（最泛用）
- [ ] **Worker 默认超时时间？** — 10 分钟（Maestro/Bernstein 共同采用）
- [ ] **是否需要 Janitor 质量门控？** — 简单场景不需要，复杂工作流可选择性启用（Bernstein 的 lint/type check 模式）
- [ ] **是否需要 Review-Gate 审核 Worker？** — 当前不需要。人工审查 + Janitor 已足够覆盖
- [ ] **Meta-Agent 是 P0 还是 P1？** — 建议保持在 P0 的架构规划中，但实现拆分为两阶段（Phase 1.5 基建 → Phase 2 完整 Meta-Agent）
- [ ] **共享记忆文件（`.pan/pan_memory.md`）的设计范围？** — 建议先只放 Worker 间协议约定和项目级约束，不做信息检索/索引

---

## 八、参考链接

| 项目 | 地址 | 核心价值 |
|------|------|---------|
| **CAO (AWS)** | https://github.com/awslabs/cli-agent-orchestrator | Worker 通信 + Supervisor 协议最佳参考 |
| CAO Supervisor Protocols | https://github.com/awslabs/cli-agent-orchestrator/blob/main/skills/cao-supervisor-protocols/SKILL.md | Meta-Agent Skill 模板 |
| CAO Worker Protocols | https://github.com/awslabs/cli-agent-orchestrator/blob/main/skills/cao-worker-protocols/SKILL.md | Worker 行为规范模板 |
| **Bernstein** | https://github.com/sipyourdrink-ltd/bernstein | 确定性调度 + Janitor 质量门控 |
| **CCB (Claude Code Bridge)** | https://github.com/bfly123/claude_code_bridge | 协议标记通信 + daemon-per-provider |
| **Edict (三省六部制)** | https://github.com/cft0808/edict | Review-Gate 审核层 + 通信权限矩阵 |
| **Anthropic Multi-Agent** | https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration | Thread 模型 + Coordinator 设计 |
| **A2A Protocol** | https://a2a-protocol.org/latest/specification/ | Agent Card + Task 生命周期标准 |
| Ruflo | https://github.com/ruvnet/ruflo | Meta-Harness + Hook 编排 |
| 三种编排模式对比 | https://qubittool.com/zh/blog/multi-agent-orchestration-patterns | Supervisor/Swarm/Hierarchical 选择 |
| 9 Agent Orchestrators 对比 | https://www.augmentcode.com/tools/open-source-agent-orchestrators | 全景对比 |
| Awesome AI Orchestration | https://github.com/LeoLin990405/awesome-ai-orchestration | 更多同类项目索引 |
| CodeBuddy Sub-Agent | https://www.codebuddy.ai/docs/cli/sub-agents | Tool Schema + System Prompt 设计 |
