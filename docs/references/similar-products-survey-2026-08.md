# Pan 相似产品调研（2026-08）

> 调研目的：确认 Pan 所处赛道与差异化位置，重点对比「Meta-Agent 与子 agent 协作」且「用户便于在任何时候介入」的产品。
> 调研日期：2026-08-23。来源以 2025–2026 公开资料为主（官方文档 + 2026 年对比文章），个别为行业常识，置信度在正文标注。

## 核心结论

**没有发现与 Pan 完全同构的产品。** Pan 的组合——中立编排中间层 + 直接驱动外部 CLI Agent 进程 + 会话与进程生命周期解耦 + 人类随时旁观/插话/接管终端——是当前唯一。按「Meta-Agent 调度 + 随时介入」诉求，存在四个梯队的产品：

1. **同赛道最接近**：Claude Code Agent Teams、Codex Agents Dashboard + Queue —— 均绑单厂商 CLI；
2. **框架层**：LangGraph / CrewAI / OpenAI Agents SDK / Google ADK 等 —— Supervisor 模式 + 显式 HITL 中断点，但需写代码；
3. **产品层**：Knowlee / OpenHands / Devin / Cursor / Roo Code 等 —— "中控室大屏"类 fleet console；
4. **观察治理层**：LangSmith / Langfuse / AgentOps 等 —— 能看能批，不能接管。

---

## 一、同赛道最接近：CLI Agent 编排 + 统一控制台

| 产品 | 出现时间 | Meta-Agent↔子 agent 模型 | 人如何介入 | 与 Pan 的差距 |
|---|---|---|---|---|
| **Claude Code Agent Teams** | 2026-02 | 一个 session 当 team lead，派多个 Claude Code 实例并行；teammate 独立 context，不继承 lead 历史 | `/tasks` 查看/接管/停止后台 agent；权限请求经 **leader permission bridge** 汇聚到 lead 一处审批；`/fork` 复制对话为独立持久 session | 只能管自家 CLI；无终端接管语义（只能 stop/resume）；无记忆注入/Character/watchdog/报告收件箱 |
| **Codex Agents Dashboard + Queue** | 2026-08（CLI 0.149.0） | 本地 app-server daemon 管理全部并发任务，按工作目录分组 | Dashboard 三态一览（Need input / Working / Ready）；`Ctrl+X` 停止；`codex queue --thread <名字或UUID> --message <指令>` 从任意终端向任意 session 注入指令；`/status` 查 UUID | 绑自家 CLI；无跨厂商适配；无记忆/人格/自愈/收件箱 |
| **Claude Code Subagent / `/subtask`** | 2024–2026 迭代 | 主会话派 worker，不继承主会话历史；可后台并行（上限默认 20 并发 / 200 累计）；subagent 复用完整 agent runtime（自带 MCP、权限、transcript） | 权限请求回流到 lead 的 `ToolUseConfirmQueue`，UI 带 worker 标识——用户只在一个地方做审批 | 进程内机制，非独立持久 session（除 `/fork`）；单厂商 |

**定位注记**：Codex Queue 的「从任意终端向指定 session 注入指令」+ Dashboard「随时看所有任务」，是操作层面与 Pan 最像的实现；Claude Code Agent Teams 是概念层面（lead 调度 + 独立持久 session + 集中审批）最像的实现。二者都不是 Pan 式的中立中间层。

---

## 二、框架层：Supervisor 模式 + 显式 HITL 中断点

需在代码中定义图/角色，非运行时中间层。「随时介入」做成了头等公民。

| 框架 | 模型 | 人如何介入 | 与 Pan 的差距 |
|---|---|---|---|
| **LangGraph（LangChain）** | graph 编排 + checkpointing | **interrupt 任意节点暂停 → 人改 state → resume**；LangGraph Studio 可视化调试：实时暂停、time-travel 回放、改中间状态重跑。介入最彻底 | 纯代码框架；无外部 CLI 进程；无记忆/人格/watchdog |
| **CrewAI** | 角色化 crew + 层级 manager agent（最接近"主管派活"）；Flows 事件驱动 | CrewAI Studio UI 启动/观察/对话 crew；子任务完成后回主管处汇报 | 框架；无持久进程模型；介入点在流程交接而非随时 |
| **OpenAI Agents SDK**（Swarm 继任） | `handoffs` 移交控制权、sessions、guardrails；编排是代码内定义 | 无独立控制台；HITL 靠代码写审批回调 | 框架；无持久进程 |
| **Google ADK** | 多 agent + A2A 协议（Agent2Agent，Google 2025-04 开放标准） | 内置 human-in-the-loop 工具；配套 Web UI 可与 agent 对话 | 框架；介入点为设计时定义 |
| **Microsoft Agent Framework + Copilot Studio** | M365/Azure 生态内编排，治理走 Purview | Purview 治理/审批 | 框架；锁微软生态 |
| **MetaGPT / AutoGen / AgentScope（阿里）/ smolagents（HF）** | 角色化/对话式多 agent，"Meta" 是核心卖点 | 偏学术/代码层 | 框架；介入能力弱 |

---

## 三、产品层：AI Workforce / Fleet Console（中控室大屏类）

