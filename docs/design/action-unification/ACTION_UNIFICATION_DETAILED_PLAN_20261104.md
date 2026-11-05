# Action 统一详细方案 v1

日期：2026-11-04。状态：产品决策已确认，详细设计待审阅；仅方案，未授权实施、部署、分支推进或恢复暂停的 TA。

## 1. 目标与完成边界

统一旧自定义 Action、系统 Action、canonical Action 的定义、版本、工具发布和调用路径。Action 是可参数化命令；Group 是面向 Session 的工具集合；Job 是持久任务定义及其执行生命周期。

直接调用 Action 可以执行 Git 命令组合、查询数据或调用现有业务能力，结束后返回结果。也可以填充 Job 创建模板，调用 canonical 创建入口，返回新 Job 的身份。直接调用不会为了包装自身而自动生成 Job、Run 或 Step。

最终只保留一个可写 Action 目录和一个 Group 发布模型。兼容读取、历史展示、入口返回 unsupported、两套目录并排展示，都不算完成统一。所有旧能力须有具名后继或明确的历史处置结论。

实施必须复用既有 provider、执行原语、UserAuth、owner fence、operation receipt、Group release 与绑定协议；不新增第二套 Job engine、scheduler、重试器或通知系统。

## 2. 已确认的用户决策

| 编号 | 决策 |
|---|---|
| U01 | Action 以命令级调用为主，执行后返回输出；耗时较长不自动转换为 Job |
| U02 | Action 可将声明的参数填入完整 Job 创建配置，返回 Job 身份 |
| U03 | Group 发布固定 Action 版本，不自动跟随最新版本 |
| U04 | 在 Action 或 Group 内，允许将此 Action 在所有 Group 中的引用更新到指定版本 |
| U05 | 允许将一个 Group 的全部 Action 引用更新到最新已发布、未退役版本 |
| U06 | Action、Group 多版本在前端各聚合为一个条目，详情提供版本切换 |
| U07 | 支持在线显式更新 Job 的 Action 引用，后续接纳的 Run 生效；已接纳 Run 不变 |
| U08 | Agent 默认可写草稿；具有明确管理权限时可启用、发布；调用权限独立 |
| U09 | 参数不兼容须明确报告；不丢弃参数、不猜补值，不允许有阻断问题的发布或 Job 更新 |
| U10 | Action 创建 Job、Job 再创建 Job 允许但不建议；本期不做专门检测或代码限制，后续可研究创建警告 |
| U11 | Trigger 可准入固定版本的 Action 调用或 Job；直接 Action 一次执行，Trigger 不承担自动重试或恢复编排 |

以下涉及具体字段、API 和事务的内容为 MA 提出的实现设计，不冒充已经实现或用户逐字段批准的接口。

## 3. 对原 Job 统一模型的修订

权威基线：`D:/project/Pan-main/docs/design/job-unification/JOB_FINAL_MODEL_20261025.md`。

原 §2.2/D2 中“每次 Action 调用必为 Step Attempt”“直接 Action 必创建受管单步 Job”“全部运行和控制均关联 Job/Run”，在直接工具及事件触发 Action 范围内由 U01/U02/U11 取代；原“Trigger 只准入 Job”同步修订为准入 Action 或 Job。

保留的边界：

- Trigger 只承担事件匹配和接纳，不承担执行编排；目标可为 Action 或 Job。Action 目标走同一命令调用入口，Job 目标走原 Run 接纳路径。
- Job 内调用 Action 仍归属于原 Run/Step/Attempt/effect，计划冻结与恢复规则保留。
- 直接调用与 Job Step 共用命令实现、参数校验和资源所有权原语；执行上下文不同。
- 直接调用的完成回执不承担定时、自动重试或无人值守恢复。需要这些能力时显式创建 Job。
- D6 的复杂、未知旧记录保留且排除执行；提供受控展示和允许条件下的删除，不追加复杂迁移。

因此不能继续用“未生成 Run 就属于绕过统一模型”判断直接 Action；应核对是否走唯一目录、授权及共享执行入口。

## 4. 对象与版本

### 4.1 Action 的稳定身份与不可变版本

逻辑 Action 使用稳定 `actionId`；发布版本使用 `actionId + revision + digest`。名称可重命名，不参与身份判断；不凭 `_v1/_v2` 后缀自动归并旧系统 ID。

Action 头记录保存名称索引、归属、启用状态、最新可选发布版及管理 revision。版本记录保存不可变的名称/说明、输入输出 schema、执行定义、provider release 引用、效果及完成/取消/查询契约。发布后的内容变更产生新版本。

`latest` 是服务端按发布序列解析的最新已发布、未退役版本，不按版本字符串、文件时间或客户端排序猜测。没有可用版本时明确报告，批量升级不静默略过。

