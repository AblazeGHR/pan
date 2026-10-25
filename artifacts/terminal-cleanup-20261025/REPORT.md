# Terminal「starting → cleanup-failed」现场故障：根因、修复与验证

- 工作树：`D:/project/pan-worktrees/terminal-cleanup-20261025`
- 分支：`fix/terminal-cleanup-20261025`
- 基线：`3bf174b1`
- 修复提交：`f3e1b3f8`（未移动 main/practical、未 push、未跑远端 CI、未启停 8767/8768）；本报告与截图在其后的证据提交中
- 证据目录：`artifacts/terminal-cleanup-20261025/`
- 环境：Windows 11；Python `E:/software/miniforge/python.exe`；node `v24.22.1`；sidecar `npm ci` 安装 2 个 exact-pin 包

## 1. 结论（根因）

现场 Terminal 不是"清理失败"，而是**终端引擎从未启动**：

1. `HeadlessEmulator` 构造时 spawn 的 node sidecar 起不来：sidecar 目录下的
   `node_modules` 里没有 exact-pin 的两个包（`@xterm/headless@6.0.0`、
   `@xterm/addon-serialize@0.14.0`），node 以 `ERR_MODULE_NOT_FOUND` 退出；
2. launcher 把这次失败如实记成 `engine-startup-failed` / `exit 8`
   （`engine.created=false`、启动 owner 已收敛），随即退出；
3. **service 没有看派生句柄**，继续在 `wait_for_bootstrap_identity` 里等满
   `startup_timeout=30s`，然后按 `bootstrap-unverified` 调用 `_mark_unproven`
   → 记录状态被写成 `cleanup-failed`；
4. UI 列表/状态行直接显示 `cleanup-failed`（"clean up failed"）。这类记录
   `pid=null`，reconcile 归为 `unattributable`，`remove()` 又要求 `exited`，
   于是既不能收敛也不能删除。

失败点位于 **引擎启动**（launcher exit 8）而非 runner 的收尾路径：runner 从未
bootstrap，所以 `natural-cleanup-exhausted` / `lease-cleanup-exhausted`
（runner.py 的 3/6 退出路径）本次都没有被触发；`cleanup-failed` 只是 service
对**所有**启动失败的统一标签。

### 现场证据（只读，不接触 8768 服务）

`D:/project/Pan/data/terminals/launcher-status/` 的两条 2026-10-25 17:46/17:47
记录均为：`phase=engine-startup-failed`、`exit_code=8`、`engine.created=false`、
`startup.converged=true`；对应记录 `status=cleanup-failed`。同一部署的
`packages/core/terminal/emulator_sidecar/node_modules/` 是**空目录**（0 项），
2026-10-06 的成功记录（`term_5deaa9030b8744a6`，exit 0）证明该部署此前装过依赖。

### 不是 3bf174b1 的回归

`packages/core/terminal/emulator_sidecar/package.json` / `package-lock.json` 自
2026-10-03 `f4214844` 起未变；`3d84c769` 的同一文件与基线完全一致。故障来自
部署漂移（文档要求的 sidecar 专属 `npm ci` 未落地/被清空），不是 dsh/kimi
adapter 或 native bridge 改动引入的代码回归。

## 2. 修复点

| 位置 | 变更 |
| --- | --- |
| `packages/core/terminal/emulator.py:353` | 新增依赖预检：按 Node 的 `node_modules` 向上查找顺序检查 sidecar 的 `package.json` 依赖；缺失时**在 spawn 前**抛出 `residual.reason=sidecar-dependency-missing`（owner=None，无 pid、无 Job、无进程） |
| `packages/core/terminal/launcher.py:108` | `STATIC_RESIDUAL_REASONS` 白名单加入 `sidecar-dependency-missing`，让该静态分类进入 launcher-status，而不是被投影成泛化的 `engine-startup-failed` |
| `packages/core/terminal/service.py:1121` | `_wait_for_bootstrap`：等自证的同时每 50ms 看一眼**自己派生的句柄**；有终态文件或非零退出即停止空等（零退出无状态文件的 shim 场景仍保持等待） |
| `packages/core/terminal/service.py:1208` | `_classify_launcher_exit`：只有「`phase=engine-startup-failed` + `engine.created=false` + `startup.converged=true`」才判为"启动失败且无遗留资源"；其它一律维持 `cleanup-failed`（**不降级、不吞错**） |
| `packages/core/terminal/service.py:1241` | `_mark_startup_failed`：写 `exited` + 静态 `exit.reason` + `exit.code=8`、`cleanup_pending=false`，删除未使用的 bootstrap 自证文件；记录可删除 |
| `packages/web/src/views/TerminalPanel.tsx:15` | 按静态 reason 显示启动失败原因（不再是"clean up failed"）；创建失败的 502 `startup-failed` 也有明确文案 |
| `packages/web/src/views/TerminalPanel.test.tsx` | 新增该 UI 文案回归 |
| `tests/test_terminal_startup_failure_ma.py` | 新增 9 条回归（预检/launcher 状态/4 条 service 分支/真实引擎 3 次开关） |
| `tests/test_terminal_service.py` | 注入替身补齐 `read_bootstrap_identity` / `delete_bootstrap_identity` 契约 |

