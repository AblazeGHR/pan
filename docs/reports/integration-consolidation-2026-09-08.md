# Pan 产品分支收束审计（2026-09-08）

## 范围与基线

- 整合 worktree：`integration/consolidation-20260908`
- clear 集成基线：`cf286865f2aaacd76c711933a455d9bd44a2a1d9`（`cf28686`）
- 本报告只记录本次分支拓扑、实际补丁等价性和验证证据；没有启动、停止或重启 Pan 服务，也没有操作 Tunnel、QQ 或 RuleWhisper。
- `Pan-main`、`practical`、`clear` 原工作树和 `unclear` 均未修改。

## 结论

基线 `cf28686` 已经收束全部本地产品 feature/fix/e2e 分支的实际产品变更。没有需要再整支合并的产品分支。

`e2e/lifecycle-fixed-20260906` 和 `fix/lifecycle-exit-supervisor-20260906` 各自仍有不在基线拓扑中的历史提交，但其唯一行为已经由 clear 后续的 lifecycle 收敛实现覆盖：当前实现直接启动 `-Supervisor`，通过 `cmd /d /c start "" /b` 建立旧 Pan 进程树之外的边界，完整传递 durable Job 参数，并保留更完整的失败持久化、checkout/PID 身份检查和回归测试。因此没有机械合并这两个分支。

## 分支清单

### 已被基线直接吸收（tip 是 `cf28686` 的祖先）

- `e2e/lifecycle-20260906`（`d5defcd`）
- `e2e/queue-reproduction-20260908`、`e2e/ui-regression-20260908`（均为 `cf28686`）
- `feature/background-job-runner`、`feature/cascade-session-delete`
- `feature/fix-delete-modal-width-20260906`、`feature/fix-stream-scroll-follow-20260906`
- `feature/long-system-prompt-adapters-20260906`、`feature/main-lifecycle-integrated-20260906`
- `feature/markdown-file-links-editor-20260907`、`feature/pan-exit-options-doc-20260907`
- `feature/select-all-sessions-20260906`、`feature/server-attachments-phase1`、`feature/server-attachments-phase1-retry`
- `feature/session-drag-autoscroll-20260907`、`feature/session-drag-toggle-longpress-20260907`、`feature/session-id-search`
- `feature/start-pan-quick-tunnel-20260905`、`feature/ui-control-row-20260905`、`feature/ui-topbar-attachment-20260905`
- `fix/chat-scroll-strict-bottom-20260907`、`fix/lifecycle-cloudflared-scope-20260906`
- `fix/lifecycle-start-restart-20260906`、`fix/streaming-layout-luna-20260907`

其中长 system prompt 分支的 Kimi 运输、Markdown 文件链接编辑器、拖动自动滚动和严格底端滚动均以基线实际源码为准；没有重复合并。

依赖准备分支 `dependency-prep-luna-20260907`（`603a2a0`）和 `dependency/playwright-20260907`（`f6081db`）也已在基线历史中；它们不是产品功能分支，不单独制造产品合并提交。

### 实际补丁已等价存在，不再合并

- `e2e/lifecycle-fixed-20260906`（`4ec8710`）：相对基线共同祖先仅有两个非等价历史补丁 `9a93df4`、`4ec8710`，内容是退出 supervisor 脱离旧 Pan 进程树及其 mock 回归测试。
- `fix/lifecycle-exit-supervisor-20260906`（`ad8fec4`）：退出 supervisor 直接启动的早期实现。
- `feature/legal-main-exit-20260906`（`77e53f4`）：补丁等价存在于基线。
- `feature/main-lifecycle-job-restart-20260906`（`dd798b0`）：补丁等价存在于基线。

clear 后续的 `a6b0bc0` lifecycle 收敛还增加/保留了 durable Job 的错误记录、失败落盘、状态迁移和生命周期测试；因此以当前 clear 实现为准，不用旧分支覆盖它。

### 明确排除

- `audit/main-restart-20260906`、`audit/queue-misdelivery-20260907`：审计/验证分支，不作为产品源合并；后者的 tip 实际就是已吸收的拖动自动滚动提交。
- `backup/main-before-queue-20260902`、`backup/main-before-queue-revert-20260901`：备份分支；后者唯一补丁是旧队列计划文档，不带入产品集成。
- `integration/pan-unclear-20260907`：包含其自身的 selected-session broadcast 实验提交 `d56f026`，属于明确排除的 unclear 内容。
- `docs/client-attachments-design*`、`docs/terminology-ma-ta`：设计/术语辅助分支，不是本次产品功能收束对象。
- `docs/跨设备移植报告-2026-08-19.md`：按纯文档变化单独审查；clear 历史中已有对应文档状态，本次不把它当作产品功能合并来源。
- `integration/batch-20260905`、`integration/final-lifecycle-20260906`、`integration/lifecycle-codex-followup-20260906`：历史整合过程分支；其产品变更已由当前基线吸收，不回合并历史整合提交。

### 待后续单独验证

没有发现仍未吸收且可安全纳入本次产品集成的 feature/fix/e2e 分支。以下是验证边界，不是已通过的 E2E：

- 真实 Windows supervisor 进程树、重启/退出和端口/PID 替换 E2E 未执行；本次仅做静态检查和 mock/隔离测试。
- 浏览器真实渲染、移动端视觉和完整 Vitest 未通过环境门槛；不把它们报告为功能已完成。

## Lifecycle 实际核对

- supervisor 脱离旧 Pan 进程树：`packages/web/server.py` 当前使用 `cmd.exe /d /c start "" /b`，并传 `-Supervisor`，不是直接把 PowerShell supervisor 留在旧树下。
- durable Job 参数：退出/重启均传递 `RequestId`、`JobId`、`RegistryRoot`、`Port`、`OldPid`、`OldPidCreatedAt`；脚本再交给 `packages.core.main_lifecycle`。
- `start_pan_probe.ps1`：`start_pan.bat` 的 ExistingMainPid、Port、ProcessAlive、WaitReady、remote/quick 状态和 QuickUrl 检查均通过 probe 脚本，未回退到启动流程中的 CMD 内联 PowerShell。
- Cloudflare 边界：`stop_pan.bat` 只处理记录的 `CF_PID`，并同时验证 checkout 根目录、端口标记和 `cloudflared.exe`；未采用全局 cloudflared 扫描/杀进程。
- 相关脚本还包括 listener owner、旧 PID 创建时间、主服务命令行标记和 Job terminal state 检查。

## 验证记录

通过：

- `python -m pytest -q tests/test_main_lifecycle_jobs.py tests/test_main_service_exit.py tests/test_remote_tunnel_api.py`
- `python -m pytest -q tests/test_kimi_adapter.py tests/test_worker_output_mode.py tests/test_worker_system_prompt_spawn.py`
- `python -m compileall -q packages tests`
- PowerShell parser 对 `start_pan_probe.ps1`、`exit_pan.ps1`、`restart_pan.ps1`、`mark_lifecycle_job_failed.ps1` 做静态解析
- `git diff --check`
- 前端 `tsc -b --pretty false`（使用只读的 Pan-main node_modules 二进制，源码 cwd 仍是本 worktree）

未通过或未完成：

- Vitest：45 个 suite 中 6 个可执行、39 个因当前 worktree 缺少依赖链接/alias 解析失败；这不是通过结果。
- Vite build：当前 worktree 的入口环境无法解析 `index.html`。
- ESLint：调用到的外部 ESLint 版本找不到当前 worktree 所需的 `eslint.config.*`。
- 没有启动任何 Pan 服务，也没有执行真实 HTTP/浏览器/Windows 生命周期 E2E。