草稿有自己的编辑 revision，用 expectedRevision 防止并发覆盖。发布记录与管理状态分离：禁用会阻止新调用，既有运行按原执行/取消规则处理；退役版本保留审计与历史引用，不直接物理删除。

### 4.2 Group 与发布版本

Group 草稿包含稳定 `groupId`、工具别名、说明、显式 ActionRef 列表。每个条目固定 `{id, revision, digest}`，不保存可运行的 `latest` 动态指针。

发布产生不可变 Group release，固定全部 ActionRef、工具 schema 及发布摘要。Session 绑定沿用既有插件绑定和运行 Worker release 固定机制。Group 草稿更新不修改已发布 release；重新发布后，空闲切换/延后重启遵守原绑定协议。

同一 Group 首期每个逻辑 Action 只选一个版本，工具名冲突在发布时明确指出；不同 Group 可固定不同版本。历史 release 的查看与回退不删除新 release。

### 4.3 Job、Run 与创建来源

Job Step 继续固定 ActionRef。接纳 Run 时冻结完整定义、参数、策略及 provider 引用。队列里已接纳但尚未执行的 Run 也属于冻结范围。

由 Action 创建的 Job 附加 provenance：创建 ActionRef、Group release（如有）、调用 requestKey/operationId、参数摘要及实际采用的配置摘要。provenance 是来源记录，不成为第二份可执行配置，也不泄露秘密。

## 5. 统一执行定义

首期支持的定义形式：

| 形式 | 行为 | 复用路径 |
|---|---|---|
| 内部业务命令 | 调用现有受控 API/provider，如查询或 Session 操作 | 既有业务服务、来源与权限校验 |
| 命令组合 | 按旧 Job 支持的 command/argv 或 shell 描述按顺序执行，合并返回输出 | 既有 command/shell runner、ProcessOwner 和输出限额 |
| Job 创建命令 | 参数映射为完整 canonical 创建配置 | 既有 Job/Template composition、operation receipt |

调用方式可以是 Group MCP、页面“运行”、受权 API 或 Job Step，最终解析同一 ActionRef 和执行定义。不能继续各自维护同义的 `system_*` dispatcher。

参数 schema 声明类型、必填、默认值、枚举和说明。普通 command 的参数以整个 argv 元素绑定；shell Action 保留旧 Job 的 command 字符串语义，由既有 shell runner 负责解释，不把普通 Action 参数偷偷拼入另一种执行模型。cwd、环境变量、解释器和可执行程序使用旧 Job 已有的路径与权限策略；秘密只保存引用，不嵌入 release、普通日志或历史参数。

需要脚本时保留旧 Job 的解释器、脚本资源和外部路径语义。版本保证区分两种资源：内联/归档脚本按内容摘要固定；显式外部路径执行当前文件，其可变性必须在详情显示。Action revision 固定不等于外部文件字节固定，不能虚报可重现性。统一模型不得因为 Action 化而禁止旧 Job 已支持的 shell 或外部脚本；现有鉴权、手动启用、owner、capability 和资源清理门禁继续适用。

> **P0 源码事实（2026-11-06）**：本段是必须满足的约束，但当前 canonical 目录**没有** shell 字符串 Action。现存唯一 shell 语义是旧 `system_scheduled-task` 的 `action.api='shell'`（`packages/actions/store.py:system_definition` + `packages/core/background_jobs.py:validate_shell_action`），其执行者 `_execute_job_action` 已退役。因此 P2 必须先交付具名 `process.shell` 类 Action，P6 才可退役旧记录；否则就是静默缩减能力。详见 [P0 矩阵 §5 C-4](ACTION_UNIFICATION_P0_CAPABILITY_MATRIX_20261106.md)。

首期串行命令组合默认遇到非零退出停止，后继命令不执行；不隐式撤销已完成步骤。更复杂补偿不在本期自动生成。

## 6. 直接调用契约

### 6.1 请求与执行

请求包含固定 ActionRef、arguments、requestKey 和可信调用上下文。通过 Group 时，服务端从绑定 release 解析引用，不接受调用者自行替换为未发布/未绑定版本。调用者身份从既有认证或可信 IPC 得到，不从 arguments 自报。

顺序为：验证调用者/绑定 → 校验版本和参数 → 权限及 capability 检查 → 持久化必要 intent → 执行 → 保存并返回回执。owner 丢失前后都不能产生未授权的新效果。

直接调用等待结果，返回结构化数据及适合 MCP 展示的文本；不创建独立 Action scheduler。客户端/Agent 断开本身不表示命令已取消；执行端尽力完成原调用并保留原回执，但不承诺 Pan 崩溃后继续运行或恢复直接命令。

调用超时按定义的原有取消/资源清理路径处理。若无法证明原效果结束，返回 uncertain 与原查询身份，不把“HTTP 超时”记为业务失败，也不自动再执行。

### 6.2 返回值

