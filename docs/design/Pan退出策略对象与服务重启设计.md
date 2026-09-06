# Pan 退出策略对象与服务重启设计

状态：设计提案，待实现

日期：2026-09-06

适用范围：Pan Core 服务退出、服务重启、Worker 关停、Session 合法状态持久化、Windows detached supervisor。

相关文档：

- [Pan 启动器与 Windows 生命周期架构](Pan启动器与Windows生命周期架构.md)
- [queue-at-most-once](queue-at-most-once.md)
- packages/core/main_lifecycle.py
- packages/core/worker.py
- packages/core/session.py
- packages/core/background_jobs.py
- packages/web/server.py

## 1. 决策摘要

Pan 采用“策略化退出”设计：

1. 对外继续保留语义清晰的 /api/main/exit 和 /api/main/restart。
2. 内部提取一个统一的服务退出/停止流程，由不可变的 ExitPlan（退出策略对象）决定停止后的行为。
3. Restart 不再维护一套独立的 Worker 停止流程，而是复用 Exit 的公共停止阶段，再执行 start 和恢复。
4. Exit 的公共阶段负责 Worker 闸门、Worker 关停、历史和队列持久化、合法状态收尾、旧服务身份校验和服务停止。
5. 策略只在入口处解析一次并持久化到 durable lifecycle Job；后续节点读取同一个策略，不在各处散落 keep_worker_running 或其他布尔判断。
6. Worker 的实时状态、最后合法状态、服务生命周期状态和未来的恢复意图必须分开表达。

目标结构如下：

```text
public /api/main/exit
        └─ ExitPlan.terminal()

public /api/main/restart
        └─ ExitPlan.restart()

两者都进入：
  prepare -> stop workers -> finalize worker state
          -> stop verified service

ExitPlan.post_stop_action:
  offline -> 结束
  start   -> start service -> readiness -> recover -> ready
```

本设计不建议让外部调用者用一个含义隐晦的 exit(restart=true) 代替 restart。参数化应主要存在于内部生命周期引擎；公共 API 仍然使用明确的操作名。

## 2. 需求背景

### 2.1 当前已经存在的需求

Pan 已经有两个服务级操作：

- Exit：彻底停止当前 Pan 服务。
- Restart：停止当前 Pan 服务，然后重新启动同一 checkout 的服务。

两者都必须处理同一批基础问题：

- 不能让服务停止过程中继续产生新的 Worker；
- 必须识别并校验旧 Pan 服务的 PID、创建时间、命令行和监听端口；
- 必须安全停止 Pan 进程树，不能误杀其他 checkout、其他端口、QQ 或 Tunnel；
- 必须把生命周期过程写入 durable Job，使请求断开后仍可查询结果；
- Windows 下必须先建立脱离旧进程树的 supervisor，再停止旧服务；
- 启动成功必须以真实 HTTP readiness 和新进程身份为准。

### 2.2 产生本方案的新增需求

未来不只有“永久 Exit”和“正常 Restart”两种退出行为，还可能需要在服务退出时附带特殊行为。例如：

1. 彻底停止服务，但把所有当前处于 running 的 Worker 杀掉后，Session 的最后合法状态仍保留为 running。
2. 该保留状态的目的不是声称 Worker 仍然存活，而是为下一次启动提供“这个 Session 原本有未完成运行，应尝试继续”的恢复线索。
3. 退出后自动 start，形成 Restart。
4. 退出前先完成某类持久化、排空某类队列或关闭某个可选组件。
5. 退出后保持 offline，但保留某些恢复意图，等待用户下一次手动启动。
6. 某些特殊行为只对 running Worker 生效，idle、held、queued、done 或 error Worker 采用不同策略。

如果 Exit 和 Restart 各自维护一套流程，未来很容易出现：

- Exit 新增了 Worker 合法状态收尾，但 Restart 忘记同步；
- 一个路径 flush 了流式历史，另一个路径直接杀进程；
- 某个 kill 或 respawn 分支绕过了特殊策略；
- 外部 supervisor 已经开始杀服务，但 Worker 状态还没有落盘；
- UI 显示的是“最后合法状态”，却被误解成当前进程仍在运行；
- 新增的附带行为只存在于内存参数中，Pan 进程退出后丢失。

因此需要把“退出过程中的共性逻辑”和“退出完成后的差异行为”显式分离。

### 2.3 当前代码事实

当前 Session 中的字段名是：

- worker.status：Worker 实例的实时运行状态，属于内存运行时事实。
- session.last_legal_worker_state：持久化的最后一次合法 Worker 状态。

当前合法状态集合包含：

- queued
- running
- done
- error
- cancelled
- idle
- restarting
- held
- offline

当前 Exit 路径大致是：

```text
POST /api/main/exit
  -> 创建 operation=exit 的 durable Job
  -> worker.begin_shutdown()
  -> _perform_main_exit()
  -> worker.shutdown_all(mark_legal_offline=True)
  -> 记录 Worker 合法 offline
  -> detached exit supervisor
  -> stop_pan.bat
  -> Job=offline
```

当前 Restart 路径大致是：

```text
POST /api/main/restart
  -> 创建 operation=restart 的 durable Job
  -> 直接启动 detached restart supervisor
  -> stop_pan.bat
  -> Job=stopped
  -> start_pan.bat
  -> 检查新 PID、监听端口和 /api/health
  -> Job=ready
```

