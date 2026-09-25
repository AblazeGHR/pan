# Job 统一计划（PLAN_JOB_UNIFICATION）

- 版本：v0.1 草案（2026-09-25，由 DISCUSSION.md A-H 节八项已定决策整理而成）
- 状态：**待立项**——前置条件是 integrate/pr1 验收并合入 main；TA 非 scheduler 修复与本计划并行
- 契约源：本文件。DISCUSSION.md 保留为决策过程存档，冲突时以本文件为准。
- 相关设计细节文档：DESIGN_DISPATCH_CLAIM_FUSION.md（派发×认领融合，统一的心脏）

---

## 0. 目标与范围

**目标**：job 成为 Pan 后台/自动化任务的唯一抽象。scheduler 插件（PR #1）收编为一种
job kind；background_jobs.py 的四种 kind 并入统一注册表；时间驱动、崩溃恢复、通知邮箱
各只保留一套机制。

**范围内**：统一 job 注册表与数据模型、统一认领循环、scheduled_task kind、source/target
模型、schedule 列表 + 模板、GUI 基础管理。
**范围外（本轮不做）**：独立 trigger 对象（放法二，留待真实需求）；HTTP 层鉴权加严
（user source 的遗留注意点单列 §8）；任意 API 调用模板的全量扩容（机制留好，按需加动作）。

---

## 1. 数据模型（job 记录 schema）

```
job: {
  jobId:            "job_" + hex12         # 主键，沿用现有格式
  kind:             见 §2 kind 清单         # 一等 kind，reconcile/循环按 kind 分支
  status:           pending|scheduled|starting|running|completed|failed|cancelled
                                            # 沿用 background_jobs 状态机，scheduled=
                                            # 有未来触发点（复用现有语义 :597-598）
  # ── 身份（第四轮定论）──
  source: { type: "agent"|"user"|"system"|"plugin",
            sessionId?: str, pluginName?: str }   # 创建者身份，fire 时以该身份执行
  target: { sessionId: str | null }        # 输出/通知对象；与 source 正交。
                                            # 归属切换 = 改此字段（第二/四轮）
  # ── 触发（第五轮定论：列表 + 模板）──
  schedule: [ entry ]                       # 列表！每条 entry:
  #   { id: "schx_" + hex6, kind: once|interval|cron,
  #     at?/intervalSec?/cron?, timezone?, anchor?,
  #     misfirePolicy: fire_now|skip, enabled: bool,
  #     nextFireAt: ISO|null }              # per-entry 下一跳，扫描取全表最小值
  #   单发动作（进程 job、手动 API job）schedule = [] 或缺省
  # ── 动作（第三轮定论：API 调用模板）──
  action: { api: str, args: dict }          # 对 Pan 内部接口的调用模板。
                                            # 首批: assign / send_session / spawn_process
  # ── 邮箱（第四轮定论：档位 1）──
  notificationState: pending|delivered      # 终态便条投递状态（沿用 :985-1015）
  terminalEventId: str|null                 # 幂等键（沿用）
  undeliveredFires: [ note ]                # P1 实现定名（原稿 mailbox）：调度触发
                                            # 无法投递的便条 {entryId, fireAt,
                                            # dispatchKey, text, error}，上限 20
                                            # （SCHEDULED_TASK_UNDELIVERED_MAX）；
                                            # target 恢复/切换后由统一循环重投。
                                            # 进程 job 的终态通知仍走
                                            # notificationState/terminalEventId。
  # ── 派发幂等 ──
  lastFireAt / runCount / lastError / maxRuns / paused
  lastDelivery?: dict                        # 最近一次动作返回（broadcast 时为
                                            # {status, results[], errors[]} 汇总）；
                                            # 历史在 runs.jsonl（部分失败也落该处：
                                            # status="partial" + 摘要 + 明细内联）
  logPath?: str                             # 进程 job 专有（stdout 全量在日志文件；
                                            # 非进程类无此字段，runs.jsonl 即全量）
  createdAt / updatedAt
}
```

**持久化**：`data/jobs/job_*.json` 一 job 一文件 + `data/jobs/runs.jsonl` 历史
（合并现有 `data/background_jobs/` 与 `data/scheduler/`；迁移见 §6）。
原子写沿用 `_atomic_write`（os.replace + Windows 有界重试）。
**锁**：`_write_lock`（进程内 RLock）**必须覆盖整个读-改-写**——这是 PR 评审 P0 结论
在统一模型里的落实，锁纪律从第一天就写对。

---

## 2. kind 清单与排除集合