建议结果包括 `operationId`、固定 ActionRef、`status`、`output`、`commands[]`、`error` 和受权日志链接。

每条 command 回执包括序号、状态、exitCode（不可知为 null）、stdout/stderr、有无截断、开始/结束时间。执行前拒绝、完整成功、已知失败、取消、部分完成和结果不明分别表达；不把 stderr 非空直接当失败。

Git 示例：add 成功、commit 成功、push 失败，应返回前两步完成和 push 的真实退出信息。新调用可以再次执行，但不能自动重放原调用，尤其不能重新 add/commit 掩盖已有提交。

输出有界并保留 UTF-8 边界；截断明确标记。完整日志遵守现有保留与访问权限，不将本机绝对路径直接当作任意可下载资源。

### 6.3 幂等与持久化

纯读取可以使用普通调用审计；有副作用调用在原 canonical 持久化与 operation 机制中保存最小 intent、效果身份和结果。复用现有 receipt/effect 原语，通过执行上下文区分 direct invocation 与 Job Step，不建立独立运行数据库或第二套预算。

同一 principal/profile/requestKey 和相同请求返回原结果/进行状态；同键不同参数返回冲突。进行中或 uncertain 只能查询原身份。没有持久 intent 时不得先执行有副作用命令再补记身份。

现有 executor 若要求 runId/stepId，先抽取共享的执行上下文接口，Job adapter 继续传原身份；direct adapter 使用 operation 身份。不得伪造 Run 来满足内部参数。实施前须用可控副作用证明 prepare/调用/回执崩溃窗口；如需要大段重写则报告具体冲突，不能悄悄新增引擎。

## 7. Action 作为 Job 创建模板

输入字段可开放阈值、账号排序等；未声明的字段不能覆盖脚本路径、授权主体或通知目标。填充后先形成完整配置，沿现有 schema、能力、权限和 owner-fenced composition 创建。

示意调用：`start_codex_rotation({threshold:90, accountOrder:"earliest_reset"})`。结果返回原 operationId、jobId，以及若该命令声明“创建并启动”所产生的 admission/run 身份。创建成功不等于启动成功，启动接纳不等于后台任务完成，结果须区分这些事实。

复用既有完整 Job Template 时，参数先形成显式 overrides，不改变 Template 已发布版本，也不把当前“完整、无必填洞”的 Template 偷改为另一种模板模型。Action 的输入 schema 承担可填字段。

每次新调用默认是一次新创建意图；是否启动、是否允许重复业务实例由该 Action 配置及现有 Job 策略决定，不按参数内容自动合并。原 requestKey 的响应丢失只查原创建结果。

嵌套创建允许；本期不增加图遍历、递归层数或专用禁止规则。普通鉴权、容量和运行配额仍正常适用。未来警告属于非阻断体验优化。

## 8. 权限与平台

使用既有 UserAuth 主体和权限矩阵，不建立 Action 私有授权系统。实现时将 read、draft write、publish/manage、invoke、Group build/bind 映射为同一矩阵内的明确操作；新增 operation 名称须同步默认拒绝和管理 UI。

Agent 默认只能写草稿；有明确管理 grant 才能启用/发布。系统定义继续要求已有 manageSystemActions 等特权。Job 创建 Action 同时检查调用权限和 Job 创建/启动权限；包装不能赋予提权。Job Step 执行继续使用原 Job authority 及其运行时权限校验。

平台差异限定在既有平台层。缺 capability 时 API 明确 unsupported，UI 提示原因，Group 不把该平台不可用命令伪装为可用。Windows/POSIX command 和 shell runner 使用同一结果、owner、取消与 unknown 契约，不因 POSIX shell 可用而改变旧命令的参数或权限语义。不得新增一层只针对 Action 的高风险确认；原有系统 Action 的保护门禁仍保留。

## 9. 显式版本升级

### 9.1 Group 升级

提供两个范围：此 Action 在所有有权管理 Group 中更新到指定发布版；此 Group 所有 Action 更新到各自最新发布版。

先生成有界 preview：每个 Group 的当前 revision、原/目标 ActionRef、schema/能力/效果契约差异、权限与冲突。范围不能因权限不足静默缩小；确认页明确纳入和未纳入项，不向无权调用者泄露 Group 内容。

apply 使用 preview token/摘要、显式目标列表、各对象 expectedRevision 和 requestKey。改 Group 草稿，不覆盖历史 release，不自动切换正在使用的 Worker。更新后的草稿经完整校验再显式发布。

同一事务存储内一批原子更新；超出有界批量上限分成明确批次，不承诺跨存储全局原子。迁移前 Group 若仍在 JSON 文件中，只提供 preview；待唯一写入权转入 canonical 存储再开放多 Group apply，避免实现临时多文件事务引擎。

### 9.2 Job 引用在线更新