当前 Restart 路径没有先执行 Exit 路径中的 begin_shutdown() 和 shutdown_all(mark_legal_offline=True)。因此旧服务停止时，Worker 可能被服务进程树一起终止，但 Session 的 session.last_legal_worker_state 仍保留为旧的 running 或 idle，其含义取决于之后是否还有恢复逻辑介入。

当前 main_lifecycle.run_supervisor() 已经根据 Job 的 operation 把 Exit 交给 run_exit_supervisor()；Restart 与 Exit 也已经共享旧服务身份检查、停止脚本和监听端口等待等部分逻辑。这说明“公共停止阶段 + 操作差异分支”的方向与现有结构兼容。

## 3. 术语和状态真源

### 3.1 Worker 实时状态

Worker 实时状态表示当前 Pan 进程内是否还有对应的运行时对象和 provider 进程。它只能用于当前进程存活期间的实时展示和控制，不能跨服务重启直接作为恢复依据。

服务退出后，Worker 对象和实时状态都会消失。不能因为 Session 的持久化字段仍为 running，就对外宣称 Worker 仍然运行。

### 3.2 last_legal_worker_state

last_legal_worker_state 表示 Session 最近一次通过 Pan 明确执行成功的合法状态迁移。

它的默认语义仍然是“最后已确认的合法状态”，不是进程存活探针，也不是单独的恢复命令。

在特殊策略下保留 running，表示：

> Worker 被 Pan 有意终止前，最后被确认处于 running；本次退出策略要求不要把它改写成 offline。

这个语义必须在文档、API 和 UI 中与“当前 Worker 正在运行”区分开。

### 3.3 resume_intent

为了支持未来的自动 continue，建议新增独立的持久化恢复意图，例如：

```text
resume_intent = none
resume_intent = continue_running_on_next_start
resume_intent = recover_durable_queue
```

last_legal_worker_state 是事实记录，resume_intent 是未来动作意图。不能只依赖 last_legal_worker_state == "running" 推断下一次启动必须继续，因为：

- 旧版本可能留下 stale running；
- Worker 可能是异常崩溃而不是受控退出；
- 进程被外部杀死时不一定有合法生命周期 Job；
- 仅有状态值不能指出具体要恢复哪个 in-flight task；
- 队列交接已经越过 sent_to_cli 边界时，不能凭状态盲目重放。

本设计允许第一阶段先保持 last_legal_worker_state=running 的兼容语义，但必须为后续增加 resume_intent、任务快照和生命周期 generation 留出位置。

### 3.4 生命周期 Job

服务生命周期 Job 是服务级操作的持久事实，负责记录：

- 这次操作是 exit 还是 restart；
- 采用了什么退出策略；
- 旧服务和新服务的身份；
- Worker 停止屏障是否完成；
- 服务当前处于哪个阶段；
- 停止后是否需要 start；
- 失败、超时和恢复信息。

Job 是跨 HTTP 请求、旧 Pan 进程和 detached supervisor 的共同真源。任何需要在旧服务退出后继续执行的参数，都必须写进 Job，不能只存在 Python 内存或前端状态中。

## 4. 设计原则

### 4.1 外部语义清晰，内部逻辑复用

外部保留：

- /api/main/exit：停止后保持 offline。
- /api/main/restart：停止后启动新服务。

内部允许：

- exit(plan=ExitPlan.terminal())
- exit(plan=ExitPlan.restart())
- exit(plan=ExitPlan.preserve_running())

这里的内部 exit 表示“执行服务停止阶段”，不等于最终 Job 一定进入 offline。

### 4.2 共性流程只实现一次

以下逻辑必须位于公共退出引擎：

- 解析并冻结策略；
- 创建 durable Job；
- 关闭新的 Worker spawn、recovery 和相关队列入口；
- 排空或停止 Worker；
- flush 必须保存的历史；
- 等待 tracked runtime 确认停止；
- 根据策略完成合法状态收尾；
- 校验旧服务身份；
- 运行安全的服务停止；
- 持久化每个阶段和错误。

只有停止完成之后的动作允许分支：

- 保持 offline；
- 启动新服务；
- 将来执行其他受限的后置动作。

### 4.3 不使用布尔参数组合爆炸

keep_worker_running 和 exit_for_restart 可以作为早期需求的概念，但不应长期扩展成一组互相独立的布尔参数。例如下面的接口会快速变得难以验证：

```python
exit(
    keep_worker_running=True,
    exit_for_restart=True,
    skip_history_flush=False,
    force_stop=True,
    continue_queue=True,
)
```

推荐使用枚举和策略对象。策略对象能够表达合法组合，也能在入口处拒绝冲突组合。

### 4.4 所有跨进程行为必须可持久化

post_stop_action=start、resume_intent=continue_running_on_next_start 等行为必须在服务停止前写入 Job 或 Session。

前端收到 scheduled 后断开、旧 Pan 进程退出、supervisor 重新启动，都不能使策略丢失。

### 4.5 受控死亡和异常死亡必须区分

只有明确进入 ExitPlan 的受控关停，才允许根据策略更新合法状态。