设计边界：

- 判"无清理债"必须同时具备 launcher 自报事实 + 派生句柄已退出，且状态文件必须
  晚于本次记录创建（防止复用显式 id 时读到旧状态）；
- 无法证明的路径（无状态文件、owner 未收敛、exit 6、shim 先退出）仍写
  `cleanup-failed`，不冒充 `exited`；
- 不新增/放宽 `RuntimeState` 词表，不把 `cleanup_pending` 置真，也不删除任何
  可能有 runner 使用的秘密。

## 3. 验证

### 3.1 隔离复现（修复前，临时数据根，无 8767/8768）

`d:/tmp/pan-repro/run1`（`TerminalService(root=<temp>)`，真实 `python -m
packages.core.terminal.launcher`）：

- `create()` 用时 **30.08s** 后抛 `StartupFailed bootstrap-unverified: SecretNotFoundError`；
- launcher stderr：`exit code=8 reason=engine-startup-failed engine-cleanup=converged`；
- launcher-status：`engine.created=false`、`startup.converged=true`、residual 有 pid 且
  `process_exited/job_verified/guard_closed/cleanup_closed` 全为 true；
- 记录：`status=cleanup-failed`、`exit.reason=bootstrap-unverified`；
- 原始引擎异常（进程内诊断）：`dependency-missing:Cannot find package '@xterm/headless'`。

证据：`repro-before-fix-*.json`、`repro-before-fix-launcher-stderr.txt`。

### 3.2 修复后（同一隔离实例 + 真实 React UI）

真实 Pan ASGI + React 构建，数据目录重定向到临时目录，随机端口 `56039`：

- 新建终端 → 运行 → 输入 `echo ...` → 看到输出（`ui-terminal-running.png`）；
- 终止终端 → 记录 `exited`、屏幕区显示"终端已退出"（`ui-terminal-closed.png`）；
- 临时移走 sidecar `node_modules` 再新建：**0.20s** 失败，记录
  `exited / exit.code=8 / reason=sidecar-dependency-missing / cleanup_pending=false`，
  UI 显示"终端引擎依赖未安装……本次没有启动任何 shell。"（`ui-terminal-startup-failure.png`、
  `after-fix-api-observation.json`）；该记录可删除（`DELETE` 语义为 POST remove，
  首次曾因并发列表轮询触发一次 500，重试成功；见 §4）。
- 关闭后无 launcher/sidecar 残留；`secrets/` 为空；无 `*.tmp` 残留。

### 3.3 测试

| 套件 | 结果 |
| --- | --- |
| `tests/test_terminal_startup_failure_ma.py`（新增） | 9 passed |
| `tests/test_terminal_launcher.py` | 84 passed |
| `tests/test_terminal_service.py` + `_rework` + `_ma` + `test_terminal_api.py` + `test_terminal_ws.py` | 164 passed |
| `tests/test_terminal_emulator.py` + `test_terminal_composition.py` + `test_terminal_deployment.py` | 69 passed |
| `tests/ -k terminal`（全量 terminal 相关） | 1006 passed, 6 skipped, 4830 deselected（17m10s） |
| `packages/web` `npx vitest run src/views/TerminalPanel.test.tsx` | 11 passed |
| `packages/web` `pnpm build`（tsc -b + vite） | 通过 |

## 4. 未测边界 / 已知限制

- **现场部署仍需装依赖**：修复让"缺依赖"在 0.2s 内被准确分类并可删除重试，但不会
  凭空提供 npm 包。现场补救是在
  `packages/core/terminal/emulator_sidecar/` 执行 `npm ci`（exact pin，2 个包），
  再用 `python scripts/check_terminal_deployment.py` 确认 `ready=true`。本次按约束
  未改动现场部署/服务。
- **mac/linux**：`HeadlessEmulator` 在平台门前即拒绝（`unsupported-platform`，终端
  设计上仅 Windows 生产宿主），本次变更不改变该行为；service 的快速失败逻辑是平台
  中立的，非 Windows 上会更快得到明确原因而不是 30s + cleanup-failed。
- 首次删除该失败记录时观察到一次 `POST /remove` 500（当时 5s 列表轮询刚并发读过同一
  记录；重试 200）。未捕获到确切异常，**未定位**根因（怀疑与 Windows 并发读下的
  `unlink` 共享冲突有关，但未验证），也未在本次修改 registry。记录重试后可删除是
  已验证事实。
- 未验证真实 8768 现场、未重启现场服务、未做跨机/跨用户/durable detach；未跑
  远端 CI。
- 未覆盖 runner 已 bootstrap 后、attach/四门阶段的启动失败（本次现场故障不在这条
  路径；该路径仍保持原有 `bootstrap-unverified` / `cleanup-failed` 语义）。
