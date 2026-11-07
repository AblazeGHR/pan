# Action 统一 P4：Job 创建、未来 Run 引用更新、Trigger 双目标

任务：`ACTION-UNIFICATION-20261104-P4` revision 3。基线：`e27fa8a850da4bc9d27a321deb36f8034730103a`。
分支：`implement/action-unification-p4-20261106`。实现限定 P4 worktree；P3 Group 实现不在本片。

## Job 创建命令

`job.create` 使用 P1 不可变 ActionRef、P2 `invoke` operation 及原 canonical Job 存储。
产品描述符默认 disabled，启用沿用现有 system Action 管理权限；用户自定义描述符无需新授权位。
`execution.config` 为 `{jobDefinition, start?: boolean}`；完整 Job 定义支持递归的
`{"$argument":"path.to.value"}` 整值参数绑定，参数来自 Action inputSchema/defaults。
输入经过 schema 校验，生成的完整 Job 经过 canonical definition/reference 校验。没有专门的嵌套创建检测或限制。

创建、原 operation 回执和 provenance 在同一 owner-fenced SQLite 事务提交。
同 principal/profile/invoke/requestKey 的异参请求冲突；丢响应后查询原 operation 或同键查询，返回原 Job。
默认只创建 disabled Job；`start:true` 另查 job.lifecycle/execute，在保存点内启用和接纳原 Run。
创建完成与 start accepted/rejected 分字段返回。start 被拒绝仍保留原 disabled Job；不会把后台 Run 成功冒充为创建成功。
之后的执行、取消、恢复、通知均由原 Job driver/ledger 负责。

Job Step 使用同一创建 helper，原 effectKey 派生稳定 operation requestKey；不创建第二份 Run/Attempt/effect ledger。
Job 创建的 Job 可以继续包含创建 Job 的 Step。Job adapter 沿用 job.execute、system gate，不增加 action.invoke 授权要求。

来源存放原 Job aggregate 的 `provenance`，不是第二份可执行定义：ActionRef、operationId、requestKey、
argumentsDigest、configDigest，以及受信来源（Event/Trigger/Run/effectKey 或 Group release）。不记录明文参数副本。
`GET /api/jobs/{jobId}/provenance` 沿用 job.read。
P3 可通过内部 `FinalService.invoke_action(..., source={groupRelease: ...})` 传实际固定 release；
HTTP body 不接收 source，source 不授予任何权限，同键异 source 冲突。

## Job 引用更新接口

- `GET /api/jobs/actions/{actionId}/job-references?cursor=...&limit=...`：原分页策略、action.read + job.read，显示当前 Job/Step 固定引用。
- `POST /api/jobs/action-references/preview`：`{jobs:[{jobId,expectedRevision,steps:[{stepId,actionRef}]}],triggers:[{triggerId,expectedRevision}]}`。
- `POST /api/jobs/action-references/apply`：`{scope:<原 preview 请求>,previewDigest,requestKey}`。

每批显式 1..64 Job、每 Job 至多 32 个 Step、至多 256 个订阅 Trigger；重复选择或范围外订阅者拒绝。
preview 包括实际定义、选定引用前后差异、输入/输出/完成/cancel/reconcile/retry/能力/效果契约、全部订阅者及 blockers。
权限不足直接拒绝整个范围；不缩小范围。apply 在同一 writer 事务重做 preview、授权与所有 revision/digest 校验，
整批更新或整批回滚。原 requestKey 在 CAS 前查询原回执；异参冲突。只改选定 Step 的 ActionRef，保留其余配置和运行记录。

不猜测 JSON Schema 的一般兼容关系：动态输入映射的 inputSchema 改变、outputSchema 改变、已支持的 cancel/reconcile
契约改变、retry/effectClass 或选用 completion 契约改变均阻断。完整 literal 映射由原 reference 校验器检验。
能力用原平台 supports 检查；不存在或不可用明确拒绝。没有新增平台 backend/capability 或 Windows-only 导入。

Job configRevision 加一。legacy targetJobId 订阅者继续读取当前配置；新固定 Job 目标必须显式选择同步，
在同一事务更新其 configRevision 和 Trigger revision；未选、并发编辑、权限不足阻断。Event、游标和去重记录均不改写。

已有 queued/retry_wait/paused_at_boundary/uncertain Run 的整个原计划保持不变。新接纳 Run 使用完整新快照。
新计划附加 `stepActions` 保存每个 Step 的精确版本，避免同 Action ID 的 v1/v2 在一个 Run 内互相覆盖；
旧 frozen plan 保留 actions map 读取兼容。执行门禁仍检查原授权和实际固定版本的启用状态。

## Trigger 双目标与恢复边界

互斥配置：`target:{type:"action",actionRef:{id,revision,digest}}` 或
`target:{type:"job",jobId,configRevision}`；旧 `targetJobId` 继续走同一 Job 分支。
不能同时提交 target 和 targetJobId。定义和对象 schema 均实施互斥约束。
配置变更继续使用原 Trigger definition CRUD/CAS；固定 Action 版本及 Job 配置版本由原 resolver 检查。

Event/Trigger 条件、启用、授权、固定引用和映射通过后，在原 `final_event_operations` 的
`(eventId,triggerId)` 身份关联原 `final_operations` invocation。重复、并发、换 Trigger revision、冷查询不产生第二个调用。
同 Event identity 内容变化拒绝。冻结实际 Trigger revision、定义、参数、事件及授权上下文；实际效果前仍复检当前授权、owner、
Action 版本与 Trigger 启用状态。Group 不参与事件授权。

