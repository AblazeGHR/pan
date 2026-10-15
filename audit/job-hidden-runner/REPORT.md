# venv runner Terminal 回归窄修

基线 main/practical 已验收的 `4a4872ec5fe284b10b354a4780e1a745f54834f5`；隔离 branch `fix/job-hidden-runner-20261015`，worktree `D:/project/pan-worktrees/job-hidden-runner-20261015`。此交付不移动 main/practical，不操作用户服务／8767／8768。

## 现场反例与归因

用户截图原件完整保留，路径／SHA256 见 verification.json。用户报告 Job `job_9aeab412187c8bfd5822a95e` hideWindow=true，空白 Windows Terminal 页标题指向 practical venv Python。

一次性只读现场核对见 incident-processes.json、incident-windows.json，没有修改、attach console、注入、隐藏或终止现场进程。服务 27440 创建于 10:30:26.914995；runner venv redirector 34272 于 10:32:52.385910 → base Python 9172 于 10:32:52.393967；WindowsTerminal 11304 于 10:32:52.521852；CI task redirector 33868 于 10:32:53.262751。Terminal 进程早于 CI task 约 741ms，不能归因于 CI 之后执行的 visible 测试。Terminal 11304 的 HWND 656846 实测 visible、1144×629、位于输入桌面 Default；核对时当前标题已是 cmd.exe，不能仅凭当前标题认定它仍是原截图的相同页。runner Python 9172 线程桌面为 Default；线程未初始化 USER 对象时 GetThreadDesktop 名称查询失败保留 null，不捏造其 desktop。

practical 的 background_jobs/background_runner 文件磁盘 mtime 均早于服务／runner 创建，路径／SHA 见 incident-source-files.json。这只能证明磁盘版本与时序，未读取或注入用户服务的 Python 内存，不能宣称已证明服务内存加载了该 SHA。下面在新进程上复现相同机制，不依赖该假设。

## 官方依据和最小修复

[CPython v3.14.5 venvlauncher.c](https://github.com/python/cpython/blob/v3.14.5/PC/venvlauncher.c#L399-L420) 在 GetStartupInfo 后重设 dwFlags，并以 process flags=0 创建真实 Python；父 redirector 的 DETACHED_PROCESS 不被该子创建沿用。[Windows process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags) 明确 CREATE_NO_WINDOW 与 DETACHED_PROCESS/NEW_CONSOLE 混用会被忽略。

旧的 legacy／independent runner 创建使用 DETACHED_PROCESS。改为 CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP，明确不混用 DETACHED_PROCESS。8 行新增／2 行替换产品差异只在这两处 runner spawn：前者为 MCP／HTTP／scheduled 共用入口，后者为独立 JobStore。runner 自身始终无窗口，与任务 hideWindow 分开；任务现有 private desktop/DACL/guard 或显式 NEW_CONSOLE 策略保持原状。NO_WINDOW 在本机实测保留 redirector 和真实 Python 共享的无窗口控制台；不是“没有任何 console 对象”，也不是 SW_HIDE。

## 红绿证据和覆盖

新测试创建自有 --without-pip --system-site-packages venv（未安装包，未改用户 venv）。test-only sitecustomize 在 runner 模块导入前记录本进程／父进程 FILETIME、console HWND、console client PIDs、desktop、venv prefix；退出时记录实际 __main__ module 名称、路径和哈希。正常退出后用仍保留的 Popen process handle 再核对 FILETIME 并 wait/close。未对共享 Terminal 做任何写操作。

- baseline.xml / baseline.jsonl：修改产品前，正式 `agent_background_start → HTTP POST /api/background-jobs handler → background_jobs.start → venv runner → DesktopCommand` 一条真实短任务复现。任务成功、stdout/stderr 正常，但 runner console HWND=2426550，回归断言红。此时观测发生在 runner 业务模块导入之前，排除任务载荷自行创建窗口。
- fixed.xml / fixed.jsonl：两处 flags 改后 HWND 已为0，但初版测试错误要求 console client 列表为空，3项失败。该要求混淆“无窗口控制台”与“不存在控制台”；按实际契约改为检查 HWND=0、client 列表包含准确 runner+redirector PID，保留红证据。没有移除 HWND 断言或用 proxy visible 位宣称显示结果。
- regression.xml：138 passed/0 skipped。含 3 条 venv 入口、13 条既有真实生产入口、多代 GUI/new-console/AllocConsole、stdio/exit/filesIPC/cancel/deadline/shutdown/recover/crash/cleanup，以及清理故障注入、policy、service/bridge 回归。批次保持原有 legacy crash survivor 限制。
- venv-final.xml / venv-final.jsonl：4 passed/0 skipped。最终补齐父 redirector retained handle FILETIME、真实入口模块路径／SHA、runner 完整退出；MCP hidden、MCP hideWindow=false+exit7、independent、scheduled。所有 runner HWND=0；隐藏任务无 console HWND，visible 任务有自己的 console HWND、failed/7、stdio/cwd/venv prefix 正确。MCP transport 用隔离 ASGI TestClient 替代到用户服务的 HTTP 传输；请求仍经过正式 MCP body 构造和真实 production HTTP handler，认证边界在 fixture 内隔离，没有启动主服务／应用 lifespan。

Windows 本机 Python 3.14.5 实测，不冒充其它 Python/Windows/默认 Terminal 组合都已验证。测试临时 venv 的 sitecustomize 仅用于观测，不属于产品方案。sitecustomize 不能观测它执行之前所有帧；本次结论是消除已复现的 runner 控制台委托源，不升级为所有程序零帧／不抢焦点保证。主动 desktop 逃逸、外部 broker、异步 guard 首次显示竞态和 DACL 兼容代价原说明继续有效。

现场 Job 未等待／轮询其结果，未停止它或 runner；未关闭共享 Terminal。原研究树及试用分支均保留。验证中的 owned 短任务均正常终止或按既有 owned 生命周期测试清理；最终进程查询无本 worktree / pytest 临时实验残留。已知 pytest timeout、Pydantic lifespan、Starlette TestClient deprecation warnings 保留；XML 本地保留，精简 JSON/JSONL 提交。
