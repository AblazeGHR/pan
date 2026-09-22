# T-FRONTEND-COLDLOAD 改进任务（2026-09-21）

## 用户目标

在独立 worktree 开始冷加载性能改进；BE-3 必须完成真实隔离 E2E；另建独立前端集成 worktree 实施第一阶段前端优化。不得操作 8768，不得 merge/push。

## BE-3：事件循环与冷读

- Session：`ses_ec1b3b3bf1db3402`
- Worktree：`D:\project\pan-worktrees\be3-eventloop-e2e-deepseek-20260921`
- Branch：`feature/be3-eventloop-e2e-deepseek-20260921`
- 基线：`591367a65f88e9d5270e8e99920ef5448d1d68c9`
- 目标：将 history/list 同步磁盘 IO 与 JSON 解析移出 FastAPI 事件循环阻塞路径，保持 API、分页、持久化、锁和 WS/stream 语义。
- 必须覆盖：并发读/写竞争、API 语义回归、真实 FastAPI/Uvicorn HTTP、真实 dashboard WS/stream、足够大的 JSONL 冷读、冷读期间事件延迟和恢复、进程树与数据根清理。
- E2E：只能使用自有隔离端口 8767/8765，先核对 PID/checkout；不得请求或操作 8768。报告需给出原始证据路径、端口/PID、启动/测试命令、停顿样本与判定。目标是无持续超过 50ms 的事件循环停摆；环境噪声须保留原始数据并单独说明。
- 交付：功能 commit、定向测试、完整 E2E、全套测试状态、未验证边界；不修改 `.workflow`、不 merge、不 push。
- 验收结果：通过本次独立交付验收。commit `5110f28f7d4e34dd895646843ef3c28836cd1a99`，工作树 clean；定向测试通过；真实隔离 8767 HTTP/WS E2E 通过。全套 pytest 为 1165 tests、1 个既有失败、0 errors、4 skipped；失败已在未改动基线复现。
- E2E 证据：`D:\project\pan-worktrees\be3-evidence-deepseek-20260921\`，覆盖 120,000 行 JSONL、8 并发 HTTP、真实 heartbeat、`/ws/agent` stream、内容/顺序/重连；`portReleased=true`、`ownedProcessesGone=true`。
- 未验证：真实 provider/CLI、生产规模与 8768、Linux/macOS、前端 React 行为；一次性 store 索引加载仍有 69–151ms 尖峰，显式全量 Session detail 读路径未改动。

## 前端集成：FE-1/FE-2/FE-4

- Session：`ses_934398f750d4b86b`
- Worktree：`D:\project\pan-worktrees\frontend-integration-deepseek-20260921`
- Branch：`feature/frontend-integration-deepseek-20260921`
- 基线：`591367a65f88e9d5270e8e99920ef5448d1d68c9`
- 范围：细粒度 Zustand selector、SessionItem 派生计算 memo、focus/visibility 恢复请求合并；保持多选、拖拽、分组、manager 树、断线恢复和 freshness 语义。
- 明确不含：FE-3 虚拟化、BE-3、协议变更和无关 T-062 候选。
- 交付：定向 Vitest/RTL、TypeScript/build/lint（按环境可用性分别记录）、功能 commit、基线失败和真实浏览器/服务未验证边界；不操作 8768、不 merge、不 push。

## 当前状态与下一事件

- BE-3 TA 已完成：commit `5110f28f7d4e34dd895646843ef3c28836cd1a99`，worktree clean；真实隔离 HTTP/WS E2E 已通过。
- 前端集成 TA 已完成：commit `b324aa1b4c30ebd6a4345ecdc1750c170a9dff5b`，worktree clean；web 全套 69 files/593 tests、TypeScript 和 build 通过，lint 仅有既有未修改文件错误。
- 前端真实浏览器/服务层未验证；BE-3 的生产/8768/provider 层仍未验证。
- MA 等待报告，不轮询中间进度。
- 两个 TA 交付均已完成独立 worktree 验收；后续如需进入 main，需单独进行 MA 整合、回归和用户/开发者验收，不由本任务自动执行。

## 2026-09-22 选择性整合更新

- 选择性整合 worktree：`D:\project\pan-worktrees\frontend-consolidated-20260922`；branch：`feature/frontend-consolidated-20260922`；提交：`cba9df0d143127b10500e237b0468a80f521b18d`。报告：[FRONTEND_INTEGRATION_REPORT.md](<D:\project\pan-worktrees\frontend-consolidated-20260922\FRONTEND_INTEGRATION_REPORT.md>)。
- `94b62e3` 选择性覆盖 BE-3 的冷读事件循环变更，`44b9ccd` 选择性覆盖 FE-1/FE-2/FE-4，`6534682` 在修复后的 store 上覆盖 queue edit/send contract；原始 `5110f28` 与 `b324aa1` 仍作为独立证据/来源保留，不等于已合入 main。
- 状态：TA 已完成，MA 已验收选择性整合范围；`main@591367a65f88e9d5270e8e99920ef5448d1d68c9` 尚未合入，尚未 push，尚未真人验收。Vitest 649/649、TypeScript/build、消息探针 9/9、reconcile 3/3、event-loop 13/13、120k 冷历史 E2E、Chromium CBC strict 8/8 通过；全库 pytest 仅有既有 Codex quota docstring 失败。
- 未验证/后续：真实 provider、生产 8768；Steer receipt 服务端稳定请求身份与 receipt 查询协议仍未实现。BE-3 独立 worktree 的完整 E2E 证据继续保留，不因其中一部分已被选择性整合而标为可清理。
