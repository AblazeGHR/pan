# Job 默认隐藏窗口验证

基线 main `4b166bd3`，独立工作树 `job-window-default-20261010`。
实现范围与限制见 `docs/design/JOB_WINDOW_POLICY_20261010.md`。

## 本轮实际结果

- Python：`test_job_window_policy.py`、`test_background_job_api.py`、
  `test_background_jobs.py`、`test_independent_job_service.py`、`test_jobs_api.py`
  合计 **115 passed**，17.93 秒，3 条依赖警告。
- Windows 真实短命子进程：隐藏时 `GetConsoleWindow()==0`，显式显示时非零；
  两项均通过。显示正控会短暂创建一个窗口，这是显式选项的预期效果。
- Job 前端 5 文件 **12 passed**；受影响 TypeScript/ESLint 校验 exit 0。
- `git diff --check` 通过。

## 未混称通过

较早包含 `test_independent_job_bridge.py` 的运行出现 HTTP 拒绝连接被 Windows
重置（WinError 10053），96 passed / 1 failed。该入口 gate 未在本批修改，
此处保留为待基线/整体验证核对项，不把它混入 115 项全通过口径。
未运行完整 Pan 服务，不重启现有服务；Codex 功能整体验证由另一冻结 Job 执行。

旧数据不重写；旧 payload 的幂等键默认隐藏兼容与显式显示重试均有回归。
没有改变独立 runner 的身份核验、Job 所有权、取消或通知链。
