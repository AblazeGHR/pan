# Hidden Job 异常生命周期预防性加固

基线 `6b1d7ad92c3b20048a0d3897e5a38adc328cbf24`；branch `fix/job-lifecycle-prevention-20261016`；worktree `D:/project/pan-worktrees/job-lifecycle-prevention-20261016`。不是把一般担忧当作孤儿已发生：以下先复现、再窄修。原研究树仍为 08589480，内容／历史未改动。TA 不移动 main/practical、不 push、不重启或探测用户服务／保护端口。

## 已有覆盖、实测缺陷和产品改动

1. 已有 independent 的运行期 runner crash 会关闭内核 Job 并回收子孙；controller/recovery shutdown 本就应保留持久 Job。保留这些行为，未强造修复。
2. 已有 legacy 真实 crash 测试明确断言子孙存活，本次基线复跑通过（即证实较弱边界），并由 owned retained handles 清理。隐藏 legacy 现在也持有 kill-on-close Job，正常 root 结束时确认终止剩余成员后关闭；异常 close 仍逐项尝试，不将未确认停止称成功。对应生产测试由旧 weaker-lifecycle 节点改名为 crash-recovery-and-cleanup，并把 ALIVE 断言加强为全部 DEAD；原反例及基线 XML 保留。
3. 新确定性 hook 只在测试 launcher 内：原生 CreateProcessAsUser 返回后、结果还没交回调用方时暂停，kill 精确 FILETIME runner。基线两路均留下 suspended child ALIVE，marker 未执行（baseline.jsonl / baseline.xml 两项红）。harness 用已验证的 retained handle 终止并确认 DEAD 后关闭，未留下反例进程。
4. 新 owned_process.job_startup_info 复用 JobObjectGuard.raw_handle，通过 STARTUPINFOEX 的 JOB_LIST 属性让隐藏任务在创建时入 Job，无事后 assign 缝隙。legacy/independent CPAS 均接入，原有 suspended、一次 resume、DACL、private desktop、输出和 cwd 保留。成员关系仍在恢复线程前验证；无保护 fallback 被禁止。显式 visible/non-Windows 路径保留原创建方式。
5. 确定性 Registry 竞态：runner 在 reconcile 的 liveness probe 中提交 completed/failed/cancelled，旧 reconcile 仍覆写该事实为 orphaned/failed（reconcile-red.xml 三项红）。现在恢复持锁重读 active 状态及相同 runner 身份，直接写入，不套用陈旧快照或嵌套锁。
6. 真 OS controller 在 Popen 后、发布 PID 前死亡：旧 legacy 任务仍活着但被恢复记 failed（publication-red.xml）。Windows hidden runner 现在在执行任务前，以实际 Python PID/FILETIME 和一次 runnerClaim 自行登记。parent setdefault 不覆盖该身份；重复 claim、UNKNOWN 和终态不启动新任务；已接受取消在 claim 阶段收敛 cancelled。现有记录字段兼容，新增字段为可选；取消／旧记录的原有 psutil 所有权检查未削弱。该修复只用于 Windows hidden 路径。

CI TA 的 `1781acd0` 不在指定基线，且其祖先还包含其他 CI/window-guard 改动。仅逐字复用其 packages/core/background_jobs.py 的 _kill_tree 修复及 tests/test_background_jobs.py 两条原测试：一名成员先退出时仍尝试其余 owned 成员。没有另写竞争版本，没有把 scripts/run_hidden_ci.py 或其整分支合进候选；MA 后续整合两分支应识别该相同补丁。

## 完整异常矩阵

| 阶段／故障 | 实证与结果 | Registry／重投语义 |
|---|---|---|
| base Python 业务模块导入前死亡 | 无命令子进程、marker 不执行 | legacy 下一次 reconcile failed；independent recover UNKNOWN，retry／同 Job run 拒绝 |
| venv 外层 redirector／内层 Python 导入前死亡 | 两种死亡位置分别测试；redirector 与 Python 均退出，无命令执行 | 同上；未靠杀外部 Terminal |
| CPAS 已返回但 Python 调用方尚未接收结果 | 旧两路 suspended child 存活；新两路 retained child DEAD、private desktop 消失 | 正常异常恢复，不留 running；独立 UNKNOWN 不重投 |
| 上述创建窗口 + venv 外层／内层死亡 | base/venv-outer/venv-inner × 两路 × pre-import/post-create 共 12 项 | 同上，保留 raw FILETIME／launch mode 事实 |
| token 构建／创建权限失败 | 既有 fault injection WinError1314，无可见 fallback | 明确失败，未执行 marker |
| JOB_LIST 属性不可用／更新失败 | WinError50 注入，两路均未调用 CreateProcess；native attribute list Delete 仍调用 | 明确失败，不退回创建后 assign |
| token.close 在创建后失败 | 既有两路真实 suspended handle DEAD、marker 未执行测试继续通过 | 清理错误可诊断，无成功伪报 |
| 已恢复并有三代 owned 子孙时 runner 被 kill/crash | 两路生产入口均验证 retained root/child DEAD、desktop 释放 | legacy failed；independent UNKNOWN、拒绝 retry/重复 claim |
| root 正常 exit0，子孙仍活着 | 三代树实际验证；legacy 终止剩余成员并确认 DEAD；independent 原有 UNKNOWN+guard 回收保留 | legacy completed/0 表示 root 退出且清理确认；独立 UNKNOWN 不被改成 completed |
| runner 等待阶段 Python 异常 | 两路三代树全部 DEAD、desktop 消失、原始 injected error 可诊断 | legacy failed；independent UNKNOWN |
| controller 的 recovery-loop shutdown／重建 | 原生产测试确认任务仍活着，再调用 cancel 整树结束 | 保留持久 Job，不把 controller stop 等同任务取消 |
| 隔离 controller 进程真实 kill，之后重建控制器 | 两路 runner/三代树在 host 死亡后仍 ALIVE；重新核对身份后 cancel，全体 DEAD | running 保持到合法 cancel，最终 cancelled |
| controller Popen 后／发布 PID 前死亡 | 新 legacy 自行 claim 后恢复保持 running；取消后 runner/三代树 DEAD | 不误报 orphan、不重复启动；claim 单次和 UNKNOWN 拒绝单测 |
| 独立 HTTP service stop/start | 既有动态 loopback 服务实测保留 runner、不重复 marker，最终终态 | 不触用户服务；独立恢复逻辑本已覆盖未改 |
| 取消与自然退出竞争／外部 deadline | 复用1781修复及既有真实 cancel/deadline 测试 | cancelled 收敛；argv API 仍没有原生 timeout 字段 |
| Job close／guard stop／thread/process/desktop close 失败 | 既有逐项清理和联合错误汇总测试继续通过；stop False 视未确认 | independent UNKNOWN、日志保留、无成功 outbox；legacy failed |
| PID 身份错误／复用、状态检查不可用 | 既有取消拒绝及恢复测试继续通过，不按裸 PID 清理外部进程 | 不重投 UNKNOWN，不把 failed 本身当作清理成功证据 |
| reconcile 与终态写入并发 | 三项确定性红→绿，保留已有终态与通知事实 | 不用陈旧 active 快照覆写终态 |