支持单 Job 和从 Action 发起的显式多 Job 更新。采用同样的 preview/apply/CAS/原 requestKey；默认只替换选定引用，保留其余配置。检查输入映射、前序输出、completion、cancel/reconcile、retry 和 requiredCapabilities。

提交时更新 configRevision，之后新接纳 Run 冻结新版本；已接纳 Run（含 queued、retry_wait、paused、uncertain）保持原计划。与 admission 并发时以同一 owner-fenced 事务顺序划界，不出现半旧半新计划。

引用 Job configRevision 的 Trigger 等订阅者由服务端列出。需要同步时在同一有界配置事务内更新被选中的订阅引用；无法合法同步的列为阻断，不让更新成功后定时器无声停摆。已有 Event、admission、来源游标不重写或重放。

回退也是新配置修订，仅影响后续 Run；不能撤销已有副作用。长期运行的当前 Run 不会因更新而改变脚本或参数。“更新并重启”和运行时参数热更新不属于本期。

### 9.3 Trigger 准入 Action 或 Job

Trigger 采用互斥的目标结构：`target.type=action` 时包含固定 ActionRef 和参数映射；`target.type=job` 时保留原固定 Job 引用与输入映射。不能同时填两个目标；旧 targetJobId 在兼容解析中映射为同一个 Job 目标，不创建第二种订阅对象。

Action 目标使用与直接调用相同的执行入口、权限、效果身份、输出和取消规则。Group 是工具发布集合，不是事件执行的必要中间层；Trigger 不借 Group 绑定获得权限。

接纳前校验 Event 与 Trigger 条件、启用状态、当前授权、目标版本及输入映射；持久化成功后才交给执行端。接纳记录冻结实际 Trigger revision、ActionRef/定义、输入、来源和有效授权上下文。执行前继续检查当前权限和 owner；冻结授权证据不能豁免撤权。

以 profile 内原 `(eventId, triggerId)` 保持同一接纳身份，修改 Trigger revision 不让同一事实自动变成第二次调用。重投、冷恢复和并发消费者只查询或领取原接纳记录。复用现有 Event/Trigger 去重和投递持久化机制，再关联统一 Action operation；不另建事件 scheduler 或效果 ledger。

必须区别两种恢复：持久化已接纳但尚未开始效果的调用可继续交付；已经开始的调用只能查原回执或按原受信查询契约 reconcile。不能将恢复投递解释为允许重新执行未知效果。无查询证据时保持 uncertain，不自动重投。由 intent 到效果的崩溃窗口必须作为独立验收项。

直接触发首期没有自动效果重试、退避、Run 编排或独立终态通知生产者。结果保存在原接纳与 invocation receipt 中供查询；失败、部分完成和 uncertain 不伪装成“事件未消费”以重新投递。需要持久重试、暂停恢复及通知时选择 Job。既有 paused/deferred 的未接纳事件规则保留，不能混同于已经尝试的失败调用。

同一 Trigger 的新调用遵守显式并发上限及有界待交付容量；首期默认 serial，满容量按既有持久投递背压处理，不能丢弃或绕过接纳去重。该交付控制不增加效果重试策略。

若 Action 创建 Job，完成边界是原创建命令的结果；若定义包含启动，则分别记录创建与启动接纳。新 Job 的终态、恢复、取消和通知由新 Job 管理，Trigger 不等待后台业务完成。一次创建结果不明时查询原 operation，不再创建第二份 Job。

Action 版本更新对既有接纳记录不生效。Trigger 目标换版走显式配置更新、schema 检查和 CAS，仅影响之后的新接纳；已有事件去重身份、调用回执和 frozen plan 不改写。页面提供 Action/Job 目标选择与实际固定版本，明确直接调用没有 Job 自动恢复策略。

## 10. API 草案（待与现有路由适配）

以下表示所需操作，不强制新增全部平行 endpoint。优先扩展现有 canonical/API/IPC 工具。

| 操作 | 建议形状 | 关键约束 |
|---|---|---|
| 逻辑 Action 列表/详情 | GET actions、actions/{id} | 一个逻辑身份一条，分页 |
| 版本与引用 | GET actions/{id}/versions、references | 按权限定界，提供固定 ref |
| 草稿/发布/退役 | 现有 definition CRUD/control 扩展 | CAS、requestKey、发布不可变 |
| 直接调用 | POST actions/{id}/invoke | 固定 ref、参数、原 requestKey |
| 调用查询 | 原 operations/{id} 扩展 | 仅查询，不以 GET 触发重放 |
| Group CRUD/build/binding | 适配既有 Group API | 发布 release 固定版本 |
| 引用升级 | preview-reference-update / apply-reference-update | 显式范围、diff、CAS、原子批次 |
| Trigger 目标配置与结果 | 扩展原 Trigger definition/control 和接纳查询 | 互斥 Action/Job 目标，固定版本，原事件去重 |

