# 独立 Job 第一阶段：Pan 接线与启用边界

实现位于独立工作树；默认关闭，不迁移旧 Job，不安装实用宿主。

## 启用

必须由 Pan 之外的用户级宿主启动服务。不能从 Pan 的生命周期或 MCP 请求中
用 Popen 启动它，再据此宣称隔离了 Pan 的进程树。

`scripts/register_independent_job_host.ps1` 提供显式注册的用户登录任务：

```powershell
./scripts/register_independent_job_host.ps1 -Python 'D:/path/to/python.exe' -DataRoot 'D:/path/to/job-service-data' -Port 8795 -WhatIf
```

确认路径、账户、端口后，去掉 `-WhatIf`，可选 `-StartNow`。使用交互用户账户，
不切换为 SYSTEM；注销、Windows 关机和外部强杀不在存活承诺内。代码目录需
保持存在，不要注册临时验收树后删除它。已有同名任务拒绝覆盖。
脚本将所选环境的 python.exe 换为同目录 pythonw.exe，避免独立宿主弹出
控制台窗口；不存在配对解释器时拒绝注册，不回退到其它 Python 环境。

Pan 配置显式加入：

```json
{"independent_jobs":{"enabled":true,"endpoint":"http://127.0.0.1:8795"}}
```

不自动修改 config.json。宿主数据根只来自绝对 `--root`，不借用 Pan 的 Job
环境变量。服务只接本机受信调用；此阶段不是远程鉴权方案。
内部 HTTP 要求准确的 127.0.0.1 Host、无 Origin、POST 为 application/json，
拒绝浏览器来源与 DNS rebinding。此门不替代同机进程的账户级鉴权。

## 路由与恢复

- 新的通用 argv Job 在启用后交给服务；已存在的本地记录继续由旧内核负责。
- 定时任务、Session 任务、广播和 provider 不迁移。显式 registry_root 的测试/
  内核调用保持本地，避免污染实用目录。
- 服务不可用时不 fallback、不产生第二次本地执行。创建请求超时不等于任务
  未启动；应检查服务记录，不盲目重发没有幂等键的业务命令。
- 独立 Job 带 `executionOwner=independent-job-service`；Pan 不用自己的进程
  reconcile 修改它的执行事实。查询、取消与重试回到服务。
- 统一 Jobs GUI 列表/详情及后台命令 MCP 共用这条路由。此阶段未实现独立
  记录的改名、转目标、删除，统一 API 明确返回 unsupported_operation，不
  在本地写出第二份执行事实；不会把“未支持”假称“已删除”。
- 结果 outbox 在 Pan 不在线时保留。恢复循环先调用已有持久通知入队接口，
  成功后才 ack。稳定 eventId 支持重复拉取后的去重；Session 已删除则不 ack。
- Pan shutdown 不停止宿主、不隐式取消其任务。停止宿主和取消任务是不同操作。

## 验收分层

对象/HTTP 测试与手动注册脚本不是实用 Windows 登录宿主验收。首次部署还须
核对任务计划的运行账户和父进程、启动就绪、Pan 整树退出后的任务存活、Pan
恢复后的单次投递。2026-10-10 已用临时同用户计划任务和隔离完整 Pan 完成
正常关闭/Job 整树终止后的命令存活及持久单次投递；证据在
`audit/independent-jobs/20261010/`。临时任务已清理，不等于实用部署已启用，
不迁移线上存量。
