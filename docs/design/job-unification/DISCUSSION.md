# Scheduler→Job 统一 · 讨论纪要（未排版草稿）

- 日期：2026-09-25（会话中）
- 状态：**讨论中，无任何结论性决议**；本文仅记录，供之后整理成正式计划
- 关联：PR #1 scheduler 插件评审（`packages/scheduler/`）；main 既有 `packages/core/background_jobs.py`

---

## A. 遗留待办（来自 PR 评审，尚未拍板）

已定结论（MA 核验后共识）：

1. `_write_lock` 覆盖 `update_task` 整个读-改-写（P0，定性为「防漂移的结构性加固」；
   `store.py:49` 是 RLock，`save_task:241` 内层再进锁不死锁）。
2. `scheduler_list` 无 target 时 restricted 身份过滤为 self+managed（Q3 选 B；实现层倾向 MCP 层，
   但需多一次 `_caller_identity()` 调用，未对齐）。
3. tick=1s 保持，记为已知成本。
4. 不做：`_check_access` 身份解析失败区分（跨模块议题，不卡本 PR）。

**尚未拍板的 3 个细节：**

1. `scheduler_list` 过滤的实现层：MCP 层客户端过滤（改动小）vs HTTP 层加权限（破坏
   api.py 零鉴权约定）。倾向前者，未与 MA 对齐。
2. `create_task`（store.py:246-302）是否一并进锁——倾向不碰，保持改动最小。
3. 测试形态：锁覆盖写「回归测试」而非「并发复现测试」（当前单事件循环拓扑下不可复现；
   多 worker 场景除外）。

⚠️ **注意：A 节全部待办可能被 B 节的统一计划作废或改写**（若 scheduler 收归 job 管理，
store/engine 会被重构）。拍板 B 节时序前不应急于实施 A 节。

---

## B. Job 计划（用户口述，2026-09-25，原文要点）

1. job 是相对独立于 agent 与 Pan 生命周期的机制，用于注册后台和自动化任务。
2. 记录所有类似「agent 创建的 detached 后台任务」，避免 job 与 agent 生命周期绑定。
   场景：agent 跑模型训练，不轮询、不因自身超时连坐杀任务；注册 job 后，job 自动调用
   pan 的 send 把 job 输出返回给 agent。
3. job 的输入/输出端口相对自由，输出可绑定**可切换**的 agent。A 创建的任务可通过切换
   归属让 B 接手管理。
4. job 具有邮箱机制：输出存盘积压，输出端空闲时（如被杀的 Pan 服务重新上线后）再推送。
5. job 承担定时/周期自动化任务（定时发送消息、群发消息等）。
6. 用户可通过 GUI 监视、管理和快捷创建简单 job。
7. main 已有 job 雏形，且与 scheduler 存在重复机制；**想把 scheduler 收归 job 管理，或把
   schedule 视作一种特殊的 job 插件/模板，便于管理**。

---

## C. 现有代码盘点（background_jobs.py 事实，2026-09-25 核实）

用户 6 点中有 4 点在 main 已有对应物：

| 计划能力 | 现有对应物 | 证据 |
|---|---|---|
| 1 独立生命周期 | 子进程 runner + PID/创建时间身份 + `reconcile_running()` 孤儿检测 | background_jobs.py:947-971 |
| 4 邮箱 | `notificationState` pending/delivered + 幂等 `terminalEventId`，重启后补投 | :974-1017 |
| 5 定时/周期 | session_message / session_broadcast job：interval/weekly 两种 schedule，claim 机制（running + `runStartedAt` + stale requeue），1s recovery loop | :581-708, :1020 |
| 2 输出送回 agent | `enqueue_notice(source="automation")` + `run_due_message_jobs` 走 session send queue | :1003-1010, :645 |

**真正缺的**：第 3 点（归属切换/可重绑定输出端口）；第 5 点的 cron/once 表达力；
第 6 点（GUI）——前端目前无任何 job 管理界面（未核实，待查）。

