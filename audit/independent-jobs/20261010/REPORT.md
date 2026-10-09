# 外部用户宿主与完整隔离 Pan 重启验收

基线 `04f766b5`，独立树 `independent-job-service-20261009`，MA 直接执行。
本批不是 main/practical 合并，也不是实用 config 启用。

## 最终实测

`tests/test_independent_job_deployment.py`：**1 passed / 0 skipped，20.90s**。
这一项包含两条完整场景，各启动/停止两次真实 Pan HTTP app/lifespan：

| 关闭方式 | 命令在 Pan 离线时完成 | 恢复后持久队列 | outbox |
|---|---|---|---|
| Pan 内部 shutdown → 正常 lifespan 收尾，exit 0 | 是，命令 exit 0 | 对应 eventId 恰 1 条 | ack 后为空 |
| 自有 Pan Job Object 整树终止，非零退出 | 是，命令 exit 0 | 对应 eventId 恰 1 条 | ack 后为空 |

没有替换生产执行、recovery、Session enqueue 逻辑。唯一 test harness 接线是
把配置与存储指向临时根；目标 Session queue_paused=true，避免调用 provider。
这是**持久入队一次**，不声称 Agent/provider 已消费。Pan 新进程读取同一临时
Session 数据；通知由生产恢复循环执行，不是测试直接调用 enqueue。

外部宿主真实由 Windows Task Scheduler 注册并 StartNow，运行账户是当前用户、
Interactive/非 SYSTEM；动作最终选同一 venv 的 pythonw.exe。监听 PID 保留
句柄，读取 raw FILETIME，祖先链在最终 JSON 中；链路为 pythonw shim →
svchost → services → wininit。对四个 Pan Job 都核验宿主不属于其成员。
因此本轮不只是“两个兄弟 Popen”的模拟布局。

`host-result-final.json` 保存身份、场景、退出与入队事实；
`host-test-final.xml` 保存最终 JUnit。`host-result.json`/`host-test.xml` 是早期
console 解释器的成功场景；windowless 同名后缀是中间核验，不与最终计数相加。
三份成功证据没有覆盖第一阶段的 109 项结果，本批未重复其全套。

## 部署脚本修正

用户登录任务使用 python.exe 会存在控制台宿主形式。本批改为选同目录
pythonw.exe，缺少配对解释器就拒绝注册，不偷换 venv。其余注册语义不变。
这里核实的是 windowless 解释器和真实进程链，不是浏览器/UI 截图验收。

验收用例仅在 `PAN_TEST_INDEPENDENT_HOST_ACCEPTANCE=1` 时执行；默认普通
pytest/CI 不注册计划任务。Windows Task Scheduler、当前用户交互登录和
PowerShell 7 是此显式部署用例的前置条件，缺失时本次验收应失败而不是假通过。

```powershell
$env:PAN_TEST_INDEPENDENT_HOST_ACCEPTANCE='1'
$env:PAN_TEST_INDEPENDENT_HOST_REPORT='<absolute owned output path>'
<deployment Python> -m pytest tests/test_independent_job_deployment.py -o addopts= -q --tb=short
```

conftest 保留显式 PAN_TEST_*；harness 为子进程清除所有继承 PAN_* 与 PYTHONPATH，
仅设置测试自身路径。宿主始终显式 --root，临时目录不借用实用 Job 注册表。

## 开发期工具错误与清理

首遍失败是 harness 将 `param($n)` 与参数拼到 pwsh -Command 后，未形成脚本
参数调用，导致 $n 为 null；不是服务缺陷。改为私有环境参数。该遍创建的临时
任务先核对准确 TaskName 和 Action，再 Stop/Unregister；没有广杀进程。

最终 finally 删除准确自有临时任务、终止准确自有 Pan Job，确认 retained
服务句柄 signaled 后关闭。终态只读扫描：自有验收 host/runner/Pan 候选 0，
临时 PanIndependentAcceptance_* 任务 0。8768 仍由原 PID 34184 监听，未请求
实用实例关闭，也未修改实用配置。main 三个既有 untracked 项保持原样。

## 源码锚定与未验证

| 文件 | git blob |
|---|---|
| scripts/register_independent_job_host.ps1 | 0b3fac7c1d33ffdb93bba34938a114e99c163809 |
| tests/test_independent_job_deployment.py | 82137dc5d7429869ff0635a9f538ba81bcd2f2f6 |
| isolated_pan.py | c35be2537be29be808430fa17b1aa644b980b161 |

尚未永久安装、合入 main/practical 或修改其 config；未测试用户注销/再登录的
自动触发、机器重启/掉电、所有外部 Job 布局、长稳、provider 实际消费、前端
交互、POSIX、跨账户鉴权、定时任务/旧数据迁移。Stop-ScheduledTask/宿主强制
整树终止仍可能终止其后代，本轮保证的是 **Pan 的停止不连带该外部宿主**。
