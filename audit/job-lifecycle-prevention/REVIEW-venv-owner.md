# 8f68b76d MA 复审：venv runner 身份证据与测试修正

在原 `fix/job-lifecycle-prevention-20261016` 工作树增量处理，保留 `8f68b76d` 和所有旧证据。本轮仅改测试/helpers、精简证据和报告，产品源码未变，未移动 main/practical、push、重启服务或触碰保护端口。MA 原记录 PID 的收尾由 MA 另行核验，本轮没有操作那些 PID。

## 红批次与根因

按 MA 原命令使用 `D:/project/Pan/.venv/Scripts/python.exe` 和 `scripts/run_tests_isolated.py` 复现，`venv-review-red.xml` 为 76 项、74 passed/2 failed/0 skipped：

1. 原 publication 用例稳定触发：Registry `runnerPid=47496`，`Popen.pid=41160`。原测试把实际 Python runner 与 venv redirector 视作同一进程；前一批 base Python 验证不足以证明生产 venv 也满足该断言。产品 claim 本来就应记录实际 Python；不能把 claim 改回 redirector 来迁就断言。
2. 另有一次独立 runner `venv-outer/post-create` 恢复得到 starting。旧测试只等待 Popen redirector 退出，没有确认实际 Python 已死亡，因此缺少宣称 runner-death 的前提；该失败本身不能证明恢复实现错误。新测试在 kill 前证明 actual runner ALIVE、身份与 Registry 一致，recover 保持 active；kill 后以独立 retained handle 强制确认 actual runner DEAD，再断言 UNKNOWN/拒绝 retry。若杀 outer 后 actual runner 超过 5 秒仍存活，仍明确失败，未改成允许 starting 或重试恢复直到通过。

红批次的 publication finally 还命中过 TerminateProcess 与自然退出的竞争。测试 kill helper 现在只有在 retained handle 确认 DEAD 时才接受 TerminateProcess 的失败；若仍活着则报 winerror、继续失败，没有忽略真实清理错误。

## 新身份断言（不是只比较两个 PID 是否相等）

publication 用例显式分 base/venv 两项，runner 和 task 都使用指定解释器；controller host 固定 base Python，使故障杀点确实是 controller 而非它的 wrapper。仅在 helper 中覆盖解释器选择结果，仍走实际 legacy start、runner 模块、claim、private desktop/Job Object、Registry、reconcile 和 cancel；不写用户 Python 配置。

- base 分支必须 `claim PID == Popen PID`，raw FILETIME 也必须相等。
- venv 分支必须二者不同，且实际 runner 的父进程必须正是 retained redirector；redirector 的 OS executable 必须是指定 venv。
- 任务 root 在三代树存活时记录到 runner 为止的祖先链；实际 runner PID/raw FILETIME 必须与 claim 完全相等，其 OS executable 必须是 base Python。原生 venv task redirector 也记录在链中。
- 用 retained handle 再次验证 raw FILETIME 与 Registry 时间字段的换算；错误 creation time 必须无法取得 ownership。杀 owned controller 后两次 reconcile 均为 0，身份不变，runner/redirector/三代任务仍 ALIVE。
- 调用真实 cancel 后确认三代任务、实际 runner、redirector 全部 DEAD，Registry cancelled，private desktop 消失。finally 持有并清理实际 runner 及 redirector，不再只持有 wrapper。
- creation-edge 的 `base` 现在显式使用 `sys._base_executable`；旧代码在 venv pytest 中把 `sys.executable` 误称 base。每个故障点现在也保留 actual runner 的 PID/FILETIME 与死亡证明。

## 双路径实证

| pytest 解释器 | 结果 | publication 的任务/runner 解释器 |
|---|---|---|
| `D:/project/Pan/.venv/Scripts/python.exe` | 94 passed / 0 failed / 0 skipped，26.167s | base + **实际 production venv** 两项 |
| `C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe` | 94 passed / 0 failed / 0 skipped，见 review-verification.json | base + **实际 production venv** 两项 |

两批重叠不累计。范围是 MA 原三文件（新增一个参数节点后 77 项），加 13 项真实 hidden production 和 4 项 MCP/HTTP/venv 入口。未再机械重跑其余无关用例。原有 warnings 保留；无 skip/xfail/阈值放宽。

生产 venv 批次的精确身份（raw FILETIME）示例：

| 路径 | Popen PID / FILETIME | claim PID / FILETIME | 任务 root |
|---|---|---|---|
| base | 36300 / 134365559848456073 | 36300 / 134365559848456073 | 46568 |
| production venv | 44872 / 134365559856409140 | 42100 / 134365559856640298 | 44532，经 task redirector 48076 到 runner 42100 |

每批 20 条 owned receipts 保存在 `venv-review-green.jsonl` / `base-review-green.jsonl`，包含实际 runtime、task ancestry、精确身份、取消与清理事实。XML 均保留本地（沿用 *.xml 忽略规则），精简批次统计提交到 `review-verification.json`。最终按 helper 命令名只读查询没有残留；没有全局 kill、按名称 kill、操作用户 Terminal 或切 desktop。

## 复验

MA 原命令现应为 77 项全绿：

```powershell
D:/project/Pan/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan/.venv/Scripts/python.exe -m pytest tests/test_job_lifecycle_prevention.py tests/test_background_jobs.py tests/test_job_cleanup_failures.py -q --tb=short
```

本轮的 94 项批次在 wrapper 隔离之后由 pytest 子进程设置 `PAN_TEST_LIFECYCLE_VENV=D:/project/Pan/.venv/Scripts/python.exe` 和证据路径，再调用 pytest；这两个测试选项不传给部署服务。base 批次同样明确指定生产 venv，未把临时 venv 结果冒充生产环境。完整 invocation 保存在 review-verification.json。通用运行时没有显式路径且当前 pytest 不是 venv 时，会创建 owned 临时 venv，正常执行两项而非 skip。

本轮证明的是 8f68b76d claim 的实际所有者身份及这些 OS 路径的恢复/取消，不增加 Windows build/架构、任意第三方 redirector、断电或外部 broker 的保证；其余边界沿用原 REPORT。候选仍等待 MA 独立验收。
