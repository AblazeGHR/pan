# 后台子进程窗口策略第一阶段 TA 报告

状态：实现及本轮定向验证完成，待 MA 验收。提交后冻结；done 不等于 accepted。

## 工作树与授权边界

- Root：`D:/project/pan-worktrees/job-child-window-policy-20261010`。
- Branch：`fix/job-child-window-policy-20261010`。
- 初始 HEAD：`1ad4fb1fada6ecdd8af7e9e747ee78ce5568ccfc`；初始 status 干净。
- 完整读取 CODEBUDDY.md、docs/TESTING.md、docs/design/background-jobs.md 与已安装 Pan skill。
- 无子代理、report subscribe、MA notify、merge、push、服务 restart、真实 provider/账号调用。
- 未改 frontend、Session 升降级、CLI 协议/队列、依赖/锁、config/live data、其他树或 .workflow。
- 所有源码/文档/审计文本编辑通过 apply_patch；测试日志/XML 由命令生成。
- 本轮单次执行约 4–34 秒，均用前台短命令；未启动长时 durable Job。

## 只读盘点与覆盖

完整基线文本匹配入口为 [baseline-launch-sites.txt](baseline-launch-sites.txt)。它包含
生产、测试、手工诊断、浏览器工具和注释/mock 命中，不能把每条匹配当作真实启动。
以下表格按真实用途分类。窗口参数仅复用 runtime-independent 公共 helper，不
包装或替换 subprocess，不自动更改任意第三方设置。

| 分类 / 来源 | 基线状态 | 本轮结果 |
|---|---|---|
| core/worker.py `_spawn_process`、`_consumer_oneshot` | 后台管道 CLI/Python wrapper，无窗口参数 | 两处默认 helper；队列、交接、取消/退出路径未改 |
| main.py `_spawn_qq_bot`、`_spawn_wechat_bot` | 后台 Python bot，无窗口参数 | 两处 helper，显式转发 stdin/stdout/stderr 保留日志 |
| remote/tunnel.py `CloudflareTunnel.start` | 后台 cloudflared 管道，无窗口参数 | helper；不运行真实 tunnel |
| adapters/codex/wrapper.py、kimi/wrapper.py、opencode/wrapper.py | wrapper 内后台 CLI 子进程，无窗口参数 | 三处 helper；绝对文件入口能从任意 cwd 导入 helper |
| adapters/codex/app_server_wrapper.py `AppServer.start` 非 native-control 分支 | 管道 app-server，无窗口参数 | helper；native-control OwnedKernel → ConPTY 分支不变 |
| adapters/cbc/adapter.py help；claude/adapter.py help/version；opencode/adapter.py models | 后台能力探测，无窗口参数 | 四处 helper，输出/超时/错误处理不变 |
| core/launcher.py `_python_works`、`_fallback_taskkill` | 后台解释器探测/精确 PID 工具，无窗口参数 | 两处 helper；未实际 stop/start 用户服务 |
| core/rewind/driver.py CBC fork；web/server.py Codex debug models | 后台 capture_output 子进程，无窗口参数 | 两处 helper；不改 provider 协议/功能语义 |
| scripts/run_tests_isolated.py | 测试入口启动 pytest，无窗口参数 | 默认 helper，显式转发标准句柄；输出与非零退出码真实回归 |
| test_codex_takeover.py:67 原调用点 | 自有后台 sleep Python，无窗口参数；只是可疑来源 | helper；不将截图根因归于此路径 |
| test_terminal_identity.py spawn_child；test_terminal_guard.py holder/自有孙进程；test_terminal_registry.py 两个跨进程 fixture；test_test_isolation.py 污染环境负控 | 普通自有后台 Python，无窗口参数 | helper；保留原断言与内核/Job 原语 |
| test_real_queue_http_e2e.py server；test_real_history_coldload_e2e.py server/heartbeat probe | 自有隔离 HTTP/WS harness，无窗口参数 | helper；真实本地 fake-provider 验证，临时根/临时端口 |
| jobs/window.py；core/background_runner.py；jobs/independent_runner.py 的命令 | 已有隐藏/显式可见策略 | flags 不改；只补精确 helper 边界文档；旧 Job 与 retry 覆盖保留 |
| core/background_jobs.py runner；jobs/independent_service.py host/runner | DETACHED_PROCESS + 独立生命周期 | 不加 CREATE_NO_WINDOW；不改 ownership/脱离机制 |
| launcher 主进程/launcher cloudflared；remote/mcp_runtime.py；tailscale_runtime.py；qq/gateway_plugins.py；notifications.py；terminal emulator/runner | 已有隐藏参数或独立策略 | 不机械替换；现有显式 visible 选项不变 |
| takeover_job.py；web/server.py 用户 terminal；terminal/spawn_win.py；codex/owned_kernel.py | 用户交互 console、CREATE_SUSPENDED/Job ownership、原生 ConPTY | 原语、显示意图、守卫及生命周期全部保留 |
| launcher `bootstrap_python_if_needed` | 重启解释器执行用户 launcher，继承用户启动方式 | 不强制隐藏此交互/visible 控制入口 |
| devTools Chrome 启动；web/e2e JS/PowerShell harness；scripts 手工 provider/rewind/MCP 诊断 | 浏览器/手工诊断或 frontend 测试工具 | 只盘点；不进入本轮前端/手工交互修改范围 |
| 其他专用 tests Terminal runner/IPC/composition/durable/lifecycle fixture、capture_output 诊断 | 存在尚无显式窗口参数的调用点 | 未全仓替换；本轮只覆盖上列普通后台及相邻受测 harness，不声称所有测试后代已统一 |