异常矩阵中的“kill”均是对测试自行创建、保留 process handle 并核对 raw FILETIME 的进程进行 TerminateProcess，未全局 kill／按名称杀进程。每项 finally 对 owned 残留再次检查、必要时精准清理并确认 DEAD 后关闭 handles。green creation/exception/controller 矩阵另外验证 private desktop 不再能以 OpenDesktop 打开（只接受 ERROR_FILE_NOT_FOUND，不把拒绝访问算释放）。没有修改用户 Terminal、input desktop 或全局 ACL。

## 验证和证据

- baseline.xml：5 项，3 passed/2 failed。两项失败证明 post-create 两路实际存活；legacy 原 weaker crash 边界通过也属于风险证据。
- reconcile-red.xml：3 项故障注入红；publication-red.xml：1 项真实 OS 红。均保留本地，不删除反例。
- regression.xml：**171 passed / 0 failed / 0 skipped**，38.266s。含 21 项本次生命周期矩阵、13 项原生产入口、4 项正式 MCP/HTTP/venv 入口、清理故障、token/visibility/background/service/bridge 回归，含原取消测试及1781两条测试。
- 后续加强 desktop/死亡后检查和 alive 前置断言后，postcheck-final.xml：**76 passed / 0 failed / 0 skipped**，9.677s。产品源码未在这两个绿批次之间变化。重叠批次不累计。
- 精简 final-verified-matrix.jsonl、production-final.jsonl 与 verification.json 提交；XML 本地保留并忽略，不带运行 Registry/DB、模型数据或 observer 大日志。publication 的红绿事实还由保留的精确测试节点及 XML 验证。
- 现有 pytest timeout config、Pydantic lifespan、Starlette TestClient deprecation warnings 如实保留。全部为真实 Windows 本机 Python 3.14.5 零模型进程；无部署用户服务操作。候选经过正常 hook 提交，等待 MA 验收。

复验命令：

`python -m pytest tests/test_job_lifecycle_prevention.py tests/test_job_hidden_production.py tests/test_job_cleanup_failures.py tests/test_hidden_job_token.py tests/test_job_window_policy.py tests/test_background_jobs.py tests/test_independent_job_service.py tests/test_independent_job_bridge.py tests/test_job_runner_venv.py -q`

## 可证明范围与剩余边界

[Windows JOB_LIST 文档](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute) 要求 Windows 10／Server 2016+；[Microsoft 对创建时入 Job 的说明](https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812/) 解释其避免创建后 assign 窗口。此候选不支持时明确失败，不为兼容旧系统静默降低回收契约；有冲突的 ambient/nested Job 限制也会明确失败。正常继承 owned 后代受控，强行在外部 broker 创建进程、其他用户服务、用户程序主动修改隔离边界仍不属于该 Job；不引入 DLL/debugger/Session0。

kill-on-close 对 runner 生命周期生效，controller 单进程死亡不应取消持久任务。若 controller 永不恢复，磁盘旧 running 不会自行变终态；承诺是恢复循环下一次 pass 收敛。legacy failed 仍沿用原语义，允许用户显式 retry；不会自动重新执行，不能把 failed 标志独立用作整树已清证明。

未测试断电、内核崩溃、精确停在 Windows 内核 CreateProcess 内部、全部 Windows build/x86/ARM64 或全部第三方 Python wrapper。测试在 native CreateProcess 返回边界停住，内部时序保证依赖受支持的 Windows JOB_LIST 契约。标准 CPython venv redirector 的外层／内层死亡已实测，任意 sitecustomize 在 Pan 业务模块导入前另起外部进程不在本次证明范围内。非 Windows 路由保留且有 policy 回归，未做 Linux OS crash E2E。

人眼不可察的既有隐藏策略保持，不追求零帧；主动 desktop 逃逸／外部 broker／异步 guard 兼容边界见 docs/job-hidden-window-trial.md。正常 root 结束后其 hidden descendants 现在会被收束，原来依赖 legacy root 退出后继续驻留的子 daemon 不再保留；这是本次整树回收加固的兼容变化。
