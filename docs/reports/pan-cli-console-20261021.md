# Pan CLI 与交互控制台候选交付

工作树：`D:/project/pan-worktrees/pan-cli-console-20261021`；分支 `feature/pan-cli-console-20261021`；基线 `883406f7e1bc8bb34ef1c9837cd595c45d6577cd`。本交付仅提交任务分支供 MA 验收，不移动 main/practical，不 push/release，不改真实用户 PATH 或共享依赖。

## 实现

- 独立绝对入口 `scripts/pan_cli.py` 与可审阅用户级注册/卸载 `scripts/register_pan_cli.py`；支持中文/空格和任意调用 cwd，拒绝无关 pan 命令/无所有权文件及被编辑的 shim。临时注册不改 PATH；真实 `--user-path` 要求稳定 practical，由 MA 验收后执行。
- start/exit/restart/debug/status/logs/logs -f/doctor/show console/hide console/help/version，完整用法见 [Pan CLI](../PAN_CLI.md)。退出策略 ask 要求明确选择；用户退出命令不调用内部 stop 原语。
- CLI 先验证记录 PID、创建时间、checkout、listener 与 HTTP 身份；生命周期 POST 再通过 expected-instance headers 防止查询后的替换。等待持久 Job 终态及旧进程/新进程事实，不将 accepted 当完成。
- Windows 独立可见启动拥有自己的 console，stdout/stderr 仍持久化。隐藏启动初始化时、reader 启动前只做一次 FreeConsole，首次 show 分配真实交互控制台。运行中 hide 保留窗口，输入和 Python 日志代理保持稳定，原生标准句柄重绑定。拒绝共享/非托管/不可核验窗口。
- 已同步 README、仓库 Pan skill 和 HTTP API 引用文档；用户级 skill 由 MA 处理。

## 实测结果

解释器为本机 Python 3.14：`C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe`，无依赖安装、无收费模型调用。

1. `python scripts/run_tests_isolated.py -- python -m pytest tests/test_pan_cli.py tests/test_launcher.py tests/test_console_exit.py tests/test_main_service_restart.py -q -o addopts= --junitxml=audit/pan-cli-console-20261021/pytest.xml --disable-warnings`：**46 passed，0 failed，0 skipped，1 warning，21.77 秒**。这是 31 个既有回归加当时 15 个 CLI 测试。
2. 补充用户 PATH mock/readback 测试后，定向 CLI 再跑：**16 passed，0 failed，0 skipped，1 warning，0.68 秒**。未机械重跑无新改动的 31 个既有回归；两份 JUnit 分开保存，不能将其相加为不重复的测试数。覆盖临时注册/精确卸载、冲突、禁止任务树真实注册、用户 PATH 内存模拟、身份/失效 PID、失败生命周期 Job、启动锁、日志轮转、只读 doctor、共享窗口拒绝及非 Windows 分支。
3. `python scripts/run_tests_isolated.py -- python scripts/validate_pan_cli.py`：**真实 Windows 完整通过，exit 0**。隔离安装为 `C:/Users/14709/AppData/Local/Temp/Pan CLI 中文 空格 dzrta4rl/安装 root`，自有动态端口 **53212**。25 次 CLI 调用，隐藏 ready 后约 **2.8 秒**返回（原始 seconds 见 JSON）；初始 PID **32980**，无自有可见窗口，两次 show/hide 同 PID/同 HWND且各次 HTTP 正常；二次启动非零；restart 新 PID **36092** 且 ready；exit offline/旧 PID 消失/端口释放。失效 PID、错误 PID 均拒绝。logs 跟随 Ctrl+C 返回 **130**，服务仍为 PID 32980。可见启动 PID **23268** 的 qqq 原生输入完成合法 Exit；前台 debug PID **22228** 持续占用，hide 拒绝，显式 Exit 后 debug 返回 0；配置 bytes 不变。临时 shim 注册/运行/卸载通过。
4. 最终可见编码及共享父控制台补验：外部自有 listener 占用 53212 时 start 返回 **3**；没有抢占。可见启动 PID **33580**，原生输入 `中文q` 后持久 stdout 可严格 UTF-8 解码并包含原文。前台 debug 的共享父窗口 **HWND 10291514**，hide 返回 **1 / HTTP 409**，操作前后均可见；debug CLI PID 36396、服务 PID 8624，合法退出后 CLI 返回 0。
5. 最后将输入菜单 HTTP 提交移出 console lock，避免与 show/hide 互等；只针对该改动补真实 hidden start → show → qqq → Exit 验证：PID **35524**，Job `job_53242c70c605265808fe0b24` 为 offline、旧 PID 消失、listener null，约 3.6 秒完成。
6. compileall（涉及的 Python 文件）与 `git diff --check` 通过；无前端源码变更，未做前端 build 或全量测试。

原始证据：

- [组合 JUnit](../../audit/pan-cli-console-20261021/pytest.xml)、[最终 CLI JUnit](../../audit/pan-cli-console-20261021/cli-final.xml)
- [完整 Windows 命令报告](../../audit/pan-cli-console-20261021/windows-report.json)
- [编码、端口冲突、共享父窗口补验](../../audit/pan-cli-console-20261021/supplemental-report.json)
- [最后 reader 原生输入回归](../../audit/pan-cli-console-20261021/final-reader-report.json)
- [生命周期终态和清理记录](../../audit/pan-cli-console-20261021/terminal-and-cleanup.json)

## 调查与边界

初次实测 show 的 AllocConsole 返回 Windows Access Denied，未报假成功；微型原生探针确认 CREATE_NO_WINDOW 下 GetConsoleWindow 为 NULL，但直接 AllocConsole 失败，而先 FreeConsole 再 AllocConsole 成功。初始化 detach 修复后，完整显示/隐藏与输入测试通过。首次日志跟随测试暴露继承的忽略 Ctrl+C 标志，CLI 的前台 follow 现显式恢复本进程信号处理；失败时遗留的自有 follower 已核验 PID/创建时间/argv 后清理。

真实验证只使用自有临时 profile 和动态端口；8767/8768 没有 HTTP 请求、测试或生命周期操作。临时服务用显式 Exit API 清理，已保存 Job 终态与 launcher `fallback=none`，没有 taskkill fallback。最终扫描未发现自有 profile 的 main/CLI 残留进程，端口无 listener。失败 profile 和日志留在临时目录供调查，不做破坏性清理。

**未验证**：真实 HKCU PATH 注册/卸载（只做临时文件实测和内存模拟，留 MA 验收后执行）、最终 practical 解释器/安装后的新 shell 发现、不同默认 Windows Terminal 宿主、跨桌面和人工关闭 console、真实 Linux/macOS 运行。非 Windows 的初始化/输入委托有定向 mock 回归，不冒充真实跨平台 E2E。无真实 Worker/provider/通道任务运行，因此不将本次结果表述为 Worker 业务合法性全链路验收。源码实现拒绝共享/不可核验窗口，但不同 Windows Terminal 宿主仍须人工确认。