EOF、provider 崩溃、watchdog 回收或未知 PID 消失，不自动调用合法 offline 记录。观察到进程消失不是“Pan 合法停止了 Worker”的证据。

## 5. ExitPlan 设计

### 5.1 推荐的数据结构

以下是概念接口，不要求第一阶段原样落地：

```python
class ServiceAfterStop(str, Enum):
    OFFLINE = "offline"
    START = "start"


class WorkerStopPolicy(str, Enum):
    MARK_OFFLINE = "mark_offline"
    PRESERVE_LAST_LEGAL = "preserve_last_legal"


class ResumeIntent(str, Enum):
    NONE = "none"
    RECOVER_DURABLE_QUEUE = "recover_durable_queue"
    CONTINUE_RUNNING_ON_NEXT_START = "continue_running_on_next_start"


@dataclass(frozen=True)
class ExitPlan:
    operation: Literal["exit", "restart"]
    post_stop_action: ServiceAfterStop
    worker_stop_policy: WorkerStopPolicy
    preserve_states: frozenset[str]
    resume_intent: ResumeIntent
    reason: str
    schema_version: int = 1
```

推荐保留的正交维度：

| 字段 | 作用 |
|---|---|
| operation | 记录外部语义和 Job 类型，例如 exit 或 restart |
| post_stop_action | 服务停止后保持 offline，还是进入 start |
| worker_stop_policy | Worker 停止后写 offline，还是保留最后合法状态 |
| preserve_states | 哪些停止前状态允许保留，例如只保留 running |
| resume_intent | 下一次启动是否需要恢复，以及恢复类型 |
| reason | 审计和日志来源，例如 main-exit、main-restart |
| schema_version | 未来策略字段演进和兼容读取 |

不建议把任意 Python callback、shell 命令或不可审计动作放进 Job 的策略对象。跨进程后置行为必须来自固定的白名单枚举。

### 5.2 预置策略

#### 普通 Exit

```text
operation = exit
post_stop_action = offline
worker_stop_policy = mark_offline
preserve_states = {}
resume_intent = none
reason = main-exit
```

这是当前 Exit 的默认语义：Worker 确认停止后记录 offline，服务停止且不再自动启动。

#### 普通 Restart

```text
operation = restart
post_stop_action = start
worker_stop_policy = mark_offline
preserve_states = {}
resume_intent = recover_durable_queue
reason = main-restart
```

普通 Restart 仍然要先受控停止 Worker，再停止旧服务。Worker 的实际进程已经停止，因此记录“合法停止”是准确的；新的 Pan 进程通过持久队列和已有 Session 机制恢复可恢复工作。

如果未来需要让 Restart 尽可能继续中断中的 running task，应显式选择另一个策略，不应偷偷改变普通 Restart 的语义。

#### 保留 running 的特殊 Exit

```text
operation = exit
post_stop_action = offline
worker_stop_policy = preserve_last_legal
preserve_states = {"running"}
resume_intent = continue_running_on_next_start
reason = main-exit-preserve-running
```

行为是：

1. 记录哪些 Worker 在策略解析时处于 running；
2. 关闭 Worker 的消费和恢复入口；
3. flush 必须保存的历史；
4. 杀掉 Worker 进程树；
5. 确认 runtime 已停止；
6. 对目标 running Worker 不写入 offline，使其已有的 last_legal_worker_state=running 保留；
7. 服务最终保持 offline；
8. 将恢复意图持久化，供未来的下一次启动实现 continue。

“保留 running”不表示不 kill，也不表示退出后 Worker 仍然运行。它表示“保留退出前的合法运行状态和恢复意图”。

#### 保留 running 的 Restart

```text
operation = restart
post_stop_action = start
worker_stop_policy = preserve_last_legal
preserve_states = {"running"}
resume_intent = continue_running_on_next_start
reason = main-restart-preserve-running
```

这是“受控停止 + 立即启动 + 将来继续”的组合。它应当是显式策略，不应由 post_stop_action=start 自动推导出 preserve_last_legal。

### 5.3 需求字段到策略字段的映射

如果早期实现需要兼容用户提出的两个布尔概念，可以只在入口映射一次：

| 早期概念 | 策略对象中的正式语义 |
|---|---|
| keep_worker_running=true | worker_stop_policy=preserve_last_legal，并按 preserve_states 限定目标 |
| exit_for_restart=true | post_stop_action=start，同时 operation=restart |

keep_worker_running 不应继续作为正式名称，因为实际 Worker 会被杀掉。推荐名称是 preserve_last_legal 或 preserve_resume_state。

exit_for_restart 也不是一个独立的最终语义；它只是把停止后的动作设置为 start。正式 API 应使用 operation=restart 或 post_stop_action=start。

## 6. 统一退出流程

### 6.1 入口阶段：解析和冻结策略

所有服务级退出入口执行同一套顺序：

```text
1. 检查当前 checkout、端口和服务生命周期是否可操作
2. 检查是否已有 active lifecycle Job
3. 将请求参数解析为 ExitPlan
4. 校验策略组合是否有效
5. 记录旧 Pan PID 和创建时间
6. 原子创建包含 ExitPlan 的 durable Job
7. 关闭新的 Worker spawn/recovery gate
8. 建立 detached supervisor
9. 进入 Worker 停止阶段
```

策略必须在第 3 步冻结。后续流程不再读取前端参数、全局可变布尔值或重复推断 operation。