合计补 17 个生产后台创建调用点，并覆盖隔离入口和上列测试辅助调用点。
本轮不实施 private desktop、扫描/事后隐藏窗口、DLL/API hook、sitecustomize
注入或第三方 visibility 配置。

## 实测、代码推断与未验证

### 实测

- 真实 Windows 自有 Python 父→子→孙逐边应用公共策略。隐藏模式三层
  `GetConsoleWindow()==0`、`IsWindowVisible==false`；反向 visible 模式三层
 均有非零 console HWND 且 `IsWindowVisible==true`。只查询本进程 console HWND，
  不枚举/隐藏用户窗口。日志保留 launch PID、实际 Python PID、raw FILETIME 和退出码。
- venv redirector PID 与实际 Python PID 不同已明确记录，未混为同一身份。
- 新 probe 的异常终止只使用自有 Popen retained handle，并核验同 handle
  PID/raw FILETIME；正常路径 communicate 后退出码为 0。未注入 timeout 故障。
- 四种 bridge 的绝对 filename `--help` 在 repo 外 tmp cwd、无 PYTHONPATH 下通过。
- 隔离入口保留 stdout/stderr，并将子进程退出码 7 原样传回。
- 定向 suite：276 passed / 3 existing skips / 1 warning，33.51 秒。
- 最终真实 HTTP/WS suite：3 passed，19.35 秒；隔离 HTTP 队列与冷历史读取运行
  本地 fake-provider，无真实 provider。最终 `portReleased=true`、
  `ownedProcessesGone=true`、`remainingListenerPids=[]`。
- AST 语法检查 381 个 Python 文件通过；git diff --check 通过。

### 定向验证命令

解释器统一使用 `D:/project/Pan/.venv/Scripts/python.exe`，外层均为
`scripts/run_tests_isolated.py -- <同解释器> -m pytest`，清 PAN_*、临时存储、
分配临时 HTTP 端口。完整命令的用例集合如下，XML/log 与 source blobs 见 evidence.json：

