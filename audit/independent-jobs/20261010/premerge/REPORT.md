# 合并前兼容性与回归验收

候选基线：`39d6e532`（父链含 `04f766b5`）；main/practical 合并前均为
`c6e504c0206b8d0de52f757bda145f4b09bae871`。使用 practical 的真实 venv
解释器，经 `scripts/run_tests_isolated.py` 清除继承 PAN_* 后执行。

## 扩展回归

20 个文件覆盖旧 background Jobs/API、统一 Jobs API、配置 reload、handoff
retarget、main lifecycle、完成记录保留、Session message、全部 scheduler
engine/dispatch/cron/API/store/MCP/integration、旧数据兼容、测试隔离，以及
新独立服务/桥接/部署测试。

- 首轮：384 passed / 1 failed / 2 skipped（32.04s），原文 `pytest.xml`。
- 失败：duplicate runner claim 的真机快速退出路径偶发 UNKNOWN。
  根句柄已 signaled 不等于 Job 会计查询已同步为空；此前只有单次查询。
- 修复：只在自然退出后有界复查（最多 0.5s），必须同时获得 active=0 与
  members=[] 才确认整树为空；真实剩余后代、查询未知/异常仍不确认。不杀进程，
  不改身份门、原子 claim、UNKNOWN 禁重跑或通知事务。
- 新增 6 个门控：延迟会计归零、两种真实/矛盾剩余成员、两种未知查询值、查询异常。
- 修后：**391 passed / 2 skipped / 0 failures / 0 errors**（34.35s），
  `pytest-fixed.xml` 独立解析为 393 cases。3 个既有依赖 warning 未改规则。
- 跳过分列：外部部署显式 opt-in（下面另跑）；cron timezone 缺 zoneinfo 数据库。
  未增加 skip/xfail，未删除或放宽旧断言。

## 修后重新验证外部宿主

`test_independent_job_deployment.py` 单独显式 opt-in：**1 passed / 0 skipped**
（21.32s），`host-fixed.xml` / `host-result-fixed.json`。
真实完整隔离 Pan 的正常关闭和自有 Job 整树终止两场景：外部 user Task Scheduler
宿主不属于 Pan Job，命令继续完成；重启后真实持久 Session 队列各一条通知并 ack。
临时任务已删除、宿主已退出。只操作自有资源；practical 8768 PID 34184 未变。

## 兼容性与合并边界

旧 JSON/Session/调度/Job 保留机制测试通过；默认 independent_jobs 禁用，旧路径
继续工作。明确 registry_root 保留本地路径，启用后旧 Job 优先、独立记录不由旧
reconcile 误处理。独立服务故障不偷偷 fallback 重复执行。

没有前端或总依赖变更，因此无需前端构建。此次只合并源码，不启用实用 config、
不安装永久宿主、不重启 practical、不 push。保留两个 canonical checkout 的既有
untracked 文档/依赖备份。未运行全库、浏览器交互、提供商、长期压力和独立定时调度；
结论限于以上实测范围，没有发现阻塞合并的回归或兼容问题。