### 6.2 Worker 停止阶段

公共 Worker 停止阶段应覆盖现有 begin_shutdown() 和 shutdown_all() 的职责：

```text
prepare_workers(plan):
  1. 设置服务级 shutdown gate
  2. 停止 global watchdog 和新的 recovery
  3. 记录停止前 Worker 快照
  4. 停止 consumer/stdout/watchdog/interrupt task
  5. flush 防抖中的历史块
  6. 处理 queue retry 和 recovery task
  7. 杀掉每个 Worker 的完整进程树
  8. 等待 _runtime_stopped
  9. 按 plan.worker_stop_policy 完成状态收尾
  10. 持久化 workers_stopped 屏障
```

begin_shutdown() 应保持为快速、同步的闸门操作；真正的 Worker 排空和进程停止可以继续异步执行，但必须在 supervisor 运行 stop_pan.bat 之前完成，或者由 supervisor 等待持久化的 workers_stopped 屏障。

### 6.3 Windows supervisor 屏障

当前 Windows 设计要求 supervisor 在旧 Pan 进程树被杀之前建立。统一后的推荐流程是：

```text
old Pan process
    |
    | 1. 持久化 Job + ExitPlan
    | 2. begin_shutdown()
    | 3. 启动 detached supervisor
    | 4. supervisor 等待 worker-stop barrier
    | 5. old Pan 进程完成 Worker 停止并写入 barrier
    v
detached supervisor
    |
    | 6. 校验旧服务 PID identity
    | 7. 运行 stop_pan.bat
    | 8. 等待旧 listener 和旧 PID 消失
    | 9. operation=exit -> offline
    | 9. operation=restart -> stopped -> starting
    v
new Pan process（仅 restart）
```

这样既不会因为在 HTTP handler 中等待整个 Restart 而导致请求失败，也不会让 supervisor 在 Worker 状态尚未持久化时提前杀掉旧服务。

如果第一阶段暂时不引入独立的 barrier phase，至少必须让 Restart 先走和 Exit 相同的 Worker 停止协程，并在启动 supervisor 前持久化一个等价的 workers_stopped=true 标记。

### 6.4 服务停止和停止后分支

公共服务停止校验包括：

- 旧 PID 存在；
- 旧 PID 创建时间匹配；
- 命令行或 cwd 属于目标 checkout；
- 命令行包含 Pan entry marker；
- stop_pan.bat 只作用于已确认的目标进程树；
- 目标监听端口消失；
- 旧服务身份不再有效。

停止成功后：

```text
if plan.post_stop_action == OFFLINE:
    Job.phase = offline
    不启动新服务

if plan.post_stop_action == START:
    Job.phase = stopped
    启动 start_pan.bat 或 launcher
    Job.phase = starting
    等待新 listener、new PID identity 和 HTTP readiness
    Job.phase = ready
```

run_exit_supervisor() 和 Restart supervisor 可以共享“验证旧服务、执行 stop、等待旧服务消失”的底层函数；不应让 Restart 直接调用会把 Job 固定为 offline 的完整 Exit supervisor。

## 7. Worker 合法状态收尾

### 7.1 统一 finalize_worker_stop

目前 Worker 停止后写入 offline 的逻辑分布在 shutdown_all()、_kill_worker() 或其他生命周期路径中。目标设计应提供统一的收尾原语：

```python
async def finalize_worker_stop(
    worker: Worker,
    *,
    plan: ExitPlan,
    stop_confirmed: bool,
) -> None:
    ...
```

它的规则是：

1. stop_confirmed 为 false 时，不能写合法 offline。
2. 普通 MARK_OFFLINE 策略在 runtime 确认结束后记录 offline。
3. PRESERVE_LAST_LEGAL 策略不把目标状态改写为 offline。
4. preserve_states 只对策略指定的停止前状态生效。
5. 每次写入都带明确 source，例如 pan/main-exit、pan/main-restart 或 pan/main-exit-preserve-running。
6. 统一记录 requestId、jobId、workerId、sessionId 和 generation，便于诊断和避免旧 Worker 事件污染新 Worker。

_kill_worker() 的普通调用仍应保留原有默认行为；只有从服务级 ExitPlan 进入时，才传入特殊的 WorkerStopPolicy。这样不会把“手动 kill”“watchdog 回收”“Worker 级 restart”和“主服务退出”混成同一语义。

### 7.2 “保留 running”精确定义

在策略解析阶段建立停止前快照：

```text
{
  sessionId,
  workerId,
  generation,
  liveStatus,
  lastLegalWorkerState,
  preserveRequested
}
```

当 preserve_states={"running"} 时，只有停止前实时状态确认为 running 的 Worker 才进入保留集合。

对于：

- idle Worker：不因 preserve-running 自动伪装为 running；
- queued Session：保留持久队列，不把 queued 改写成 running；
- held Worker：除非策略显式允许，否则不自动继续；
- error/zombie Worker：不因本策略自动恢复；
- 无活 Worker：不执行 Worker kill，但可以根据 Session 的持久队列另行恢复。

如果停止前实时状态是 running，但 last_legal_worker_state 已经不是 running，第一阶段不应静默伪造事实。应记录状态不一致并写入独立的 resume snapshot；后续恢复协议再决定是否将其恢复为 running。