| kind | 动作 | 进程 | reconcile 孤儿检测 | 来源 |
|---|---|---|---|---|
| background-process | spawn 独立 runner 子进程 | 是 | **是**（PID/创建时间身份） | background_jobs.py:37 |
| session-message | send_session 单目标 | 否 | 否（排除集合） | :38 |
| session-broadcast | send_session 扇出 | 否 | 否 | :39 |
| main-lifecycle | 服务生命周期标记 | — | 否 | :40 |
| **scheduled_task（新）** | action.api 任意（首批 assign） | 否 | **否（加入排除集合）** | PR #1 scheduler |

排除集合模式已有先例（background_jobs.py:956-957），scheduled_task 只是加成员。
无进程动作的 kind 靠 claim 状态机 + 幂等键保证 exactly-once 视角（见融合设计文档）。

---

## 3. source × target 身份模型（第四轮定论）

- **source 四类**：`agent+sessionId` / `user` / `system` / `plugin+pluginName`。
  fire 时刻动作以 source 身份执行，权限由各 API 按自身规则裁决——**无 job 级全局绕过**。
- **target 正交**：动作参数，指明输出/通知对象。user 建的定时任务发给 agent A =
  source=user, target=A。
- **归属切换**：改 `target`（含 managed 语义的接管）；切换后 mailbox 里未投递的便条
  **投新归属**（第二轮定论）。切权限裁决跟着 source 走还是随归属切换，**待定**
  （倾向：source 不随切换变——动作身份保持创建语义，切换只改收件人）。
- 注意：scheduler 的 misfire 通知 / assign 的 dispatch 均带 source="automation"——
  统一后归入 system 类。

## 4. 时间驱动：统一认领循环（第三/四轮定论）

- **唯一循环**：每秒一轮（现 recovery loop 位置），扫 `schedule` 列表，取各 entry
  nextFireAt 最小者判到期；scheduler 的独立 tick + **leader 锁退役**。
- **per-job 认领**：到点 → `_job_lock` + 状态置 running + runStartedAt → 执行动作 →
  落终态/推进 entry。多实例天然安全（认领后他实例跳过）。
- **错过策略（取 scheduler）**：late > grace（默认 300s）时 once → expired 自动 disable；
  周期 → 按 entry.misfirePolicy：fire_now 补派一次 / skip 跳过并推进。
- **在途恢复（取 message job）**：running 超过 `REQUEUE_AFTER_SEC`（现 5s，可按 kind
  配）无终态 → stale claim 重入队；重投由幂等键保证不双跑（见融合设计文档）。
- **schedule 模板**：`config.json` 或 `data/jobs/schedule_templates.json` 存命名预设
  （"工作日9点"等），GUI 快捷创建引用之；模板是输入复用，非运行时实体。

## 5. API / MCP / GUI

- **HTTP**：`/api/jobs/*`（统一新路由）；`/api/scheduler/*` **保留为兼容别名**一个
  版本周期，内部重定向到 job API（scheduler 从未发布，别名只为消化 PR #1 期间的前端
  面板）。PR 的 9 个 `scheduler_*` MCP 工具改为薄封装指向 job API。
- **GUI**（第 6 点，需求未细化——**待用户问答**）：最低范围 = JobsView 列表
  （kind/status/下次触发/最近结果/logPath 链接）、enable/disable/delete、
  模板化快捷创建。管理动作集合与展示字段另开一轮讨论。
- cron.py 纯函数零 I/O，**原样保留**为 `packages/jobs/cron.py`。

## 6. 迁移

1. `data/scheduler/tasks/*.json` → 逐条转 `scheduled_task` kind job
   （schedule 变单元素列表，text→action assign，target_session_id→target）。
2. `data/background_jobs/` 字段名基本沿用（camelCase），微调即可。
3. 迁移脚本一次性 + 启动时探测旧目录提示；无外部存量用户，**不做双写**。
4. PR #1 的 scheduler 测试套件（tests/test_scheduler_*.py 约 2000 行）随接口重写
   改造，cron/求值类测试应原样通过。

## 7. 分阶段实施

