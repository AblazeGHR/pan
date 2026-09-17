# T-043 后端验收证据

工作树：`D:/project/pan-worktrees/jobs-backend-20260918`
分支：`feature/jobs-backend-20260918`
基线：`c5e0a19beac448f76b5415714446d794b68fec6a`

## 已实现

- `session-message` Job 与现有 Background Job Registry 共用持久 JSON、原子写入和跨进程 Job 锁。
- 一次性调度支持绝对 ISO-8601 `at` 或相对 `delaySeconds`；周期调度支持 `intervalSeconds` 或每周 `weekday`（周一为 0）+ `time`，可选 `timezone`。
- Job 持久化目标 Session、描述、正文、`source`、创建方 `sourceSessionId`、状态、下一次执行时间、最近投递结果和错误；服务重启恢复循环会重新排队过期的 stale claim，并投递到正常 Session send queue。
- MCP 提供 `agent_message_job_create/get/list/update/cancel`；正文只作为 Session 消息，不执行 OS shell。创建与编辑从 MCP 入口保留 `////by agent` 前缀和调用方 Session ID。
- `POST /api/sessions/broadcast` 与 `agent_send_many` 提供即时选中 Session 群发，逐目标复用 `worker.send_session`；定时群发明确未接入。
- `PATCH /api/background-jobs/{jobId}` 编辑未终态时间 Job；通用 Job 查询/取消列表同时返回进程 Job 与 Session-message Job。

## 验收命令与结果

- `python -m py_compile ...`：通过。
- `python -m compileall -q packages tests`：通过。
- `python scripts/check_pan_skill_tools.py`：通过，55 个 `@mcp.tool()` 与 skill 表一致。
- 聚焦回归：`tests/test_session_message_jobs.py`、`tests/test_background_job_api.py`、`tests/test_background_jobs.py`、`tests/test_mcp_handbook.py`、`tests/test_agent_naming.py`、`tests/test_source_metadata.py`、`tests/test_task_id_inheritance.py`：功能相关断言通过；组合运行中的既有跨进程 Registry race 偶发失败，单独连续 3 次通过。
- `pytest tests`：收集完成，1 个既有 `test_codex_quota_api` 文档断言失败；不是 T-043 改动路径。
- 全量 `pytest -q`：QQ 测试收集因环境缺少可选 `nonebot` 失败；未安装依赖。
- 隔离真实 HTTP：自有 `127.0.0.1:8793`，服务 PID `43816`；health、Session 创建、创建 Job、列表、PATCH、cancel 均返回成功。验证后已停止自有 PID，`8793` 已释放；未触碰 8768。

## 边界与未验证项

- UI Job 标签页、Create Job 窗口和具体控件交互按 DEC-004 暂缓；本提交未修改前端源码，前端 `pnpm build` 未运行（工作树没有 `node_modules`，未安装或修改共享依赖）。
- 定时群发不属于本阶段；即时群发后续可由 UI 复用 `POST /api/sessions/broadcast`。
- 未运行真实 Provider Worker 到期发送 E2E；消息发送已通过单元测试确认调用 `worker.send_session`，真实 HTTP 仅验证 Job 管理契约。
- 未修改 `Pan-main/.workflow`、`D:/project/Pan` 或旧 worktree；未 push，未操作 8768 或他人进程。
