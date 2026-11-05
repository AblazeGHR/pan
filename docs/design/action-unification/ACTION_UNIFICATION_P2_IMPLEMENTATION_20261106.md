# Action 统一 P2：共享命令执行

基线：P1 `ccd54a1b44a5078857630818d42afc0580bb55a5`。工作树：`D:/project/pan-worktrees/action-unification-p2-20261106`。保留 P1 不可变版本、alias 和生命周期存储。

## 入口与身份

- direct：`POST /api/jobs/actions/{actionId}/invocations`，请求为 `actionRef: {id, revision, digest}`、`arguments`、`requestKey`。回执使用原 `final_operations`，作用域为 principal/profile/invoke；不存在隐藏 Job、Run、Step，也没有新增数据库表。
- 查询：原 `GET /api/operations/{operationId}` 或 `GET /api/operations?operation=invoke&requestKey=...`。重复请求同参数返回原身份/进度，异参冲突；冷实例无需取得执行 owner 即可按原身份只读查询。
- Job：原 FinalService driver、FinalRuntime/Ledger 接纳 Run/Step/Attempt/effect。`process.command`、`process.shell` 使用 command adapter；原 `process` Job 仍使用原 IndependentExecutor/ExecutionStore/supervisor，不改变其恢复、日志、自动重试契约。direct 调用 `process` 可复用同一共享命令原语。
- 两个 adapter 均调用 `TypedExecutor.execute_commands` → `command_execution` → 既有 `ProcessOwner`，不伪造 Run/Step 身份。

## 描述与旧能力

具名 successor 为 `process.command`、`process.shell`。产品注册只发布、默认禁用；通过原 Action lifecycle 显式 enable 后才可调用。用户草稿/发布和旧文件型入口继续保留，迁移与全面退役属于 P6。

`execution.config` 与按 schema 校验后的调用参数形成 descriptor。支持：

- `argv` 字符串数组；旧 `executable` + `args` 形式也可复用。旧 `{parameter}` 只按整个 argv 元素替换，绝不将参数拼入 shell 字符串。
- `command` 保留完整 shell 字符串，经旧 `validate_shell_action` 与平台层 `shell_command_argv` 处理；Windows 为原 COMSPEC `/d /s /c`，POSIX 为原 `/bin/sh -c`。显式 shell 解释器可用 argv prefix，命令字符串作为最后一个元素传递。
- `externalScript: {path, contentDigest?}` 和显式 `interpreter` 字符串/argv prefix，脚本参数为 `args`；相对脚本路径以 cwd 解析。与 command/argv 同时出现时，externalScript 是所引用可变资源的观测信息，不替换原命令。
- `cwd`、继承环境加 `env` 覆盖、`hideWindow`、`timeout`/旧 `timeout_sec`。进程树所有权、超时及取消清理均复用 ProcessOwner。
- `commands[]` 串行组合，沿用现有 chain 的 1..32 项边界。任一非完成结果阻断后继，不回滚已完成副作用。

每条命令返回 ordinal、status、exitCode、stdout/stderr、各自截断标记、开始/结束时间、原进程身份和 cleanupProof。每条命令分别保留至多 512 KiB stdout/stderr，保留旧命令逐项输出额度；整次调用受原串行链的 32 项边界约束。仍持续排空超量输出，UTF-8 截断不留下半个字符。stderr 非空不会把零退出改为失败。

外部资源回执给出 declaredPath、解析路径、mutable=true、declaredDigest 与 spawn 前观测的 observedDigest。它用于发现文件变化；观测摘要不保证外部文件在随后打开时未被其他进程改变，不能宣称 revision 固定等于脚本字节固定。

## 门禁、崩溃与取消

新增 `action.invoke` 位仍在原 UserAuth Action 行，旧授权文件缺此字段时只投影 false，不改写原文件/授权 revision。新入口默认拒绝 Agent；system Action 保留 manageSystemActions，high-risk 保留既有 supervisor 边界。React 授权界面属后续 UI 范围；本片可由现有授权 API 显式设置该位。

新 direct 调用必须持有 canonical owner。每次 spawn 和运行中的检查都通过 owner-fenced transaction；版本状态、授权 revision、能力在执行边界重查。owner 失效时旧执行端只能尽力清理自身原进程树，不能提交新效果或伪造终态回执。

direct 在调用前提交 prepared intent，随后记录 calling、逐条输出和 terminal receipt。任一窗口崩溃，原 prepared/calling/uncertain 回执都保留，只查原身份，不自动续跑或重放。Job 仍以原 effect admission 阻止重放，逐条 checkpoint 附在原 Run plan，最终 receipt 写回原 effect。

前序命令成功、后续失败返回 partial；Job 将该 effect 保持 uncertain，原 retry 门禁拒绝重放组合。只有未产生前序成功的已知拒绝才保留原 Job 显式 retry 语义。

direct coroutine 取消和 timeout 都要求 ProcessOwner 整树清理证明；无法确认则 uncertain。Job 的原 cancel intent 由本地 command owner 观察，待原 Attempt 的所有命令清理已确认后完成原取消 operation；不将 command 身份误投给独立 process store。宿主崩溃后没有回执时保持 unknown，不假定资源已清，也不增加无人值守恢复承诺。

## 验证入口与边界

`tests/test_action_unification_p2.py` 使用隔离临时目录/真实 Git 和 Python 子进程，覆盖 Git 部分成功、shell/argv、旧 descriptor、环境/窗口参数、外部脚本变化、输出上限、timeout/取消/树清理、同键并发/冲突、prepare/调用/回执崩溃窗口、owner 前后失效、Job 原身份及失败阻断。

回归矩阵另包含 P1、HTTP service、final contracts/schema、TypedExecutor、IndependentExecutor、UserAuth、旧 Action Group 与 ProcessOwner 契约。各次精确结果见交付报告；Windows 本地结果不代表 Linux/macOS、远端 CI 或部署验收。

本地验证记录（Windows、Python 3.14，全部后端命令经 `python scripts/run_tests_isolated.py --`）：

- 扩大矩阵：`python -m pytest tests/test_action_unification_p1.py tests/test_action_unification_p2.py tests/test_job_final_http_service.py tests/test_job_final_contracts.py tests/test_job_final_executors.py tests/test_job_final_independent_execution.py tests/test_authorization_permissions.py tests/test_authorization_runtime.py tests/test_action_groups.py tests/test_platform_process_contract.py -q -o addopts=''` → 393 passed、19 skipped（平台显式 skip）、1 项 Starlette 弃用 warning。
- P2 专项：`python -m pytest tests/test_action_unification_p2.py -q -o addopts=''` → 最终 25 passed（包含逐命令输出额度保持负控）。具名 successor 的 enable 使用原系统管理授权；没有放宽 P1 system Action 门禁。
- `python -m packages.jobs.final_contracts --write` 更新生成契约后，schema drift 断言恢复通过。首次扩大矩阵保留的 red 为 388 passed、19 skipped、1 schema artifact drift failure；未削弱断言。
- 隔离 `import packages.web.server` 成功。`packages/web` 的 `pnpm install --frozen-lockfile`、`pnpm build` 成功（tsc、Vite、88 个预压缩产物校验）；仅既有 chunk/dynamic import warning。
- 将具名 successor 的 cancel 能力声明与已实现的整树取消对齐为 supported=true 后，P1/P2/HTTP/契约子集 190 passed；最终逐命令输出额度复核另跑 P2 专项，精确结果随候选提交报告。

不接入 Group MCP、Job 创建模板、Trigger Action target、React UI、真实迁移或旧入口全面退役；没有 push、合并、服务重启或真实 profile/data 操作。
