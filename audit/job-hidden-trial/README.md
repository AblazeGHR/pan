# 最小试用候选验证

交付 worktree：`D:/project/pan-worktrees/job-hidden-trial-20261015`；branch `trial/job-hidden-20261015`。
起始 main `097a356efa840247a7802141636fa0f1f53bfc9f`；验证期间 main 前进到 `393ee688c62c9dfd2e1fab5e4ae5ed2247f23a57`，仅在本交付分支安全快进至该新基线，未覆盖 dirty。其变更没有触及本任务源文件。TA 没有移动 main/practical。

## 源码溯源

| 产品源文件 | 研究来源／本次调整 |
|---|---|
| packages/jobs/desktop.py | c81aac0c：f14dc631 private desktop、c06dd77b 外部 guard、c81 retained root／suspended resume 和 DACL 接入的最终稳定内容 |
| packages/jobs/restricted_token.py | c81aac0c：同用户 DefaultDACL 拒绝 0x200、CreateProcessAsUser，无可见 fallback |
| packages/jobs/independent_runner.py | c81aac0c 相对当前 main 的 private desktop + token + owned guard 接入；没有 c1e7aaf9/9874e3cf 的实验 CLI/debugger |
| packages/core/background_runner.py | c81aac0c：hidden Windows 分支改用 DesktopCommand；visible/non-Windows 分支保留 |
| packages/jobs/window_guard.py | c81aac0c：仅 owned HWND 异步缓解；窄取 c1e7aaf9 的“先排队缩小再同步改 style”顺序及真实 API 请求结果记录；未取 geometry/DPI/cloak/alpha |
| packages/core/background_jobs.py | 当前 main + 本次实际入口发现的取消竞态修复：身份检查后原子记 cancelRequestedAt，runner 在锁内把接受取消后的退出转换为 cancelled，避免 failed/15 盖过取消，也不留永远 active 的 intent |

`tests/test_job_window_policy.py` 的旧 Popen mock 改为验证 DesktopCommand 选择及 context cleanup（c81aac0c 同一测试修正），保留 node IDs、visible/non-Windows 断言。新增取消竞态和 caller 消失测试，未削弱断言或新增 skip。

## 验证

真实启动使用 `JobStore.create → independent_runner`、`background_jobs.start → background_runner`、`start_scheduled_process → background_runner`，没有替换 launch/CPAS/取消／reconcile。仅 Session 存在性、外部 notice 投递和无关 scheduler 在自有 fixture 内隔离，未联系用户服务。日志、registry、kernel IPC、文件均在 owned pytest 临时目录。

- 首轮旧 policy mocks 的四项失败保留本地 `regression.xml`；移植已有对应 policy 测试后修复。
- 首轮实际 legacy cancel 得 failed/15，保留本地 `production.xml`。源码修复后的两条生产 cancel 原断言通过；增加确定性 race/canceller-disappears 用例。
- `regression-final.xml`：121 passed/0 failed/0 skipped，覆盖 token、guard、visibility policy、background jobs、independent service/bridge；此后增加两项 race 测试和三项生产生命周期／shell 场景，定向验证通过。最终汇总见 `verification-summary.json`，重叠批次不重复计数。
- `production-receipts-final.xml`：13 个真实 Windows 生产场景全部通过；`production-receipts.jsonl` 保存精简 PID/raw FILETIME、desktop、HWND placement、退出及清理事实。普通继承路径三代 CREATE_NEW_CONSOLE/AllocConsole+GUI、两路 stdio/exit7/cwd/双向文件 IPC、真实取消、外部 deadline→cancel、shutdown/recover、independent runner crash 和拒绝重领、legacy crash 边界、scheduled shell 均已验证。
- `test_hidden_job_token_preserves_peer_file_and_ipc_access`：真实文件、named event/mutex/section/pipe 和 peer PROCESS_SET_INFORMATION 拒绝；token/create WinError1314 fault injection 确认不 fallback。
- ConsoleWindowClass／ConPTY proxy HWND 的 visible 位不作真实显示像素证明。此候选不声称零帧；真实像素与 foreground 的历史反例完整保留在原研究树的 REPORT-SOL-COMBINED/OFFSCREEN/EXPERIMENTAL-RUNNER。
- legacy crash 的 survivors 明确断言为 alive，之后由测试 harness 使用已核验 FILETIME 的 retained handles 清理，不能称为 legacy 产品 crash cleanup 通过。independent crash 则是产品 kernel Job 自动清理，恢复 UNKNOWN。
- 本机 Windows 真实执行；非 Windows 路由由保留的 policy 测试验证，未在 Linux 主机跑 OS E2E。已知 pytest timeout config／Pydantic lifespan warnings 如实保留。

命令（普通短批次前台，无长实验）：

`python -m pytest tests/test_job_hidden_production.py tests/test_hidden_job_token.py tests/test_job_window_guard.py tests/test_job_window_policy.py tests/test_background_jobs.py tests/test_independent_job_service.py tests/test_independent_job_bridge.py -q`

生成 compact 生产事实时使用测试专用 `PAN_TEST_JOB_TRIAL_EVIDENCE` 环境变量，避免 conftest 正确清除应用 PAN_* 设置；它不是产品配置。

原研究 branch/worktree/08589480 和全部 DB/observer/DLL 证据未改历史、未删文件，没有搬入本候选。交付 diff 不含运行 DB、observer 日志、debugger/DLL/alpha/geometry，不新增第三方依赖。等待 MA 验收后决定 main→practical 试用，TA 不合并、不 push、不重启。