MCP Group 工具使用稳定工具别名，schema 来自固定 release，返回同一 invocation 结果。Agent 草稿工具、HTTP 页面及 MCP 不能各维护独立 schema 或执行分支。

错误至少区分 invalid_schema（含字段路径）、permission_denied、reference_conflict、revision_conflict、capability_unsupported、capacity_exceeded、execution_failed、uncertain。既有错误码可复用，不要求按此列表另造同义错误。

## 11. 前端流程

Actions 列表一个逻辑 Action 一行：名称、用途、系统/自定义标识、当前展示版本、版本数、状态、能力提示。系统和用户定义可以筛选，不能形成两套同义执行目录。

详情版本选择器默认最新可用发布版；从 Group/Job 引用进入时默认实际引用版。展示输入 schema、命令/模板、输出、权限、资源可变性、引用和版本差异。版本切换只改变查看对象，不隐式升级引用。

Group 一个条目含草稿与 release 版本视图；提供单 Action 换版、全部升级、发布差异、Session 使用情况。升级所有 Group 的危险范围用明确数量和差异确认，不逐个弹确认框。

Job 引用升级页面同时列出“后续运行将改变”“已有运行保持版本”以及订阅引用影响。运行详情展示冻结 ActionRef；当前目录版本变化不覆盖历史运行的名称/定义证据。

运行 Action 表单由同一输入 schema 生成；显示阶段输出、结果或新 Job 链接。保持宽屏和 320/390px 可用，长输出可滚动，明确显示截断/部分完成/unknown。

## 12. 旧能力迁移与退役

先逐项建立旧能力矩阵：registry CRUD、API GET 白名单、命令执行、Session selector/source、系统特权、Group immutable build/pins/bindings、模板创建、历史与删除。每行附现有符号、真实入口、目标接口、差异和具名验证，不按文件名宣告退役。

旧 ID 到稳定 Action/版本的映射由明确清单确定；保留 provenance。旧 Group release、运行 Worker pin 不原地改写，过渡期必须有明确停用边界和只读兼容，不成为永久双写。

简单白名单定义经 dry-run 后幂等转换，核对版本、引用、权限和原字节备份；禁用定义保持禁用。复杂/破损/unknown 保存原字节并排除执行，展示原因和受控处置。真实未知 lease 不清零，不借迁移取得删除权。

写入切换按 profile 明确记录权威代际：验证目标 → 取得现有 owner fence → 停止旧写入口 → 在同一受控切换中启用 canonical 写入口。崩溃后按原切换 operation 查询恢复，不双写或重复迁移。不在未知 writer 存活时切换。

退役验收同时覆盖 HTTP、MCP、插件目录、动态 provider、后台恢复和旧系统 dispatcher。保留必须的历史读取宿主，只删除已证明不再承载能力的叶子。没有具名后继证明的旧能力不得删除。

## 13. 实施分片与路径所有权

| 阶段 | 交付 | 前置/验收 |
|---|---|---|
| P0 设计与清单 | 本方案、旧能力/调用路径矩阵、模型修订、真实 schema 差异 | MA 审阅；不以旧 UI 双目录候选作统一实现 |
| P1 定义与版本存储 | 唯一逻辑目录、不可变版本、解析与授权 | 并发发布/CAS/缺版本/旧 ID 映射契约 |
| P2 共享命令入口 | internal/command/direct 与 Job adapter | Git 部分成功、输出、崩溃与 no-replay；不新增引擎 |
| P3 Group 发布 | 固定 refs、MCP、绑定、显式升级 | 运行 pin、schema 一致、真实 stdio 调用 |
| P4 Job 创建、引用升级及 Trigger 双目标 | 参数化创建、provenance、在线引用更新、Trigger 准入同一 Action 入口 | 响应丢失幂等、admission 并发、Trigger 一致性、事件去重/崩溃 unknown |
| P5 React | 聚合条目、版本、调用、引用差异及更新 | 真实生产 HTTP/Chromium，桌面与窄屏 |
| P6 数据与入口收敛 | dry-run/apply、D6 展示、旧写入口退役 | 源身份保全、故障注入、零旧执行调用证据 |
| P7 整合门禁 | 精确整合 SHA、三平台 CI、手工验收说明 | 所有能力逐行闭环，非只看扫描和聚焦绿 |

后续派发必须重新核对实际 HEAD/dirty 与 agentflow 政策。模型和并发从有效政策读取；Codex TA 暂停不因本方案自动解除。Automation 美化拥有 AutomationView/CanonicalJobsView/components/automation，Action 任务不得并发覆盖；需要交叉变更由 MA 排序整合。

## 14. 必须证明的验收场景