### 7.3 不把运行时状态伪装成 running

服务停止后，前端或 API 不能因为 last_legal_worker_state=running 就显示“Worker 正在运行”。建议 UI 和 API 同时展示：

- Pan 服务状态：offline、stopping、starting、ready；
- Worker live status：当前服务有活 Worker 时才有意义；
- last legal state：最近一次合法状态；
- resume intent：是否存在待恢复意图。

在没有新增字段的过渡期，至少使用服务级 Job phase 覆盖 Worker live 展示，避免服务 offline 时显示真实运行中的 Worker。

## 8. 恢复意图和队列边界

### 8.1 保留状态不等于已经实现 continue

本方案只定义如何正确表达“下次应尝试继续”，不等于第一阶段就实现自动 continue。

下一次启动真正恢复 running Worker 至少需要：

- Session ID；
- 原 CLI session / transcript 标识；
- Worker generation；
- 被终止时的 in-flight task；
- 任务最后可靠持久化位置；
- resume intent；
- 对 provider 重连或 CLI resume 的具体协议。

没有这些信息，仅把 last_legal_worker_state 留在 running，会导致恢复时不知道恢复哪一个任务，或者重复执行。

### 8.2 queue_pending 仍然是持久真源

退出策略不能把 queue_pending 当成内存队列清空。服务停止时：

- 尚未交接给 CLI 的队列项继续保留；
- 已越过本地 CLI 交接边界的项遵守现有 at-most-once 语义；
- queue_delivery_ledger 继续作为交接收据，不得被重启逻辑当成第二条队列；
- 新服务启动后根据现有恢复规则重建 pending signal 和 consumer。

如果未来要恢复一个已经越过 sent_to_cli 边界但没有 provider 终态的 in-flight task，需要新增明确的 in-flight recovery ledger，不能通过“看到 running 就重新发送”实现。

### 8.3 恢复意图的清除时机

resume_intent 必须在以下条件全部满足后才清除：

1. 新服务已经达到 ready；
2. 对应 Session 已创建新的 Worker generation；
3. 恢复动作已经被持久化接受；
4. 新 Worker 已进入可观察的 queued、running 或 idle 状态；
5. 对应的恢复 Job 或任务快照已经有明确结果。

启动失败、Worker spawn 失败或恢复信息不完整时，恢复意图应保留并进入可诊断的 error/held 状态，不能无声丢失。

## 9. Durable Job 和状态机

### 9.1 建议的 Job 字段

现有服务 Job 字段继续保留，并增加或等价表达以下字段：

```json
{
  "jobId": "job_...",
  "kind": "service_lifecycle",
  "operation": "exit",
  "requestId": "...",
  "policyVersion": 1,
  "postStopAction": "offline",
  "workerStopPolicy": "preserve_last_legal",
  "preserveStates": ["running"],
  "resumeIntent": "continue_running_on_next_start",
  "reason": "main-exit-preserve-running",
  "phase": "stopping_workers",
  "workerStopCompleted": false,
  "root": "...",
  "port": 8768,
  "oldPid": 1234,
  "oldPidCreatedAt": 0,
  "newPid": null,
  "newPidCreatedAt": null,
  "error": null
}
```

字段的正式命名可以在实现阶段与现有 snake_case / API camelCase 映射统一，但语义必须保持一致。

不建议把每个 Worker 的完整历史写进 Job Registry。可以在 Job 中保存最小快照和摘要，详细的 Session 状态仍写入 Session metadata 或专用 recovery ledger。

### 9.2 建议的阶段

公共阶段：

```text
requested
  -> preparing_workers
  -> stopping_workers
  -> workers_stopped
  -> stopping_service
  -> stopped
```

Exit 分支：

```text
stopped
  -> offline
```

Restart 分支：

```text
stopped
  -> starting
  -> ready
```

失败和超时可以从每个可中断阶段进入 failed 或 timed_out，但不能把失败伪装成 offline 或 ready。

workers_stopped 是推荐新增的明确屏障。若暂时不新增阶段，至少要在 stopping_workers 阶段记录 workerStopCompleted，并禁止 supervisor 在该标记完成前运行 stop_pan.bat。

### 9.3 状态迁移不变量

必须保证：

1. ready 只能从 Restart 的 starting 进入。
2. Exit 不允许从 offline 重新进入 starting。
3. Restart 的 starting 之前必须确认旧 listener 和旧服务身份都消失。
4. workers_stopped 之前不能报告 Worker 关停完成。
5. last_legal_worker_state=offline 只有在受控停止且 runtime 确认结束后才能写入。
6. resume_intent 在新 Worker 恢复成功前不能清除。
7. 已有 active Job 时，新的 Exit 或 Restart 必须返回 busy，而不是创建第二个 supervisor。
8. 同一个 requestId/jobId 的重复调用必须幂等。

## 10. 策略合并和 OR 规则

### 10.1 为什么不能在各节点取 OR

把所有位置都写成：

```python
keep = request.keep_worker_running or inherited_keep or ...
```

短期看似方便，长期会产生三个问题：

- 某个调用路径忘记传参数；
- 不同节点对 inherited_keep 的来源理解不同；
- 一个节点已经做出不可逆动作后，另一个节点才发现策略应当保留。