**重叠冲突**：scheduler PR 与 background_jobs 是两套独立的时间驱动
（scheduler tick 1s + leader 锁 vs background_jobs recovery loop 1s + 每 job 命名锁），
两套 schedule 表达（cron/once/interval vs interval/weekly），两套持久化
（data/scheduler/tasks/ vs data/background_jobs/）。

---

## D. 待澄清问题（尚未问/待用户答复）

1. 统一形态：深整合（job 是唯一抽象，scheduler=一种 job kind/插件，tick 收敛为一套）
   vs 浅整合（scheduler 引擎保留，仅注册表/GUI 展示统一）？
2. 时间驱动源收敛：两套 1s 循环是否合一？misfire 语义归一（scheduler 有 fire_now/skip +
   grace 300s；message job 无宽限概念，stale claim 直接 requeue 重投）？
3. 归属切换：切换后积压邮箱里的旧输出推给谁（新归属 or 原创建者）？权限是否复用
   managed/claim 体系？
4. 邮箱范围：仅积压终态通知（现状）还是全部输出流？体积上限/滚动策略？
5. 与本 PR 的时序：统一是后置立项（本 PR 照原样合）还是本 PR 内改架构？
   ——直接决定 A 节待办是否还有必要实施。
6. scheduled_task 在统一模型中的形态：无独立进程的「纯派发 job」如何进 reconcile/展示？

## E. 第二轮问答（2026-09-25）

**用户已确认：**
- Q1：job 是唯一抽象（深整合）；先后顺序待定，要决策参考。
- Q3 归属切换后积压推给**新归属**。
- 邮箱「全量输出流」含义待解释；Q4 要决策参考。

**Q2 解释后的 3 个不一致（统一必须裁决）：**
1. 派发原语：scheduler `worker.assign`（agent 执行任务）vs message job `send_session`
   （人可见的消息）。语义不同，统一模型须保留区分（deliveryMode 或双 kind）。
2. 错过语义：grace 300s + fire_now/skip 策略 vs stale claim（`MESSAGE_JOB_REQUEUE_AFTER_SEC`）
   直接重投，无宽限概念（background_jobs.py:593-604）。
3. 多实例策略：scheduler leader 锁单派发（每实例都跑 recovery loop）vs message job
   per-job 命名锁 + claim，多实例并发安全。**倾向统一到 claim 模型**（更细粒度、已在
   main 验证、天然多实例；scheduler 的 leader 锁退役）。

**Q3 邮箱三档位（待用户选）：**
1. 终态通知 + logPath 指针（现状：job 全量 stdout 本就写日志文件，邮箱只装便条）
2. 通知 + 尾部 N 行快照（有界概览）
3. 全量输出流进邮箱（无界、与日志文件双写、积压风险；不推荐）

**Q4 两种放法（倾向放法一）：**
1. 一等 job kind（kind="scheduled_task"）：改动集中在统一 tick 分派 + reconcile 排除集合
   加成员（background_jobs.py:956-957 已有此模式）；schedule 成为 job 字段，未来进程 job
   也能复用；GUI/权限/列表免费统一。
2. trigger 正交子结构（job=做什么，trigger=何时做）：更优雅但重构 background_jobs 数据
   模型本身，所有按 kind 分支的代码都要改，改动面大一个量级。留待真实需求出现再做。

