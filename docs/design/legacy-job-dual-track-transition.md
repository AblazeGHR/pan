# Job 双轨入口过渡（2026-10-24）

状态：入口过渡 candidate。直到用户另行明确确认迁移完成，两套执行语义长期并存；本变更不代表迁移完成，不会自动转换、复制或重放旧 Job。

## 源码审计与入口

本次整合基线为最新 main `4e1d7fa1307388623dc698938092ede59dece4a6`（含授权 f03e、安全删除 9418 及性能字体优化）。隔离树 `D:/project/pan-worktrees/legacy-job-latest-20261024`、分支 `integration/legacy-job-latest-20261024` 选择性整合旧候选 `0998277b` 的 UI 契约，不引入旧 `legacy_management.py` 或其 1 秒出生容差/进程内取消观察门禁。main/practical 由 MA 独立验收后决定整合，本任务不移动分支。

- 前端路由 `/jobs`（生产访问 `/react/jobs`）渲染 `packages/web/src/views/JobsView.tsx`；导航和 Session 徽标共用 `stores/jobStore.ts`。该 store 的 `/api/jobs` 数据本来就是混合列表，不是新执行器专用 registry。本变更保留共享 store 和 WS 更新机制，只在列表视图按执行语义分栏，不改变导航总数。
- `packages/jobs/api.py` 的 `/api/jobs` 是历史全 kind 展示/管理门面：读取 `background_jobs` 和可选独立服务桥，生成 `job_public_view` 与兼容 `unified.project`。URL 中的 jobs 或返回的 `unified` 字段不能证明它是新 Job。
- 新 Job 的真实执行识别是 Action-reference 配置（`action.api=action_reference`）；当前出口为 `unified` 存在且没有 `compatibility`。它进入 `action_runtime.Runtime`，写入完整配置、revision、Run/Attempt 与快照生命周期。
- `background_jobs` 的后台进程、定时 message/broadcast、旧 scheduled-task、服务生命周期仍是原执行路径。`system_actions` 已会给某些旧执行写兼容审计/快照；这不是旧 runner 被替换，也不能据此把旧记录送入新 Action Job API。
- `packages/web/server.py` 保留 `/api/background-jobs` 列表/get/cancel/retry、message PATCH 和 `/api/session-message-jobs` 创建。raw list 只含后台进程、定时 message/broadcast，并排除 scheduled shell 子进程；scheduled-task 和 main-lifecycle 来自 `/api/jobs` 门面。旧详情另读 raw get，保留原始 PID、出生身份、日志路径、通知/投递字段。

## Registry、执行与状态边界

| 边界 | Legacy Jobs / 旧 Job | Unified Jobs / 统一新 Job |
| --- | --- | --- |
| 数据与 registry | background_jobs 原 kind 原记录；scheduler 兼容记录；可选独立服务保持自己 ownership | Action-reference 配置及配置 revision，完整 Job Template 来源，Action 构建引用 |
| 物理位置 | 默认 `data/background_jobs/jobs/*.json`，日志在同根 logs；`PAN_BACKGROUND_JOBS_DIR` 可覆盖 | 当前 Action runtime **复用相同 generic registry 容器**，按 scheduler data root 寻址，另有 Action/Template/快照相关数据 |
| 目录注意 | scheduler root 优先级为 `PAN_SCHEDULER_DIR` > `PAN_BACKGROUND_JOBS_DIR` > 默认。人为配置两者不同会形成两个旧 HTTP 寻址空间，混合门面并非自动跨目录迁移/合并工具 | 本 candidate 不重配这些目录，不尝试合并 registry；隔离 HTTP 用默认生产式共享根验证 |
| 执行器 | 原 background runner、message tick、legacy scheduler、supervisor；可选独立服务桥保持其 owner | Action runtime、固定 system Actions、Action 引用与快照、恢复与重叠策略 |
| 状态来源 | 原 Job `status`、schedule、lastDelivery/lastError、通知回执、PID 身份；原始审计可查看，不作为新 Attempt 控制 | 新配置完整性、Run/Attempt、unknown/uncertain/cleaning、snapshotCleanup、revision |
| 列表 UI | 混合门面中过滤非新执行记录，兼容投影也归旧标签 | 只显示无 compatibility 的新执行记录 |

不能为满足“两套系统”而虚构两个已经分开的物理 registry。这里明确区分的是记录契约、执行 ownership、状态解释和操作 API。

## 现有能力清单与 API

| 能力 | 旧 Job | 新 Job |
| --- | --- | --- |
| 列表、详情 | `/api/jobs` 混合读门面；`GET /api/background-jobs[/{id}]` 原始详情/列表；按现有 kind 和状态筛选、搜索、排序、置顶 | 相同混合门面中的新记录；`GET /api/jobs/action-jobs` 与其 runs；新详情保留 Action/Run/Attempt |
| 创建 | 原 background API 启动命令；session-message-jobs 创建单发/群发定时消息；原 scheduler/jobs kind API 仍可用；旧标签 New legacy Job 复用已有 NewJobForm 创建向导，提交原 POST /api/jobs | 现有 New Job / Job Template 编辑器、完整配置与固定 revision 实例化 |
| 编辑/调度 | `/api/jobs/{id}` PATCH、旧 message PATCH；scheduled-task 的 pause/resume、run-now 与 schedule 编辑保持旧语义 | protected `/api/jobs/action-jobs/{id}` PUT，expectedRevision；Action schema、trigger、recovery、overlap、notification、completion |
| 取消 | `POST /api/background-jobs/{id}/cancel`，原 PID/出生身份校验及原消息取消；scheduled-task 用已有 enabled/paused 控制，不伪造 process cancel | protected Action Job cancel，保留现有安全状态语义 |
| 重试 | 只给 completed/failed/cancelled 后台进程展示 Retry legacy Job；`POST /api/background-jobs/{id}/retry`，由旧 API 创建新旧 Job ID 与 retryOf；message 不支持 retry，仍须编辑或重新创建 | protected retry/confirm-recovery，与 Run/Attempt/副作用不确定性规则一致 |
| 日志/历史 | 保留日志路径、旧 `/api/jobs/{id}/runs` 与投递详情；新增可展开原始记录。旧 raw API 没有通用日志内容 tail 端点，本次不新建文件读取 API | 现有新详情中的 Attempt 和快照清理审计 |
| 删除 | `DELETE /api/jobs/{id}`，直接复用最新持久安全删除；scheduler DELETE 别名仍经同一底层路径 | protected Action Job DELETE、revision/确认规则照旧 |