尤其是 kill、flush、record offline、start 这些动作发生顺序不同，局部 OR 不能保证全局一致。

### 10.2 推荐的合并位置

在入口阶段使用单一 resolver：

```python
plan = resolve_exit_plan(
    operation=request.operation,
    explicit_options=request.options,
    inherited_requirements=system_requirements,
)
validate_exit_plan(plan)
persist_plan(job, plan)
```

之后所有函数接收同一个 plan：

```python
await prepare_workers(plan)
await finalize_worker_stop(worker, plan=plan, stop_confirmed=True)
await run_service_stop(plan)
await run_post_stop_action(plan)
```

### 10.3 如果确实有多个来源提出 preserve

多个来源的需求可以在 resolver 内进行集合合并，例如：

```python
preserve_states = frozenset().union(
    request.preserve_states,
    operation_defaults.preserve_states,
    system_requirements.preserve_states,
)
```

但合并后必须再次校验：

- 是否把不允许自动恢复的 held 或 error 包含进来；
- worker_stop_policy 与 resume_intent 是否匹配；
- post_stop_action 是否与 operation 一致；
- 是否存在需要人工确认的危险组合。

合并完成后下游不再继续 OR。

## 11. 模块职责

### 11.1 packages/web/server.py

负责：

- 校验请求和当前服务状态；
- 把 /exit、/restart 映射为预置 ExitPlan；
- 创建 durable Job；
- 快速关闭新的 Worker spawn/recovery gate；
- 启动公共退出协调任务或 detached supervisor；
- 返回 scheduled、busy、failed 等 API 结果。

不负责：

- 在 endpoint 内直接杀掉自己并等待重启完成；
- 在多个 API 分支中重复实现 Worker 停止；
- 依赖前端轮询作为生命周期真源。

### 11.2 packages/core/worker.py

负责：

- 接收 ExitPlan 的 WorkerStopPolicy；
- 关闭 Worker consumer、watchdog、recovery 和相关 task；
- flush 历史；
- 安全杀进程树；
- 确认 _runtime_stopped；
- 通过统一的 finalize_worker_stop 写入或保留合法状态；
- 维护 Worker generation 和队列恢复边界。

不负责：

- 决定服务停止后是否启动 Pan；
- 解析 Windows BAT/PowerShell；
- 自行解释一个散落的全局 keep_worker_running 布尔值。

### 11.3 packages/core/main_lifecycle.py

负责：

- 读取 durable Job 和 ExitPlan；
- 管理 Worker stop barrier；
- 复用旧服务身份验证、stop、listener wait；
- 根据 post_stop_action 进入 offline 或 starting；
- 启动后检查新 PID、创建时间、checkout 身份和 HTTP readiness；
- 持久化失败、超时和最终状态。

不负责：

- 直接访问或伪造 Worker 的内存状态；
- 在没有 Job 策略的情况下猜测是否应当恢复。

### 11.4 packages/core/background_jobs.py

负责：

- 原子创建和更新服务生命周期 Job；
- 持久化策略字段；
- 执行 phase transition 合法性校验；
- 通过 checkout+port 防止并发服务生命周期操作；
- 支持旧 Job 的读取兼容和新 Job 的 schema version。

### 11.5 packages/core/launcher.py 和 Windows 脚本

继续遵守《Pan 启动器与 Windows 生命周期架构》的分层：

- launcher 负责可复用的 start/stop/status 业务；
- main_lifecycle 负责 durable lifecycle 和跨进程监督；
- BAT/PowerShell 负责 Windows 兼容、进程脱离和低层探测；
- start_pan.bat 不重新承载策略解析；
- stop_pan.bat 不负责 Session 或 Worker 合法状态。

## 12. API 和前端契约

### 12.1 对外操作

现有端点保持：

```text
GET  /api/main/exit/status
POST /api/main/exit

GET  /api/main/restart/status
POST /api/main/restart
```

/exit 默认使用 terminal plan，/restart 默认使用 restart plan。外部不需要知道内部是同一个退出引擎。

### 12.2 特殊策略是否开放给 API

第一阶段建议只开放预置操作，不开放任意组合：

- exit；
- restart。

后续如果确实需要用户触发“保留 running 的 Exit”，应使用显式、可审计的策略名，例如：

```text
POST /api/main/exit
{
  "policy": "preserve-running-for-next-start"
}
```

不建议直接公开一组可以自由拼接的布尔参数。任何新增策略都应：

- 有固定名称；
- 有明确的合法组合；
- 写入 Job；
- 在 status API 中返回；
- 有对应的权限和危险操作提示；
- 有测试和恢复说明。

### 12.3 status 返回建议

生命周期状态至少应能返回：

```json
{
  "available": true,
  "pending": true,
  "operation": "restart",
  "phase": "stopping_workers",
  "postStopAction": "start",
  "workerStopPolicy": "mark_offline",
  "resumeIntent": "recover_durable_queue",
  "jobId": "job_...",
  "requestId": "...",
  "terminal": false,
  "error": null
}
```

前端只负责显示服务阶段和等待恢复。它不能通过取消浏览器轮询来取消已经持久化的生命周期 Job。

## 13. 异常、超时和并发

### 13.1 Worker 停止失败

如果某个 Worker 停止异常：