**Q1 时序决策参考 6 条（倾向：先合并、后统一、A 节修复全搁置）：**
1. 迁移成本≈0（scheduler 从未发布，无存量用户/数据）→ 支持先合并的最强论据
2. cron.py 纯函数直接存活；真正扔掉的主要是 store 形状 + API 路由 + MCP 薄封装
3. API 契约债（/api/scheduler/* + 9 MCP 工具 + ScheduledTaskPanel）→ 支持先统一的最强论据
4. 外部贡献者 PR（fork，混 WeChat/ts/scheduler 73 文件）不宜被内部重构无限期挂起
5. packages/scheduler 隔离干净，合并不污染核心，将来挖空替换风险低
6. interim 价值：近期是否真用定时功能？用→先合并立即收益；不用→此条归零
- 连 P0 锁修复也可搁置：统一会重写 store，修了也是扔（且当前单事件循环下本就不可复现）

## F. 第三轮问答（2026-09-25）

**用户确认：**
- Q2① 派发原语：assign/send 是「可供选择的细节」；理想形态 job 可调用 Pan 的**所有
  接口**（按需添加，如定时创建 session）。→ 动作 = 对 Pan API 的调用模板，非封闭枚举。
- Q2③ 多实例策略：**per-job 认领**（leader 锁退役，此方向已定）。
- Q2② 错过处理：用户征询意见 → 我方分析见下。

**我方对 Q2② 错过处理的结论（待用户确认）：**
两套机制处理的**不是同一个故障窗口**，是正交关系，各取所长：
- scheduler grace+fire_now/skip 处理「到点时没人在场」（tick 停摆/休眠唤醒面对过期
  触发点）→ 决定**过期触发点还要不要跑**；
- message job stale claim requeue 处理「认领后跑一半死了」（claim 与终态之间的崩溃
  窗口）→ 保证 at-least-once 崩溃恢复。
统一方案：**错过策略**取 scheduler（grace + fire_now/skip 作为 trigger 字段；现状语义
自洽——fire_now 的宽限外补派正是其存在意义，休眠唤醒只补一次不补 N 次；发消息场景
天然不敏感，行为向后兼容）；**在途恢复**取 message job（stale claim requeue 成为统一
机制；scheduled_task 派发也走认领，requeue 重投由 dispatch_key 幂等保证不双跑）。

**新衍生问题（待问）：**
- job 调用任意 Pan API 时**以什么身份**：注册时 creator 身份快照 vs 运行时系统身份绕过
  权限？涉及鉴权模型，job 化任意动作前必须拍板。

## G. 第四轮问答（2026-09-25）

**Q2 衍生（身份）——用户已定：**
- source 固定几种：`agent + sessionId` / `user` / `system` / `plugin + pluginName`。
- **区分 source 与输出对象（target）**：source = 动作以谁的身份执行；target = 动作参数
  （如 user 创建的定时发送任务，接收对象是 agent A）。fire 时刻的权限裁决下放给各 API
  按其自身规则对该 source 类型执行——无全局 job 级绕过。残留注意点：「user」source 走
  HTTP 时 HTTP 层当前零鉴权（本地部署可接受，公开部署需另议）。

**Q3 邮箱——已定：档位 1**（便条 + logPath 指针；agent 收通知后自读日志）。

**Q4——用户已懂放法一，问放法二详解 → 已解答**（要点：放法二 = trigger 从 job 字段
升级为独立一等对象，不是单纯「字段拆开」；当前选放法一不封死放法二，schedule 字段
将来可提升为 trigger 表，无沉没成本）。

**Q4 定论（第五轮，用户提出、我方同意）：schedule 为列表 + schedule 模板，不做独立
trigger 对象。**
- schedule 字段允许是**列表**（一个 job 多条触发规则，每条可单独 enable/disable），
  覆盖放法二 90% 实用价值（同动作多时刻表），无需第二张注册表/join/双生命周期。
- **schedule 模板**（"工作日9点"等命名预设）解决输入复用（GUI 快捷创建）。
- 放弃的边缘能力：trigger 跨 job 挂载/独立授权（边缘场景，模板+broadcast 近似）。
- 实现注意 3 点：① next_fire_at 变 per-entry（扫描时取各 entry 下一跳的最小值，或
  存 per-entry next）；② dispatch_key 加 entry 索引防同刻多 entry 幂等碰撞；③
  runs.jsonl 记录 entryId 便于 GUI 定位。

## H. Q1 时序定论（2026-09-25，待用户最终 go）

采用「先合并、后统一」，且不走 fork 推送：PR #1 以 `--no-ff` 合入本地待处理分支
（基于 main，建议名 `integrate/pr1`）；TA 在其上修非 scheduler 块（ts 审计、WeChat
清理、前端接入缺口）；验收后由用户决定何时合 main；**统一作为合 main 后的普通立项**，
本纪要届时升级为正式计划。A 节 PR 修复全部搁置（统一重写 store，修了也是扔）。
PR #1 平台侧最终手动关闭+留言。
