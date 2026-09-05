# 术语统一 × Background Job 分支：同步点与冲突风险记录

> 创建：2026-09-05（docs-skills-concept-clarification TA）。供 MA 在测试分支统一解决冲突时使用。
>
> - 本分支：`docs/terminology-ma-ta` @ `136a1a3`（基于 Pan-main/main `1fd9e08`）——MA/TA/Session/Worker 术语统一。
> - 并行分支：`feature/background-job-runner` @ `4fbb0f7`（同基线 `1fd9e08`）——Background Job Runner MVP。

## 1. 必须保留的术语定义（合并时不得被改写或删除）

三层模型（已固化在 `docs/skills/pan/SKILL.md` 开头与 §1、`docs/USER_MANUAL.md` §1.1、`README.md`/`README.en.md` 核心概念）：

- **角色（职责）**：meta-agent（MA）负责编排元任务；task-agent（TA）执行具体开发/测试/调查/文档任务。
- **身份（持久编排对象）**：Session 承载 MA 或 TA 身份，以 session_id 寻址；旧称 "Agent" = Session（兼容说法）。
- **进程（物理执行体）**：Worker 是临时 CLI 进程，实际运行 MA 或 TA 的 Session——**既有 MA Worker 也有 TA Worker**；Worker 不承载身份、不与 TA 等同。
- **推荐关系链**：用户 ↔ MA(Session/Worker) → `agent_assign` → TA(Session/Worker) → `report_subscribe`/`queue_pending` → MA 验收。

## 2. Background Job 相关术语定位（与三层模型的关系）

bg-job 设计文档（`docs/design/background-jobs.md`）自述 "Background Jobs are durable process records, not Workers. An Agent Worker may start and disappear while the Runner process continues."——与本术语体系**一致**，无需改写其事实描述。统一口径：

| bg-job 概念 | 术语定位 |
|---|---|
| **Background Job** | 持久化的**进程记录**（Job Registry 条目）：既不是 Worker（不占 Session 名下唯一 worker 槽位）、不是 TA/MA（不是角色）、也不是 Session（不承载对话身份） |
| **Background Job Runner**（`packages/core/background_runner.py`） | **Pan Core 控制面上具有独立生命周期的组件进程**：由创建 Job 的 API 拉起，独立于创建它的 Agent Worker 与 Pan 主进程恢复循环；崩溃不影响 Session 数据 |
| **恢复循环（lifespan recovery loop）** | Pan 主服务生命周期内的对账机制：把孤儿 Runner 记为 failed、把终态 Job 的通知投影到目标 Session 的 `queue_pending` |
| `agent_background_start/get/list/cancel/retry` | bg-job 新增的 5 个 MCP 工具（调用方 = 当前 MCP Agent Session，即 MA 或 TA 身份均可）；`agent_notify` 保持底层兼容 |

关键边界句（建议合并后如 bg-job 文档有中文版可沿用）：**Job 的生命周期与 Worker/Session 生命周期解耦——目标 Session 被删除时 Job 事实保留、通知保持 pending。**

## 3. 文件级冲突风险清单

| 文件 | bg-job 改动（4fbb0f7） | 本分支改动（136a1a3） | 冲突风险 | 合并裁决规则 |
|---|---|---|---|---|
| `docs/skills/pan/references/http-api.md` | 在 `/api/notify` 行后、`/api/report-subscribe` 行前**插入 5 行** background-jobs 端点 | 改了同一区域附近：`/api/report-subscribe` 行 `managerId "<meta-agent session id>"` → `"<MA session id>"`；顶部 §6→§5、§8.1→§7.1、§11.2→§10.2、§8.3→§7.3 | **高（同 hunk）** | 两者都保留：bg-job 的 5 行端点**原样保留**（不覆盖、不删除）；本分支的 MA 措辞与 §引用修正**原样保留** |
| `docs/design/background-jobs.md` | 新增（65 行，英文） | 未动 | 无 | 原样保留；如需中文术语说明可另加段落，不改事实 |
| `docs/skills/pan/SKILL.md` | 未改（但 bg-job 新增 5 个 MCP 工具使 §5「当前共 38 个工具」计数失效） | §开头术语块、§1 概念表、多处 meta-agent→MA、§2.1/§5 命名分层等 | **语义同步点（非文本冲突）** | 合并后需把 §5 工具计数 38→43，并在「编排派发」表后补 bg-job 工具行（MA/TA 身份均可调用，Job ≠ 派发任务，普通任务仍走 `agent_assign`） |
| `README.md` / `README.en.md` / `docs/USER_MANUAL.md` / `.en.md` | 未动 | 多处 | 无 | — |
| `manifest.json` | 未动 | SMA system_prompt 加最小映射（MA/TA） | 无 | — |
| `packages/mcp/server.py`、`packages/core/*`、`packages/web/server.py` | bg-job 代码改动 | 本分支未动代码 | 无 | — |
| `tests/test_docs_terminology.py`（本分支新增） | 未动 | 新增静态检查 | 与 `tests/test_background_job*.py` 无冲突 | 合并后整跑 `python -m pytest tests/ -q` |

## 4. SKILL.md 主源 / 同步副本清单与状态

| 副本 | 位置 | git | 当前状态 |
|---|---|---|---|
| **主源** | `docs/skills/pan/SKILL.md`（本分支） | 是 | 已统一术语（136a1a3） |
| 编辑器同步副本 | `D:/project/Pan/.codebuddy/skills/pan/SKILL.md` | 否（gitignored，按检出维护） | ✅ 已同步为 136a1a3 版本；**测试分支/practical 合并后需再刷新** |
| 全局 skill 加载区 | `C:/Users/14709/.agents/skills/pan/SKILL.md`（+ `references/`） | 否 | ⏳ **落后**，待 MA 合并后另派 TA 更新（含 references 三份） |
| Pan-main 检出 | `D:/project/Pan-main/.codebuddy/` | — | 不存在（无需处理） |

## 5. 合并时术语审查清单（逐项过）

1. bg-job 的 5 个 HTTP 端点行、`docs/design/background-jobs.md` 全文、`agent_background_*` 工具描述**完整保留**，未被我方术语改动覆盖。
2. 本分支保留项完好：SKILL.md 开头术语分层块、§1「既有 MA Worker 也有 TA Worker」、USER_MANUAL §1.1 表、`tests/test_docs_terminology.py`。
3. http-api.md 合并后该表 hunk 同时含：bg-job 5 行 + `managerId "<MA session id>"` 措辞 + §引用修正。
4. SKILL.md §5 工具计数与 bg-job 新工具行补齐（43 个）。
5. 禁用旧词无回流：`rg -n "worker-agent|subagent|child agent|Task-Agent" README.md README.en.md docs/USER_MANUAL.md docs/USER_MANUAL.en.md docs/skills/pan`（允许 SKILL.md「旧称呼映射」说明行命中）；或直接 `python -m pytest tests/test_docs_terminology.py -q`。
6. `git diff --check` + 链接抽查（本分支已修正的 §引用、手册章节锚点不被合并回退）。