1. 记录具体 Session、Worker、generation 和错误；
2. 不把未确认停止的 Worker 记为合法 offline；
3. 继续或中止服务停止必须由策略定义，不能由异常处理路径自行决定；
4. 如果 Windows supervisor 最终强制停止整个 Pan 进程树，Job 必须保留“Worker 状态未完全确认”的事实；
5. Restart 启动后不能把未确认状态直接当成已成功恢复。

现有 Exit 的“Worker shutdown 出错后仍交给 service supervisor 收尾”可以保留为 best-effort 行为，但必须持久化错误，不能让最终 offline 掩盖 Worker 阶段失败。

### 13.2 服务停止失败

如果旧服务身份不匹配、stop_pan.bat 失败、listener 未消失或超时：

- Job 进入 failed 或 timed_out；
- 不得报告 offline 或 ready；
- 不得在旧服务仍可能存活时启动第二个服务；
- 不能按未知 PID 强杀；
- 需要保留日志路径和最后检查结果。

### 13.3 Restart 启动失败

如果旧服务已停止但新服务未 ready：

- Job 保持 failed 或 timed_out；
- 不得重复启动多个 start supervisor；
- resume_intent 和未完成队列不得静默删除；
- 下一次手动启动或恢复流程可以继续处理；
- status API 应明确区分“旧服务已停”和“新服务已 ready”。

### 13.4 并发操作

同一 checkout+port 同时只允许一个服务级生命周期 Job。

以下操作必须被拒绝或排队到生命周期完成后：

- 第二个 main Exit；
- 第二个 main Restart；
- Exit 与 Restart 并发；
- 启动脚本发现同一 checkout 已有合法服务；
- 旧 supervisor 和新 supervisor 对同一 Job 重复执行。

Session 级 Worker restart 与主服务级 Restart 是不同层次，但主服务进入 shutdown gate 后，Session 级新 spawn/restart 必须被拒绝或持久化为待处理请求，不能穿透服务级策略。

## 14. 行为矩阵

| 场景 | 是否 kill Worker | 合法状态处理 | 服务停止后 | 恢复意图 |
|---|---:|---|---|---|
| 普通 Exit | 是 | 已确认停止后写 offline | 保持 offline | none |
| 普通 Restart | 是 | 已确认停止后写 offline | start，ready 后恢复 durable queue | recover_durable_queue |
| 特殊 Exit：保留 running | 是，仅对 live running 保留 | 不改写目标 Worker 的 running | 保持 offline | continue_running_on_next_start |
| 特殊 Restart：保留 running | 是，仅对 live running 保留 | 不改写目标 Worker 的 running | start，ready 后恢复 | continue_running_on_next_start |
| Worker 级 restart | 按 Worker 操作 | 默认沿用 Worker 级 restart 语义 | 不影响 Pan 主服务 | 由 Worker 级协议决定 |
| EOF/zombie/watchdog | 可能 | 不自动写合法 offline | 不改变主服务状态 | 按异常恢复协议 |
| 服务外部崩溃 | 可能 | 不得伪造合法停止 | 由启动/reconcile 处理 | 由 Job、队列和异常恢复决定 |

## 15. 测试和验收标准

### 15.1 策略解析和组合测试

- 默认 /exit 得到 terminal plan；
- 默认 /restart 得到 restart plan；
- preserve-running-for-next-start 得到 preserve plan；
- post_stop_action=start 必须对应可恢复的 restart 语义；
- MARK_OFFLINE 与 continue_running_on_next_start 的冲突组合被拒绝，或有明确的兼容定义；
- preserve states 只包含允许的合法状态；
- 多个 preserve 来源只在 resolver 中合并一次；
- 下游执行阶段不再读取原始布尔参数。

### 15.2 Worker 状态测试

- 普通 Exit 在 runtime 确认停止后写 last_legal_worker_state=offline；
- 特殊 preserve-running Exit 杀掉目标 Worker 后不写 offline；
- preserve-running 只对停止前 live status 为 running 的 Worker 生效；
- idle、held、error、zombie 不被误标为 running；
- Worker 停止未确认时不写合法 offline；
- _kill_worker()、shutdown_all()、respawn 和服务级停止都经过统一策略收尾；
- 普通手动 kill、watchdog 回收和 Worker 级 restart 的既有语义不被主服务策略污染。

### 15.3 Durable Job 测试

- Job 在 Worker 停止前已经持久化完整策略；
- 旧服务进程退出后，detached supervisor 可以只依赖 Job 继续执行；
- 重启服务后可以读到未完成 Job 和策略；
- phase transition 拒绝非法跳转；
- 同一 Job 重复执行不会重复 start；
- active Job 防止第二个 Exit/Restart；
- Job 失败时保留错误、日志、PID 身份和恢复意图。

### 15.4 Exit/Restart 流程测试

- Exit 经过 Worker stop barrier 后进入 offline，不调用 start；
- Restart 经过 Worker stop barrier 后进入 stopped、starting、ready；
- Restart 不能在旧 listener 消失前启动新服务；
- 新 listener PID 不等于旧 PID；
- 新 PID 的创建时间、checkout root marker、entry marker 和 /api/health 均有效；
- stop/start 脚本失败时不会报告成功；
- HTTP 请求在服务停止前返回 scheduled，浏览器断开不影响 Job；
- 前端取消恢复轮询不会取消服务级 Restart。