`phase=admitted` 是尚未开始效果的持久交付，可以在 owner 交接后由原 tick 继续领取。
命令调用在效果前提交 `phase=calling/status=unknown`，再调用 P2 `execute_prepared`/TypedExecutor/ProcessOwner。
该阶段崩溃或失去回执只查询原身份，不被 tick 重选；无证据不自动重放。逐命令输出和 terminal receipt 仍在原 operation。
Job 创建是本地 canonical 事务，创建和回执原子提交，不存在提交了 Job 却缺少原创建结果的事务内窗口。

直接调用无自动效果重试、退避、Run 编排和独立终态通知。Action 创建 Job 的 Trigger 完成于创建结果，
新 Job 的业务终态归新 Job 管。默认 serial、maxPending=64；显式 concurrency.maxActive 为 1..32、maxPending 为 0..256。
原 tick 每轮至多领取 256 个 invocation；并发消费者事务领取，calling/uncertain 占用容量，满容量返回 deferred，原 Event 保留背压。

## P3 整合接口与冲突点

主要逻辑独立在 `packages/actions/job_creation.py`、`packages/jobs/action_references.py`、`packages/jobs/action_trigger.py`。
共享接线：

- `packages/actions/invocation.py`：job.create 分支、可复用 execute_prepared、内部 source provenance、按 Step 精确版本读取。
- `packages/jobs/final_service.py`：系统描述符注册、三个新模块入口、_admit 的 stepActions、冻结读取 helper、Trigger 双目标和 tick。
- `packages/jobs/final_api.py`：引用 discovery/preview/apply 与 provenance 路由及 service Protocol。
- `packages/jobs/final_contracts.py` + 两个 Trigger 生成 schema：双目标、concurrency，object 生成保留 oneOf。
- `runtime_activation.py`、`final_runtime/coordinator.py`、`scheduler/source_migration.py`：新 Job target 的引用/删除保护兼容；
  legacy source migration 仍要求 Job successor，不把 Action 猜成旧 Job successor。

P3 如同时修改 invocation/service/api/contracts，整合时按符号合并，保留其 Group release/auth/pin 接线与本片 source 参数。
不覆盖 P3 Group 文件，不假设 P3 已完成真实 MCP 验证。

## 本地验证与红证据

所有后端测试经 `scripts/run_tests_isolated.py`，使用临时 profile/data；无真实服务或 8767/8768 操作。
Windows Python 3.14 本地结果不能代替 Ubuntu/macOS 或远端 CI。

- 第一轮：2 failed、127 passed、37 setup errors：新 job-created 描述符缺 terminalProofSchema、Trigger 缺目标错误分类、schema drift。
  修复描述符/契约/生成物后原矩阵 166 passed，未改旧断言。
- 首次扩大矩阵：393 passed、2 failed：兼容 head disable 和 legacy automation projection 在新版本门禁/重复查询路径中漏掉。
  修复后 HTTP+P4 子集 59 passed；最终主矩阵 398 passed，1 项原 Starlette 弃用 warning（含当时 26 项 P4）。
- P4 专项最终数量及后续小矩阵见候选交付报告；测试覆盖响应丢失、并发创建、异参冲突、嵌套、start 拒绝、批量回滚、
  CAS、订阅同步、已有/未来 Run、双版本 Step、Event 去重/换版、admitted 冷恢复、calling 回执丢失不重放、撤权、停用及并发上限。
- 补充 Scheduler/平台首轮 149 passed、19 显式平台 skipped、4 failed：本机 Python 缺 requirements 中的 tzdata。
  保留原红；后续仅下载 tzdata==2025.2 到 worktree `.tmp/p4-tzdata` 并设 PYTHONTZPATH 解包路径复验，不修改解释器安装环境。
- `pnpm install --frozen-lockfile` 与 `pnpm build` 通过（tsc/Vite、88 个预压缩产物）；原有 chunk/dynamic import warning。
  无 React 源码改动。正常仓库 pre-commit hook 保持启用。

未验：P3 真实 Group MCP 整合、P5 页面、生产 TCP/Chromium 的 P4 新入口、三平台实机/远端 CI、真实数据迁移、部署及人工验收。
真实效果证据包括临时命令进程，以及原 Creator/EventStore/tick→Action→Job 路径；TestClient 故障注入不冒充真实断网验证。

最终补证：

- P4 + final contracts：`tests/test_action_unification_p4.py tests/test_job_final_contracts.py` → **156 passed**（P4 **27** 项），18.80s；最后新增 discovery 路由的分页与权限用例也在其中。
- 时区数据就绪后的补充矩阵：`tests/test_scheduler_source_migration.py tests/test_job_final_runtime.py tests/test_job_final_creators.py tests/test_platform_process_contract.py tests/test_platform_process_posix_unit.py` → **153 passed、19 skipped**，35.58s。`PYTHONTZPATH` 仅用于该子进程；tzdata==2025.2 wheel 不纳入提交。
- 隔离导入 `packages.web.server`、`packages.actions.job_creation`、`packages.jobs.action_trigger`、`packages.jobs.action_references` 通过。
- 主矩阵命令：`python scripts/run_tests_isolated.py -- python -m pytest tests/test_action_unification_p1.py tests/test_action_unification_p2.py tests/test_action_unification_p4.py tests/test_job_final_contracts.py tests/test_job_final_http_service.py tests/test_job_final_executors.py tests/test_job_final_independent_execution.py tests/test_authorization_permissions.py tests/test_authorization_runtime.py tests/test_action_groups.py -q -o addopts='' --tb=short` → **398 passed**，108.20s。当时为 26 项 P4；最后追加的第 27 项与 discovery 小接线由上面的 156 passed 覆盖。