```text
tests/test_job_window_policy.py tests/test_codex_takeover.py
tests/test_terminal_identity.py tests/test_terminal_guard.py
tests/test_test_isolation.py tests/test_terminal_registry.py
tests/test_launcher.py tests/test_remote_tunnel_api.py
tests/test_codex_adapter.py tests/test_claude_adapter.py tests/test_kimi_adapter.py
tests/test_opencode_models.py tests/test_wechat_spawn.py
tests/test_worker_oneshot_usage.py tests/test_worker_cli_session_binding.py
tests/test_background_jobs.py tests/test_rewind_core_ownership.py
-o addopts= -q -s -rs --junitxml=audit/job-child-window/20261010/targeted.xml

tests/test_real_queue_http_e2e.py tests/test_real_history_coldload_e2e.py
-o addopts= -q -s -rs --junitxml=audit/job-child-window/20261010/http-harness-final.xml
```

现有 3 项 skip 均来自 test_kimi_adapter.py（199/211/223 的本机 test workdir
缺失），没有新增 skip/xfail。warning 是现有 pydantic lifespan forward-reference。

### 失败证据与修正

- 首次定向 suite 的微信 fake 不接受 creationflags；补明确 fake 参数和隐藏/
  标准句柄断言后通过，没有放宽原断言。
- `http-harness.xml/log`、`coldload-recheck.xml/log` 保留两次冷历史收尾失败，
  失败 XML/日志仅去除 traceback 行末空白以通过 Git whitespace 校验，结果与断言内容未改。
  原断言为 `ownedProcessesGone`。失败后对记录 PID 的只读核验均已退出；fixture
  后续 finally 也持久化 gone=true。这不能用来把失败次数改写成通过。
- harness 原来在 Python 退出后立即核对整个树，其中自有 console host 可短暂
  仍在退出。新增树快照 raw FILETIME，在收尾时对 snapshot 匹配的同 handle
  仅 WaitForSingleObject（每个至多 2 秒），不新增终止/按名字找进程，不削弱最终
  all-owned-processes-gone 或任何性能断言。最终重新执行 3 项 HTTP suite 全通过。

### 代码推断与未验证

- helper 对单次创建提供 NO_WINDOW+STARTF_USESHOWWINDOW；参数不是后代强制策略。
  生产调用点参数覆盖不等于真实 provider、任意 Node/PowerShell 后代或 GUI 验收。
- 旧记录缺 hideWindow 默认 true；显式 false/重试保留可见选项；非 Windows
  kwargs 门控通过模拟 posix 的单元测试。未在真实 Linux/macOS 主机运行。
- 用户截图仅说明 Default Python console；未建立来源 PID/FILETIME/父链关联，
  无法确认根因。tests/test_codex_takeover.py 原路径仍只是可疑启动点。
- 未跑全库、remote CI、用户实用服务、真实 provider/账号或 MA 的另一树全量。
- 不保证 arbitrary third-party 后代隐藏、不宣称 detached flag 组合隐藏、
  不声称 Window API probe 证明独立 Job 的脱离寿命。
- docs/skills/pan/SKILL.md 已更新精确边界；本树不存在 .codebuddy skill 副本，
  未创建忽略的副本或修改全局安装 skill。

## 证据与 MA 验收入口

- [evidence.json](evidence.json)：本轮最终源码 Git blob 锚点、XML 数量、完整窗口
  probe 树与最终 HTTP cleanup 摘要。
- [targeted.log](targeted.log) / [targeted.xml](targeted.xml)：276 passed，现有 skip。
- [http-harness-final.log](http-harness-final.log) /
  [http-harness-final.xml](http-harness-final.xml)：真实隔离 HTTP/WS 与收尾证明。
- [http-harness.log](http-harness.log) / [coldload-recheck.log](coldload-recheck.log)：
  保留修正前的两次失败，不作为最终通过证据。
- [window-policy.log](window-policy.log) / [window-policy.xml](window-policy.xml)：
  前期 28 项 helper/窗口回归，最终新增入口日志回归包含在 targeted suite。

MA 可核对提交的源码 blobs 与 evidence.json、复核 Windows tree 的默认/反向可见
结果及测试 skip，分别接受上述范围或另立剩余专用 harness/第三方后代任务。
本报告不操作 MA 的验收状态或 .workflow。