1. fresh profile 单一目录包含系统和自定义逻辑 Action；多个版本只显示一个条目。
2. Group 发布 v1 后 Action 发布 v2：原 Worker 仍调用 v1；显式升级/发布后新绑定使用 v2，原 release 可查。
3. Git add/commit/push 成功及中途失败：完整输出、真实退出码、已完成动作不被重放；同键不同参数冲突。
4. 断连接、超时、执行后回执前崩溃：按原身份查询；无法证明的结果保持 uncertain，不重发效果。
5. Job 创建 Action 响应丢失：只产生原 Job；创建成功/启动失败分别可查；嵌套创建未被专门禁止。
6. Job 引用更新与 admission 并发：每个 Run 只有完整旧或新快照；queued/retry/uncertain Run 不被改写；Trigger 后续仍正常准入且无重放。
7. 批量升级中并发编辑、权限不足、不兼容及目标退役：明确阻断，原子批次无半写，原 Group releases/运行记录不变。
8. Agent 草稿可写、无 grant 发布拒绝、有 grant 发布成功；Group/Job 包装均不能提权；错误不回显秘密。
9. Windows/Ubuntu/macOS import、三根收集和同接口命令契约；unsupported 显式；Windows 专属 skip 不算 POSIX 证明。
10. 真实 MCP stdio 和受权 HTTP/生产浏览器完成发现、调用、版本切换、升级、未知状态与手机展示。
11. 旧 registry/Group/system/session/template 能力逐节点对应；复杂源记录字节不变、启动不因历史数据崩溃。
12. Trigger 直接 Action：重复 Event、并发领取和冷恢复只对应原调用；接纳后未执行可继续交付，效果已开始但缺回执保持 uncertain；修改 Trigger 版本不能重放同一 Event。
13. Trigger 调用创建 Job 的 Action：响应丢失查询原身份且只有一个 Job；Trigger 调用成功不冒充后台 Job 成功；撤权、停用、并发上限与持久背压保持有效。

前端执行 pnpm build 和正常 hook；后端/长命令使用隔离 runner 和持久 Job，保留原红与实际受测 SHA。最终区分源码、单测、构建、真实链路、远端 CI、数据迁移、部署和人工验收。

## 15. 本轮产物与后续

本文件是完整方案首稿，不是产品交付。实际源码观察以 main `c9dc621a01ccb568878c8a37297c034cbaec18de` 为基线：final_service._admit 冻结 actions，final_contracts._check_action_ref 校验 revision/digest，publish_action 拒绝覆盖定义；旧 packages/actions/provider.py 仍有直接执行路径。

下一步是 MA 对照只读能力映射审查 P0，修订原模型文档及生成逐片任务规格；用户仅要求编写方案，本轮不开始 P1 产品实现。系统 actions 的完整族映射、实际 authority/receipt 抽取点在 P0 必须据代码补齐，不将本设计中的拟议字段当成现成接口。

## 16. 旧能力覆盖附录

本节将旧方案作为兼容基线接入本方案。旧能力不能因为改名为 Action 而减少；以下内容是兼容约束，不是新的可选安全策略。

| 旧能力族 | 统一表达 | 必须复用的既有能力 | 兼容结果 |
|---|---|---|---|
| background process | `process.exec` Action，可直接调用或作为单步 Job | 原 Runner、ProcessOwner、PID/birth、进程树、日志和取消 | 原 Job ID、日志、退出码、查询/取消/重试语义保留 |
| command / shell / external script | `process.command` 或 `process.shell` Action | 原 command/shell runner、cwd、解释器、环境、窗口策略、超时和清理 | shell 字符串、外部脚本路径和旧参数形状不能因 Action 化被拒绝 |
| Session message | `session.message` Action + Job | 原 queue、delivery、目标 Session、source 和通知 | 原 target、Queue ID、通知和错误字段保留 |
| broadcast | `session.broadcast` Action + Job | 原 fanout、稳定目标顺序、逐目标 receipt | broadcast identity 和逐目标结果保留 |
| scheduled task | Action + Scheduler/Event/Trigger/Job | 原时间计算、时区、misfire、发布游标和 owner switch | 旧时间行为不静默改成即时执行 |
| QQ/channel | provider Action + Job | 原 provider receipt、messageId、错误码、超时和 unknown 查询 | 不把通道结果压成普通文本或自动重发 |
| main lifecycle / supervisor | 受保护 system Action + Job | 原 supervisor、phase、PID/birth、offline/ready 证明和授权矩阵 | 继续保留原保护门禁，不将任意 shell 规则套到受保护生命周期 |
| assign/resume/session control | 受控 system/session-operation Action | 原 UserAuth、owner、lease、Pause/Illegal 和目标选择 | 不通过 Action 包装提权或绕过原管理边界 |

旧 MCP/HTTP/GUI 入口的兼容适配必须返回原工具可识别的参数、Job ID、错误、通知、查询、取消、删除和恢复结果。兼容层只能让旧入口落到同一 canonical Job/Action 执行身份，不能在后台再创建一条旧 Job 或第二个执行实例。旧入口在完成逐项覆盖前继续保留；历史读取与审计保留，不以“统一 UI”代替迁移。