旧详情不渲染兼容投影中的 Unified execution 卡片，不提供“转成新 Job”或自动启用按钮。原始 JSON 里的历史兼容审计仅用于检查原记录，不赋予新的执行能力。群发/background MCP 不加入 Authorization 新权限矩阵；原 background/broadcast API 不新增权限矩阵项或 Bearer 要求；新 Action Job 列表/详情/模板与执行继续保留最新 Authorization 契约，未授权时隐藏新 Job，登录后按现有 token 重新加载。OAuth、private exporter、额度策略、数据格式与旧 runner 均不在变更范围。

## 旧删除生命周期门禁

本次不新增删除门禁，完全复用基线 `packages/core/background_jobs.delete_job` → `packages/jobs/process_cleanup.terminate_owned_jobs`。未修改 `background_jobs.py`、`background_runner.py`、`process_cleanup.py`、HTTP/scheduler 删除路由、PID 校验、cancel/retry 实现或通知。

1. 显式 DELETE 的生产事务先序列化 delete/spawn/task-spawn，在 registry 锁内持久化 `cleanup_pending`，再释放 registry/global 锁执行进程清理。删除必须先完成安全取消/清理并等待进程树结束，不能仅凭请求取消成功就视为退出。无需人为要求用户先点 Cancel：运行中 DELETE 本身进入原安全清理路径，不在 UI/API 额外叠加 status-only 拒绝。
2. 进程删除复用最新精确出生身份（零容差）、retained process handles、原 runner 持有的 Job Object 复制及成员校验，终止 Job 后必须证明树为空和所捕获成员已退出。证明持久化为 `cleanupMembers` / `cleanupTreeEnded`，允许显式再次 DELETE 核对，不能退回旧候选的进程内观察表或 1 秒容差。
3. 缺失身份、无可信 Job Object、访问拒绝、PID 复用、unknown/uncertain、退出未确认都不能直接 unlink。后台 unknown/uncertain 会先持久化为 `cleanup_pending`，失败保留原 ID、`cleanupOriginalStatus` 与 `cleanupError`；冷恢复不自动重投或再次删除。UI 保留错误并重新读取最新状态，供用户显式再次核对。`cleanup_pending` 不是新 Job Attempt 状态，不允许在此状态走旧 retry/cancel 旁路。
4. 终态记录沿用最新生产删除判断；不要求终态历史记录具有本次服务内取消证据，不因服务重启额外封死原本合法的删除。旧消息/定时任务的非进程终态删除与 scheduled-shell 活跃 dispatch/child 拒删都按生产路径保持；不声称为所有旧记录新增了统一 OS 容器保证。
5. protected Action Job 的旧 DELETE 旁路仍返回 `user_confirmation_required`，新删除继续走最新授权/revision/快照门禁。独立 Job service 既有删除不支持语义不变。自动 retention、本次树外服务与真实数据不在任务范围。

## 何时可考虑收口

只有用户明确确认迁移完成后才讨论移除旧入口。前提包括：逐 kind 能力和历史字段映射经审阅、所有旧生产者/MCP/脚本/通知消费者清点完毕、活跃与未知进程 ownership 完整退役、未知回执不重放、旧日志与审计保留方案和可恢复备份、独立迁移工具经过真实环境验收且提供逐条结果。任何兼容投影、UI 分类完成或单次测试通过都不是迁移完成证据。

本次不会移动/改写 registry，不自动复制 schedule、Action 参数或执行快照，不从旧 Job 生成可执行新 Job，不重放 unknown/uncertain，不改变旧通知与额度语义。后续迁移需要单独授权。

## 验证入口

- UI/API 定向：`JobsView.bulk-search.test.tsx`、`JobsView.schedule-shapes.test.tsx`、`api.legacyJobs.test.ts`、`tests/test_job_delete_safety.py` 与 `tests/test_job_delete_main_combination.py`。
- 真实 HTTP/Chromium：`tests/test_legacy_job_transition_http.py`，随机非 8767/8768 端口、临时 data 和 Chromium profile，生产 build，无网络路由 mock；实际启动本地 Python 后代进程，证明未登录新 Job 不可见、登录后可见、旧创建/取消/重试仍走原 API、运行中 DELETE 实际通过原 Job Object 清理完成、unknown 保留 cleanup_pending，以及旧 retryOf 与新配置 revision 分离。
- 证据目录：`audit/legacy-job-latest/`。源码/单元、构建、隔离 HTTP/Chromium 与生产接管是不同结论；本任务不部署、不合并、不 push、不访问受保护服务端口，也不启动真实模型 Worker。

登录态组合修复：现有 API request() 添加 Authorization 后，默认 JSON Content-Type 曾被 options.headers 覆盖，旧创建返回 422。本次在 requestBody() 合并默认与调用者 headers，保留 Bearer 和原 JSON 类型；仅修复传输，不改变权限判定。对应 transport 回归与真实 GUI 旧创建验证保存在本轮证据目录。