2026 年出现的整类 "agentic workforce" 产品，形态最接近 Pan 的「中控室大屏」。

| 产品 | 类型 | Meta-Agent↔子 agent | 人如何介入 | 备注 |
|---|---|---|---|---|
| **Knowlee** | 商业 SaaS | fleet dashboard + 并发 sessions | 实时 agent 状态、**in-flight interrupt/steer**、审批流一等公民 | 产品形态最接近"中控室"，但面向企业合规（AI Act），非本地轻量部署 |
| **OpenHands** | 开源自托管 | 云端/本地 coding agent，多 agent 并发 | Web UI 实时围观 + 中途发消息介入 | 模型无关；无终端接管/记忆人格/watchdog |
| **Devin** | 商业闭源 | 会话式 autonomous agent + planning 面板 | 中途发消息修正方向 | 绑云端；介入能力有限 |
| **Cursor 2.0/3** | IDE | 最多 8 个并行 background agents | approval agents / PR 路由审批，人在 PR 层兜底 | IDE 场景限定 |
| **Roo Code Orchestrator（回旋镖模式）** | IDE 插件 | Orchestrator 拆解任务 → 派给 Architect/Code 等专用 mode 独立上下文执行 → **回旋镖回主管汇报** | 人在子任务交接点审核 | 结构上与 Pan「主管派活→收汇报」最神似；限定 VS Code |
| **GitHub Copilot Workspace** | 平台 | spec → plan → implement | 各阶段人审 | HITL 在决策点而非随时 |
| **Agentforce / Cognigy / Hubler / AGNTSEA** | 企业平台 | fleet + 治理 | 审批/审计 | 重治理，个人不可用 |

---

## 四、观察 / 治理层（能看能批，不能"接管终端"）

| 产品 | 特点 | 与 Pan 的差距 |
|---|---|---|
| **LangSmith**（含 Engine / Fleet） | tracing + 人工标注/审批；Fleet 低代码 agent；原生 MCP/A2A/Agent Protocol | 围观/治理类，无接管语义 |
| **Langfuse** | 开源 MIT，可自托管；框架无关 tracing + prompt 管理 | 同上 |
| **AgentOps** | 多 agent **session replay**（人类可读时间线回放） | 回放 ≠ 介入 |
| **Braintrust / Laminar / Helicone** | eval / 代理 / 成本观测 | 同上 |

**协议层（与 Pan 的 adapter 抽象直接相关）**：

- **MCP**（Model Context Protocol）：工具层标准，Pan 已支持。
- **A2A**（Agent2Agent，Google）：agent 间通信开放标准，Pan 当前无 worker 间直连，未来可考虑。
- **ACP**（Agent Client Protocol，Zed + JetBrains 2026）：IDE↔agent CLI 互操作标准 + Registry。**Pan 的 adapter 抽象本质上是 ACP 的先行私有实现**；若 ACP Registry 成熟，Pan 的"任意 CLI 即插即用"会更通。

---

## 五、与 Pan 特性逐项对照

| Pan 特性 | 谁有 | 谁没有 |
|---|---|---|
| Meta-Agent 是**角色**（任意 CLI 经 MCP 扮演主管） | Claude/Codex（仅自家 CLI） | LangGraph/CrewAI 等框架（主管是代码逻辑） |
| 会话持久化**与进程解耦**（kill 不丢，可重建续聊） | Claude `/fork`、Codex sessions | 多数框架无此概念 |
| 人**接管真实终端**（takeover） | — | 全部没有（Claude/Codex 只能 stop/resume） |
| watchdog 自愈 + 落盘队列 | — | 几乎全部没有 |
| 报告收件箱（managed 订阅制） | — | Roo 回旋镖"汇报"神似，但无收件箱 |
| 记忆注入 + Character 跨会话人格 | Letta (MemGPT) 记忆单点 | 编排类普遍缺 |
| 多通道（Dashboard / QQ / Remote / MCP） | OpenHands 有 Web + CLI | 单一入口居多 |

**一句话定位**：Pan 站在「Claude Code Agent Teams」与「Codex Agents Dashboard」之间，并补齐了二者都没有的**跨厂商 CLI 适配 + 终端接管 + 自愈 + 记忆/人格 + 报告收件箱**；形态上更接近「个人自托管版 Knowlee + ACP 运行时」。

---

## 附：主要信息来源

- Claude Code Agent Teams 官方文档（code.claude.com/docs/en/agent-teams）；javaguide.cn《Claude Code Multi-Agent 机制详解》（2026-07-26，核对至 v2.1.218）
- OpenAI Codex Agents Dashboard / Codex Queue（proflead.dev，CLI 0.149.0，2026-08-20）
- Knowlee《Agentic Workforce Platforms Compared 2026》（2026-04-29，含利益冲突披露，作为产品面参考）
- Latitude《AI Agent Observability Tools: 2026 Comparison》；ai-tools-hub / aimultiple 观测对比
- Google A2A 官方博客（2025-04-09）；zed.dev/acp、jetbrains.com/acp
- Cursor / Roo Code / OpenHands / Devin 相关 2026 报道