> **P0 状态（2026-11-06）**：当前基线对旧写入口统一返回 `410 retired`，旧创建类 MCP 工具已移除并给 canonical guidance，只保留读投影与显式取消（见 [P0 矩阵 §5 C-1/C-2](ACTION_UNIFICATION_P0_CAPABILITY_MATRIX_20261106.md)）。这与本段"返回原结果"不是同一语义，需 MA 明确签收"显式拒绝 + guidance + 读投影"作为具名处置，或决定恢复适配器；P0 不把现状写成迁移完成。

普通 Action 继承旧 Job 的 shell、外部脚本、cwd、环境、窗口、输出、超时、进程树和清理能力。现有鉴权、手动启用、owner、lease、capability 和系统 Action 保护门禁继续生效；本方案不额外添加一层 Action 专属高风险确认，也不因直接调用没有 Run 就收窄能力。

## 17. Scheduler/Event 范围与未覆盖部分

Action/Group/Job 统一首期冻结直接调用、Action 链、Job 引用更新、Group release 和 Trigger 双目标规则。Scheduler/Event 的下列细节不在本片重新发明执行引擎，但必须在 P0/P4 的兼容规格中补齐：

- 多时间 Trigger 的允许范围、稳定命名、编辑和删除关系；
- 时间类型、时区、DST、misfire、下次触发计算和发布游标；
- Event 类型、字段路径、来源命名、identity 算法和授权范围；
- Job/Scheduler/Trigger 跨存储创建、崩溃恢复、禁用草稿和半成品清理；
- 共享 Scheduler 的删除保护、引用影响和 owner switch；
- 旧 Scheduler MCP/HTTP/GUI 的参数、错误、结果和历史字段映射。

这些事项的默认基线是旧 Scheduler 的时间、恢复、owner 和去重行为；若新实现无法无损表达，必须保留原记录并明确不兼容，不能猜测为 immediate、自动 recovery 或新的执行权限。它们可以作为独立 Scheduler 阶段交付，不阻塞 Action/Group/Job 的直接统一，但在宣称整体迁移完成前必须逐项验收。

## 18. 方案覆盖结论与 P0 输出

新方案已经覆盖旧方案的核心产品决策：直接 Action、Action 创建 Job、串行 Action 链、固定 Group release、Job admission 冻结、在线引用更新、Trigger 指向 Action 或 Job、旧能力保全以及不重放 unknown/uncertain。

P0 不再负责重新讨论这些方向，而负责产出一张逐项覆盖矩阵：旧入口/旧能力 → canonical ActionRef → 复用 executor/owner/receipt → 兼容 API/MCP/UI → 迁移或保留策略 → 验收证据。P0 同时要修订旧模型中与 U01/U02/U11 冲突的“所有 Action 必须创建 Run/Trigger 只能指向 Job”文字，并把本节列出的 shell、外部脚本和 Scheduler/Event 边界同步到实施规格。

完成 P0 后，设计状态分为两部分：Action/Group/Job 核心方案可冻结进入 P1；Scheduler/Event 的未覆盖细节作为后续阶段规格继续推进。任何一部分都不能用只读目录、历史展示或扫描零引用替代真实执行和兼容验收。

## 19. P0 源码核对与修订记录（2026-11-06）

本节记录 P0 在真实基线上的核对结果、对本方案的限定语，以及仍待裁决的冲突。逐项能力矩阵与调用链见 [P0 覆盖矩阵](ACTION_UNIFICATION_P0_CAPABILITY_MATRIX_20261106.md)。

### 19.1 基线更新

§15 记录的是 `main c9dc621a01ccb568878c8a37297c034cbaec18de`。P0 改在 `main f5fb73c5035a2ebed6ece33167205bb984be531d` 上核对。两者之间的差异只有 Automation 前端（`packages/web/src/views/CanonicalJobsView.tsx`、`AutomationView.tsx`、`components/automation/*`）、`evidence/automation-ui-polish/*` 与新增的 `tests/test_automation_ui_polish_browser.py`；`packages/` 下没有任何 Python 变更。因此 §15 对 `final_service`/`final_contracts`/`actions/provider` 的代码观察在 `f5fb73c5` 仍然成立。

### 19.2 §15 三项代码观察复核