### 15.5 Windows 隔离 E2E

使用独立 checkout 和 8767/8765 等测试端口，保持 8768 practical 实例不受影响：

- 启动目标 checkout；
- 创建至少一个 running Worker 和一个 idle/queued Session；
- 执行普通 Exit、普通 Restart、特殊 preserve-running Exit 的独立测试；
- 观察真实进程树、监听端口、Job 文件和 Session metadata；
- 验证目标 Worker 进程被杀且不产生孤儿；
- 验证 last_legal_worker_state 与服务 phase 的组合语义；
- 验证 Restart 的旧 PID/new PID 和 readiness；
- 验证其他 checkout、8768、QQ、Tunnel 未受影响。

静态测试、mock supervisor 和 jsdom 测试不能替代真实 Windows 进程树验证。自动 continue provider E2E 属于后续阶段，未实现前不得宣称已经验证。

## 16. 分阶段实施建议

### 阶段一：建立策略契约

- 新增 ExitPlan、枚举和 resolver；
- 保留现有 /exit、/restart 对外接口；
- 将两个入口映射到预置策略；
- 将策略写入 durable Job；
- 为策略解析和 Job schema 增加单元测试。

### 阶段二：统一 Worker 停止收尾

- 把 mark_legal_offline 的布尔语义收敛到 WorkerStopPolicy；
- 提取 finalize_worker_stop；
- 让 shutdown_all()、服务级 kill 和相关停止路径接收同一个 plan；
- 保留兼容参数，但内部尽快转换为策略对象；
- 为 preserve-running 增加状态快照和审计日志。

### 阶段三：让 Restart 进入公共 Exit 停止阶段

- Restart 请求先关闭 spawn/recovery gate；
- 在 detached supervisor 停止旧服务前完成 Worker 停止屏障；
- 普通 Restart 默认使用 mark-offline + durable queue recovery；
- 保持 Exit 的 terminal offline 和 Restart 的 start 分支差异。

### 阶段四：统一 service supervisor

- 提取旧服务身份验证、stop 脚本、listener wait 等公共函数；
- 用 Job operation 和 postStopAction 驱动后续分支；
- 为 supervisor 增加对 worker-stop barrier 的等待；
- 保证 Windows 进程树隔离和 durable Job 恢复。

### 阶段五：实现 resume intent

- 新增 Session 或 recovery ledger 中的 resume intent；
- 固化 Worker/Session/task/generation 快照；
- 定义 provider/CLI resume 方式；
- 新服务 ready 后按 intent 恢复；
- 只有恢复动作完成后清除 intent；
- 增加真实 Windows 和 provider E2E。

自动 continue 不应与第一阶段的 Exit/Restart 策略重构强绑定。第一阶段可以先正确记录意图，等恢复协议明确后再启用实际继续。

## 17. 未决问题

以下问题在实现前需要单独确定：

1. 普通 Restart 是否始终把已停止的 Worker 记为 offline，还是将部分 active Worker 直接纳入 preserve-running 策略。
2. preserve-running 时，last_legal_worker_state 是否只保留已有 running 值，还是允许受控地写入 running；推荐优先增加 resume snapshot，避免伪造事实。
3. resume_intent 放在 Session metadata、service Job，还是单独的 recovery ledger；推荐 Session 记录 Session 级意图，Job 记录本次服务级策略。
4. Worker 停止失败时，Exit 是否继续强制停止服务；推荐继续执行安全的服务收尾，但把 Worker 未确认状态作为 Job 错误事实保留。
5. 服务级 Restart 是否需要恢复所有 running Worker，还是只恢复有 durable queue/in-flight snapshot 的 Worker。
6. 前端在服务 offline 期间如何显示“最后合法状态为 running，但当前 Worker 不存在”；推荐使用服务 phase 覆盖实时运行展示。

这些问题不改变本文的核心结论：共性逻辑应由统一退出引擎执行，差异行为应由入口处冻结并持久化的策略对象决定。

## 18. 最终结论

“Restart 就是带参数的 Exit + Start”可以作为 Pan 的内部生命周期模型，并且适合未来持续加入特殊退出行为。

推荐的正式表达是：

```text
restart
  = exit(ExitPlan(
      post_stop_action="start",
      worker_stop_policy=...,
      resume_intent=...,
    ))
    + start/recovery
```

但这里的 Exit 不是当前仅代表“终态 offline”的完整 API，而是一个可复用的服务停止阶段。真正的终态由 post_stop_action 和生命周期 Job phase 决定。

最重要的实现约束是：

- 公共逻辑只实现一次；
- 策略在入口解析一次；
- 策略必须持久化；
- 不在各处散落 OR 判断；
- last_legal_worker_state 不等于 live Worker 状态；
- preserve-running 必须区分“杀掉 Worker”与“保留恢复意图”；
- Exit 不启动，Restart 才启动；
- 任何跨进程后置行为都由 durable Job 驱动；
- 未实现自动 continue 前，不把保留 running 宣称为已经具备自动恢复能力。

这样既能复用 Exit 的 Worker 合法状态管理，又能保持 Exit 和 Restart 的外部语义清晰，并为未来的特殊附带退出行为提供一个可验证、可扩展的承载位置。
