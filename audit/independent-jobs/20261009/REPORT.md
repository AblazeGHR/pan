# 独立 Job 第一阶段交付

工作树：`D:/project/pan-worktrees/independent-job-service-20261009`。
分支：`feature/independent-job-service-20261009`。
起点：`c6e504c0206b8d0de52f757bda145f4b09bae871`。

## 实现与验收

一个 gpt-6-luna high TA 完成执行内核；MA 完成接线与关键验证，不另派审查 TA。
最后一次组合命令（隔离环境，实际部署 venv 解释器）：

```text
D:/project/Pan/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan/.venv/Scripts/python.exe -m pytest tests/test_independent_job_service.py tests/test_independent_job_bridge.py tests/test_jobs_api.py tests/test_background_job_api.py tests/test_background_jobs.py -o addopts= -q --tb=short --junitxml=audit/independent-jobs/20261009/bridge/pytest.xml
```

结果：**109 passed，0 skipped，21.38s**。3 个依赖警告分别来自 Starlette/httpx、
anyio BlockingPortal alias、pydantic_settings 前向注解；未为本功能改依赖。
JUnit 原始结果在 `bridge/pytest.xml`。早期独立 HTTP 三负控另列于
`bridge/http-gates.xml`；不要与最后全套计数相加。

已实测：

- 独立 HTTP/SQLite 注册、幂等键、原子 runner claim、快速退出与 outbox ack。
- Windows suspended→assign→成员确认→resume；整树取消与错身份零取消。
- 服务停止/恢复时活 runner 不重发；UNKNOWN 不自动重跑或直接 retry。
- 预启动服务为模拟 Pan 的兄弟进程：模拟 Pan 自有整树终止后命令仍完成。
  **这不是实用 Pan 或已安装 Task Scheduler 的验收**。
- 真实统一 Jobs create→服务命令→outbox→真实 Session 持久队列→ack。
- ack 丢失后重新加载磁盘 Session，重拉结果仍只有一个 eventId。
- 配置禁用不启动服务；服务故障不 fallback；明确 registry_root 保留旧路径。
- Jobs GUI 列表/详情读取独立记录；未实现的元数据修改/删除明确拒绝。
- 内部 HTTP Origin/Host/非 JSON 拒绝在创建前，任务列表仍为空。
- 子命令清除继承 PAN_* 与 PYTHONPATH。

静态检查：`git diff --check` 通过；注册脚本 PowerShell parser 0 error。
终态只读进程扫描：independent_service/independent_runner 候选 **0**。
未按名称清理任何进程。main 的三个既有 untracked 项保留未动。

## 源码 git blob 锚定

| 源文件 | blob |
|---|---|
| independent_service.py | 507bb75ffe35ac7dca44c11a44cd210b663989ec |
| independent_runner.py | da71d36440187d6fa080ef9aed8871518665ea85 |
| independent_client.py | cbd8d8e1fa584194b7d2b09129c9ff7be6f6b2b8 |
| pan_bridge.py | 98acc8f67582f4aae7c0e428675fd351c86dfe5b |
| background_jobs.py | a9d0670ec7b2e9e6d51e9a81e3fc512dc1bee75c |
| jobs/api.py | 6dffb3fa151d019c6c3996bb264e423b35c551aa |
| test_independent_job_service.py | c8abae5e1ea19c1d113718da42c6a727414a8011 |
| test_independent_job_bridge.py | 050052cd1ece021742f1308e658432e225d90feb |

## 部署和未实现边界

默认关闭；没有改实用 config，没有注册计划任务、监听 8768、重启服务、合并
main/practical 或 push。注册脚本仅完成语法验证，未执行安装。下阶段需明确
实用 Python/数据根/端口后，验证外部登录宿主以及真正 Pan 重启后的存活和投递。

未迁移旧任务或定时调度；独立记录改名、删除、转目标暂不支持。UNKNOWN 保守
保留待人工核验，未实现通用修复入口。机器关机/注销、宿主或 runner 的强制
整树终止、所有祖先 Job 布局、POSIX、长稳、大日志容量、远程/多用户鉴权、
完整前端交互及全库未验收。不能承诺任意命令的副作用 exactly-once。

用户级 Task Scheduler 宿主与 Pan 的祖先树分离；不代表 Stop-ScheduledTask
或外部 taskkill /T 不会终止宿主的后代。只承诺本阶段已测布局，不泛化。