| §15 原观察 | 复核结果 | 位置 |
|---|---|---|
| `final_service._admit` 冻结 actions | 成立：把 `cfg.steps` 与按引用解析后的 action 定义复制进 `frozen`，再写 admission | `packages/jobs/final_service.py:_admit` |
| `final_contracts._check_action_ref` 校验 revision/digest | 成立：解析 ActionRef 时按 id/revision/digest 校验 | `packages/jobs/final_contracts.py:_check_action_ref` |
| `publish_action` 拒绝覆盖定义 | 成立：仅 `definition("action", old) != value` 时报 `reference_conflict`；同内容幂等 | `packages/jobs/final_service.py:publish_action` |
| 旧 `packages/actions/provider.py` 仍有直接执行路径 | 成立，但入口收窄：只在 Group build 的 `*-provider` stdio 能力调用里可达，不经过 FinalService/ledger | `packages/actions/provider.py:execute/run` |

### 19.3 需要写进实施规格的源码事实

1. **两个写目录并存**：旧 `/api/actions` CRUD/Group build/草稿（`packages/actions/api.py`）仍可写；canonical `GET /api/jobs/actions` 只读，且 `GET /api/actions` 已被 `packages/jobs/final_api.py:install` 永久别名到 canonical catalog。P1 必须一次收口，不能靠"两个 tab"。
2. **canonical 目录只有产品 typed Action**：`FinalService._install_product_actions` 发布 `product_process`（含 `_cleanup_v1`/`_logs_v1`）、`product_session_message`/`_v2`/`_runtime_v1`、`product_session_broadcast`/`_v2`/`_runtime_v1`、`product_assign_v2`/`_runtime_v1`、`product_resume_legal_running_v1`、quota 与（provider 绑定时）`product_channel`/`_runtime_v1`。没有 `shell`、`scheduled-task`、`session pause`、`main-lifecycle` 的 canonical Action。
3. **旧执行宿主已大面积退场**：`packages/jobs/system_actions.py:execute/invoke` 由 `__getattr__` 拒绝；`packages/core/background_jobs.py` 的 `start/start_message/run_due_*/_execute_job_action/_recovery_loop` 等在 `_RETIRED_ENTRYPOINTS`；`automation.job_runtime` 恒为 `None`，`packages/jobs/action_runtime.py:Runtime` 无生产装配点。这决定了"迁移进度"不能用旧代码是否存在判断。
4. **Trigger 只指向 Job**：`packages/jobs/final_contracts.py` 的 trigger 定义硬性要求 `targetJobId`；`packages/automation/engine.py` 的准入目标是 `targetJobId`。U11 的 Action 目标属于 P4。
5. **main lifecycle 走专用受控路由**：`POST /api/system-actions/main-lifecycle` → `packages/core/lifecycle_action.py:protected_plan` → `ProtectedLifecycleExecutor` → `TypedExecutor`；它不进入 `FinalService` 目录（`high_risk` 被拒绝）。§8"沿用旧保护门禁、不新增 Action 专属确认"因此成立，但"唯一目录"必须为它保留受控登记方式。
6. **Scheduler 旧桥只剩源码**：`packages/scheduler/legacy_bridge.py:LegacySchedulerBridge` 当前无 import、无实例化；旧 scheduler 写路由在 `packages/scheduler/api.py:RETIRED_ROUTES` 统一 410。不能据此宣称已删除或已迁移。

### 19.4 与 U01–U11 的逐条对照

逐段核对后，本方案正文**没有**与新决策相反的文字；以下三处容易在实施时被误读，已加限定语：

- §5（统一执行定义）末尾：补充"当前无 shell Action，P2 必须先补具名 successor"，避免把"支持 shell"读成现状。
- §16（旧能力覆盖附录）末段：补充"现基线是 410 + 工具移除 + 读投影"，避免把"兼容适配"读成已具备的返回契约。
- §8 的"新增 operation 名称须同步默认拒绝"：实施时必须**同时**映射旧 Job 已有的授权/手动启用语义，不能因为默认拒绝而把旧能跑的路径变成新拒绝；本方案不新增 Action 专属鉴权层（`FinalService._permit` 只读 `UserAuth` 矩阵）。

### 19.5 待裁决（P0 不自行改模型）

| 编号 | 待决点 | 关联 |
|---|---|---|
| C-1 | 旧 MCP 是否继续要求"工具名不变"的无感迁移，还是接受 `JOB_FINAL_MODEL` §0 的退休 + guidance | 矩阵 §5 C-1 |
| C-2 | "兼容适配返回原结果" 是否可用"显式 410 + guidance + 读投影"作为具名处置 | 矩阵 §5 C-2 |
| C-3 | `legacy-mcp` transport 授权保留到 P6 还是立即改 grants（前者违反 E03 时间表，后者断旧读/取消） | 矩阵 §5 C-3 |
| C-6 | `system_main_lifecycle` 如何在"唯一目录"中受控登记而不放开 `high_risk` | 矩阵 §5 C-6 |

### 19.6 本轮未验证

未跑测试/构建/服务；未跑数据盘点、dry-run 或 owner switch；未验证真实 QQ/HTTP/stdio/Chromium 链路；未验证 Windows/POSIX 实机差异。矩阵中的"当前状态"均为静态阅读结论。
