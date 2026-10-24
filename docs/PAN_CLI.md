# Pan 命令行

Windows 用户级 `pan` 入口通过绝对安装路径启动，与当前工作目录无关。不需要管理员权限，不安装系统服务；命令只管理注册指向的这一份 Pan。

| 命令 | 行为 |
| --- | --- |
| `pan start` | 复用 launcher 的解释器选择、实例检查与 HTTP ready 屏障。按 `startup.console_hidden` 启动独立进程，ready 后返回；重复实例/端口冲突返回非零。 |
| `pan exit` | 调用显式合法 Exit API，等待持久 Job offline、旧 PID 消失及端口释放。 |
| `pan restart` | 调用显式合法 Restart API，等待持久 Job ready、旧 PID 消失、新实例身份及 HTTP readiness。 |
| `pan debug` | 沿用 `python main.py` 的前台启动语义；当前终端持续占用，结束主进程后返回。不额外开启 DEBUG 日志级别，不改永久配置。 |
| `pan status` | 显示 running/stopped/unknown、PID、端口、CLI 版本、运行版本及 console 状态。身份不明时返回 unknown 和非零。 |
| `pan logs [-n 50]` | 读取 `logging.file`（缺省 `data/logs/pan.log`）末尾日志。 |
| `pan logs -f` | 持续跟随日志，支持轮转/截短；Ctrl+C 或 Ctrl+Break 只结束跟随，不停止 Pan。 |
| `pan doctor` | 只读检查安装入口、配置、解释器/基础依赖、端口占用及日志目录，不创建目录、安装依赖或自动修复。 |
| `pan show console` | 显示 Pan 自己的交互控制台；无控制台时由服务进程分配新控制台并绑定输入输出。 |
| `pan hide console` | 隐藏 Pan 独占的控制台，保留窗口、缓冲区和输入能力，服务继续运行。 |
| `pan --help` / `pan --version` | 查看帮助 / 这份 CLI 的版本；运行中的版本请用 status。 |

exit/restart 遵守保存的 Session 退出策略。若策略为 ask，显式传 `--running offline`（安全退休可核验的运行责任）或 `--running preserve`（保留未收尾执行为 Illegal），否则 API 拒绝；CLI 不擅自选择。`--timeout` 必须是正的有限秒数，start/debug 默认 30 秒，exit/restart 默认 90 秒。超时/连接中断返回非零并报告 Job 位置，不自动重投副作用或用 kill 代替合法退出。

```powershell
pan start
pan status
pan show console
pan hide console
pan logs -f
pan restart --running offline
pan exit --running preserve
```

debug 保留前台 main.py 的信号行为：主服务 Ctrl+C 不构成安全退出授权。需要合法退出时，在另一个 shell 使用 `pan exit`。前台调试窗口归调用者所有，show/hide 拒绝控制该窗口。默认日志为 `data/logs/pan.log`，配置的 `logging.file` 可以覆盖；stdout/stderr 也保留在前台。debug 不是 doctor，且不会修改 `logging.level`。

## 隐藏、detach 与日志

Windows 独立启动时，stdout/stderr 始终写入 `data/logs/pan-console.out.log` / `pan-console.err.log`，正常日志仍按既有 logging 配置轮转。show 后同时输出到真实交互控制台，支持原来的 q / qqq / ask 菜单；这不是日志 viewer。hide/show 不重启服务、不覆盖 `startup.console_hidden`，保持相同 PID 和 HWND。隐藏期间继续写文件及保留的控制台缓冲区。

这两个文件有界：独立启动时 launcher **不把文件句柄交给子进程**（Windows 的 venv `python.exe` 是重定向 stub，会替子进程长期持有继承到的每个句柄，导致活跃文件在整个运行期都无法改名）。launcher 只创建/确认 `data/logs` 可写并把精确路径放进 `PAN_CONSOLE_OUT_LOG` / `PAN_CONSOLE_ERR_LOG`，用 `python -c <shim> main.py ...` 启动；shim 在导入 main 之前先用普通追加句柄接住 stdout/stderr（保存导入期崩溃证据），随后立刻换成转发线程并释放文件句柄。单文件达到 `logging.console_max_bytes`（默认 20 MiB）时，在跨进程锁内整文件改名为 `.1` 并把 `.1`…`.N-1` 顺移，然后开新文件；保留 `logging.console_backup_count`（默认 3）份备份（active + 3）。永远不会截断活跃文件，也不会为了腾空间删除旧备份以外的任何文件。

若活跃文件被其他进程/编辑器以不允许改名的方式占用，改名失败只记录降级（`GET /api/diagnostics/persistence` 的 `consoleLogs.*.rotationFailures` / `degraded` / `overLimit`），写入继续追加到当前文件；此时上限是软上限：超出量不超过降级窗口内新写入的字节数（单次转发读取块 ≤16 KiB）。占用解除后下一次检查会自动补做轮转。首次接管时若文件已远超上限，会立即轮转一次，原文件原样保留为 `.1`，不会被截断或删除。

