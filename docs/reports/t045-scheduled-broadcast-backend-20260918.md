# T-045 定时群发 Job 后端交付记录

## 范围

- 在既有 Background Job Registry、单 Job 锁和恢复循环中增加
  `kind="session-broadcast"`，没有新增 scheduler、Runner 或 CLI stdin 路径。
- `targetSessionIds` 在 API/Core 两层去重并保持首次出现顺序；一次 occurrence
  按顺序调用既有 `worker.send_session`，每个目标独立记录结果，失败不阻断后续目标。
- Job 分开持久化 `creatorSessionId` 与接收目标 `targetSessionId`/`targetSessionIds`；
  Agent MCP 创建会同时保留 `sourceSessionId`/`////by agent` 来源语义，但 creator 不会
  被错误当成接收目标。定时消息 Job 不自动把终态通知复制回 creator。
- 复用既有 once `at`/`delaySeconds`、interval、weekly `weekday`/`time`/`timezone`
  规范化、编辑、取消和 stale-running 恢复。周期恢复只补一个当前 due occurrence，
  随后从本次发送/恢复时间计算下一次，不回放所有漏掉次数。
- 扩展既有 `/api/session-message-jobs` 与
  `agent_message_job_create/get/list/update/cancel`：单目标旧契约保持兼容，
  `targetSessionIds` 提供定时群发入口。MCP 创建/编辑继续做 managed 权限检查、
  保存 `sourceSessionId`，并在文本入 Job 前保留 `////by agent` 前缀。
- 一次性群发 Job 全成功为 `completed`，混合结果为 `completed` 且
  `lastDelivery.status="partial"`；全失败为 `failed`。周期 Job 无论部分失败或全失败
  都保留 `scheduled`，在 `lastDelivery.results`/`lastError` 中保存逐目标事实。

## 验收证据

- `tests/test_session_message_jobs.py`：目标列表去重/稳定顺序、逐目标失败隔离、一次性
  结果持久化、周期运行、stale-running 重启恢复、只补一次、编辑/取消和二次扫描幂等。
- `tests/test_background_job_api.py`：HTTP 定时群发入口、目标校验、MCP 前缀与
  `sourceSessionId`、多目标权限拒绝。
- `D:\project\Pan\.venv\Scripts\python.exe -m pytest -q`（T-045 相关 183 项）：通过。
- `D:\project\Pan\.venv\Scripts\python.exe -m compileall -q packages tests`：通过。
- `D:\project\Pan\.venv\Scripts\python.exe scripts/check_pan_skill_tools.py`：通过，
  55 个 MCP 工具、48 个一等工具、7 个兼容别名与 SKILL 清单一致。
- `git diff --check`：通过。
- 独立 Runner Job 的自动终态通知使用 queue item 的结构化 `envelope.jobId`，并保留
  status、target(s)、creator；无 creator 的系统通知使用 `automation` 来源标签。

## 未验证边界

- 未启动 Pan 服务、未触碰 8768，也未启动/停止/操作其他外部进程。
- 未做真实 HTTP/WS、真实 provider/CLI、MCP stdio 实例或浏览器验证；相关证据为
  单元/API 合约测试和静态检查。
- Job UI、Workspace UI 仍不在 T-045 范围内，DEC-004/DEC-005 不在本提交解除。