- **P1 内核**：统一数据模型 + 认领循环 + scheduled_task kind + 迁移脚本（后端可全测）
- **P2 接口**：/api/jobs/* + MCP 工具重定向 + 兼容别名
- **P3 GUI**：JobsView + 模板快捷创建
- **P4 收编**：删 packages/scheduler 引擎与 leader 锁、旧路由别名下线
- 每阶段独立可验收；P1 完成即达到"两套循环合一"的核心目标。

## 8. 已知遗留（记录在案，本计划不解决）

- user source 走 HTTP 层零鉴权——本地部署可接受；网络暴露场景需 API 鉴权立项。
- `_check_access`「有身份但解析失败→放行」的跨模块加固（区分无身份/解析失败）。
- misfire 通知里 humanReadable 时间摘要（scheduler API 曾有 preview_next，届时随 GUI 定）。

## 9. 未决问题（立项前需拍板）

1. ~~归属切换是否联动 source~~ → **已定（第六轮）**：source/target 完全正交、均可独立
   修改；A→B 切 target 不影响 source（执行身份不变）。job 操作权限另开讨论。
2. GUI 需求细化 → **已定方向（第六轮）**：后端先行，GUI 慢慢调；P3 范围按 §5 最低
   草案起步，迭代补充。
3. 兼容别名退役 → **已定（第六轮）**：`/api/scheduler/*` 别名暂不退役；
   ScheduledTaskPanel 保留在代码中，GUI 调整时再决定去向。

## 10. 第六轮补充决策：target 缺失的积压语义

- target session 不存在/不可投递时：**积压输出 + warning 提醒用户切换 target**，job
  不因此失败/取消；
- 切换 target 后积压便条正常投递新 target（与 mailbox 既有语义一致，正好衔接）；
- 实现要点：终态通知投递循环（recover_notifications 形态）捕获投递失败 → 记
  `notificationState=undeliverable`（新增态）+ warning 事件（GUI/日志可见）→ 保留
  pending 便条不丢；切换 target 的 API 落盘后自动重置为 pending 触发重投。

## 11. P1 实施纪要（2026-09-25 落地，worktree jobs-unification-p1）

P1 内核已在 `packages/core/background_jobs.py` 落地：`scheduled-task` kind、
统一认领循环（`run_due_scheduled_tasks`，挂 recovery loop）、stale requeue、
grace/misfire、max_runs、paused 推进、undeliveredFires 积压重投、
runs.jsonl 泛化、迁移（`store.migrate_legacy_tasks`）。测试全绿
（scheduler 151 项 + background_jobs 66 项 + 全量）。

与本计划正文的三处**有意偏离**（均为实施时裁决）：

1. **kind 名 `scheduled-task`（连字符）**，非正文表的 `scheduled_task`——与既有
   kind 命名（`session-message` 等）一致（background_jobs.py:41）。
2. **dispatch_key 前缀用 taskId 而非 jobId**：`f"{taskId}:{entryId}:{fire_ts}"`
   （DESIGN §3 的唯一性/稳定性实质保留；taskId 是迁移后仍稳定的对外主键，
   jobId 在迁移时会重新生成）。兼容层同时保留 `taskId` 字段承载旧 `sch_` id。
3. **注册表根未改名 `data/jobs/`**：沿用 `data/background_jobs/`（迁移成本为零，
   用户的既有 message/process job 记录原地不动）。`PAN_SCHEDULER_DIR` >
   `PAN_BACKGROUND_JOBS_DIR` > 默认根，生产环境两类 job 同表。
4. cron.py 上移 `packages/jobs/cron.py`（正文既定）；`packages/scheduler/cron.py`
   保留为再导出垫片，P4 随插件退役。

§10 的 undeliverable 落地形态：`undeliveredFires` 便条列表（上限 20）+
`lastStatus="undeliverable"` + fired 事件；target 恢复/切换后由循环自动重投，
dispatch_key 原样复用（接收端幂等索引兜底）。终态通知层的
`notificationState=undeliverable` 新态留给 P2（当前 scheduled-task 不产生终态通知）。

## 12. P2 实施纪要（2026-09-25 落地，worktree jobs-unification-p1）

P2 后端收口，GUI 对接面齐备：

- **/api/jobs/***（packages/jobs/api.py，挂 server.py）：全 kind 列表
  （kind/status 过滤 + active-first）、详情、PATCH（name/description/enabled/
  paused/target 归属切换）、DELETE、runs、run-now、next 预览、kinds 元数据、
  schedule 模板 CRUD。`/api/scheduler/*` 兼容别名照旧（§9）。
- **结构化 source/target**：新记录双写 `sourceStruct`/`targetStruct`（扁平
  字段保留兼容旧读者）；出口 `job_public_view` 把任何年代的记录折算成
  `source: {type, sessionId?, pluginName?}` × `target: {sessionId, sessionIds?}`
  （automation→system；旧记录读路径折算不重写文件）。归属切换 =
  PATCH target，scheduled-task 积压便条由统一循环按新 target 重投。
- **action 模板**：`_run_job_action` 按 `action.api` 分派，首批
  assign（默认，幂等键兜底）/ send_session（message 语义）；未知 api 或
  兼容层旧记录（无 action 字段）一律回落 assign。
- **schedule 模板**：packages/jobs/templates.py，内置 4 条（工作日9点/
  每小时/每天21点/每周一9点）+ 自定义持久化（schedule_templates.json，
  原子写），内置不可删。
- **partial_failed 事件**：run_due_message_jobs 的 partial 分支即时广播
  `job.partial_failed`（jobId/name/errors/results），GUI toast 监听用。

遗留到 P4：mailbox 终态通知的 notificationState=undeliverable 新态
（scheduled-task 暂不产生终态通知，无消费方）；MCP jobs_* 薄封装。