隐藏启动使用 `CREATE_NO_WINDOW`，初始化时先解除可能残留的无窗口 console 关联，且在输入线程创建前完成。第一次 show 再 `AllocConsole`。运行中的 hide 使用 `ShowWindow(SW_HIDE)`，不对活跃 reader 做 FreeConsole。输入线程只有一个，按键非阻塞读取；Python 日志流引用稳定，原生标准句柄及 stdin 在分配控制台后重绑定。console 输出失败不传播为主服务退出。

只允许隐藏 Pan 独占的窗口。共享 shell、其它附着进程及无法核验可见性的伪控制台会明确拒绝，不关闭 Windows Terminal 或用户整窗。若默认终端宿主不能提供可验证窗口，命令返回非零。首次 show 是新分配交互控制台；初始化 detach 前没有可恢复的旧屏幕缓冲区。API 语义见 [FreeConsole](https://learn.microsoft.com/en-us/windows/console/freeconsole)、[AllocConsole](https://learn.microsoft.com/en-us/windows/console/allocconsole)、[GetConsoleWindow](https://learn.microsoft.com/en-us/windows/console/getconsolewindow)。

## 注册与卸载

Windows 正常安装 `scripts/setup.bat` 在解释器准备后自动注册；已安装环境首次执行 `scripts/start_pan.bat` 也会在调用 launcher 前自动注册，以后每次执行幂等核验。默认目录是 `%LOCALAPPDATA%/Pan/bin`，只写 HKCU PATH，并广播环境变更；请打开新终端使用 `pan`，已有终端的 PATH 不会被改写。单纯 `git clone` 不执行代码，因此不会注册。直接 `pip install -r ...`、`python main.py`、`python -m packages.core.launcher`、Worker/Job 及模块导入不触发注册；手工安装的 Windows 用户应使用 `scripts/start_pan.bat` 完成首次启动。POSIX 安装入口不注册 Windows shim。

自动注册失败（同名命令、其他 checkout、文件修改、权限不足等）输出明确警告，安装和服务启动继续。解决冲突后重跑 setup/start 即可；不会覆盖另一 checkout 的注册，也不会自动切换解释器或接管其注册。需要迁移时，先按下面的卸载命令显式移除原注册，再从目标安装运行 setup/start。

仍提供独立维护脚本以供审阅和安全卸载。它生成带 UTF-8 BOM 的 `pan.ps1`、ASCII `pan.cmd` 与精确内容所有权清单，支持空格和中文路径。发现 PATH 中其它 pan 可执行文件、无所有权的同名文件、被编辑的注册文件或不同目标注册时拒绝覆盖。

审阅只在临时目录验证，不传 `--user-path`：

```powershell
python scripts/register_pan_cli.py register --root 'D:/稳定安装路径' --python 'C:/Python/python.exe' --bin-dir "$env:TEMP/Pan CLI 临时 bin"
& "$env:TEMP/Pan CLI 临时 bin/pan.cmd" --help
python scripts/register_pan_cli.py unregister --bin-dir "$env:TEMP/Pan CLI 临时 bin"
```

注册不读取 Git 元数据、不限制分支名；普通克隆、任意分支、detached HEAD 或没有 `.git` 的源码安装均可使用。应选择长期保留的安装目录（不要注册即将删除的临时工作树）。`--user-path` 只写 HKCU PATH 并读回确认，不改系统 PATH。解释器使用安装实际可用的绝对路径：

```powershell
python D:/project/Pan/scripts/register_pan_cli.py register --root D:/project/Pan --python 'C:/实际Python/python.exe' --bin-dir "$env:LOCALAPPDATA/Pan/bin" --user-path
# 打开新终端：Get-Command pan; pan --version; pan status
python D:/project/Pan/scripts/register_pan_cli.py unregister --bin-dir "$env:LOCALAPPDATA/Pan/bin" --user-path
```

卸载先核对拥有的文件 bytes，仅移除本次注册增加的用户 PATH 项，保留预先存在的同路径项及其它内容。修改过的 shim 必须由用户处理，不能强制删除。已有 shell 的环境不会自动改写，需打开新终端。

未注册时可用绝对入口：`python D:/安装路径/scripts/pan_cli.py --help`；指定其它 profile 时在子命令前传 `--root`。为避免 Pan Worker/Job 的实用环境串入，CLI 清除自身继承的 `PAN_*`，目标端口和解释器以目标 checkout 配置为准。直接 main.py / 内部 launcher 的环境覆盖规则不变。

## 验证

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_pan_cli.py tests/test_launcher.py tests/test_console_exit.py tests/test_main_service_restart.py -q -o addopts=
python scripts/run_tests_isolated.py -- python scripts/validate_pan_cli.py
```

后者仅 Windows：复制源码到带中文/空格的临时 profile，使用动态非保护端口、关闭通道/远程/插件，检查独立启动、窗口枚举、show/hide、PID/HTTP、生命周期终态、输入、前台 debug、日志跟随及临时注册。不写用户 PATH，不调用收费模型。报告及临时数据保留在输出的 report.json，服务清理由显式 Exit API 完成。
