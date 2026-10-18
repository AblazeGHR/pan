# 未选中 Session 与 Job 实时标识修复

## 交付状态与边界

- 工作树：`D:/project/pan-worktrees/live-session-job-indicators-20261018`
- 分支：`fix/live-session-job-indicators-20261018`
- 基线：`d70b148ebd2b002b0b1972fbeb10f2d1a2a762bc`
- 状态：修复、回归、隔离浏览器/API 验证完成；提交交 MA 验收并负责快进。
- 未合并、未 push，未修改 main/practical，未访问 8767/8768，未重启实用服务。
- 前端依赖通过本工作树 junction 只读复用 `D:/project/Pan/packages/web/node_modules`；未安装依赖。Python 使用现有 `D:/project/Pan-main/.venv/Scripts/python.exe`，测试数据均隔离。

## 源代码根因和修复

1. `packages/web/server.py::_attach_session_summary_patch` 未给完成的 `worker.stream` 附带最新 Session summary。`useWebSocket` 也未摄入 status/result/stream 的嵌套 Session。后台 Session 的 history revision、display preview 因而等待后续列表或选中读取。现在完成块从已持久化 history 边界带出 authoritative summary，并即时摄入对应 Session；delta 保留轻量事件。
2. `queue.item_delivered` 先更新 summary、随后 `appendDeliveredMessages` 又以编排输入覆盖 `lastMessage`，且增加计数。receipt 可能晚于 assistant 到达。现在在同一次 store mutation 内合并 delivery history 和后端 summary，用既有 summary revision 保护新预览和计数；historyEpoch/historyRevision 也纳入保护。没有消息行的 receipt 仍摄入 summary。用户直接输入与 agent/report/automation/system_prompt 原有来源和 queue/message identity 保留。
3. 旧预览路径只节流更新 `lastMessage`，遗漏 display/assistant preview。移除该 500ms timer；合法 live text 即时更新，存在服务端 summary 时由它提供完整投影。没有增加轮询、定时器、强制刷新或重挂载。
4. `background_jobs` 的 agent API 和 message/scheduler 状态机直接提交 registry，缺少统一 Job 广播；进入 Job 页面才读到新值。统一在成功 registry commit 后发布冻结的完整 public Job 快照，覆盖 create/schedule/claim/start/terminal/target。隐藏的 scheduled child 不进入公开列表；后台进程终态沿用原有严格 retry/ack recovery，保持通知与队列语义。
5. `jobStore.upsertJob` 原来无条件替换快照。现在同一 server epoch 的事件序号去重，Job 自身 updatedAt 防止新 transport cursor 携带旧持久化快照而回退，并保留删除 tombstone。Pin 是独立 metadata writer，旧 pin 响应只更新 pin，不覆盖较新的生命周期事实。
6. `SessionItem` 和 `ViewEntryBadge` 原有 primitive selectors 正确订阅同一个全局 jobStore；无需修改组件 memo 或颜色/优先级。回归直接渲染真实组件证明 ingestion 即可驱动它们。

## 验证结果

以下检查退出码均为 0，无 skip：

| 层次 | 结果 | 本工作树证据 |
| --- | --- | --- |
| 全量前端 | `pnpm exec vitest run --no-cache`，165 files、1968 passed | `artifacts/live-indicators-vitest-full.log` |
| 新增 hook/store/component 回归 | `liveIndicators.test.tsx` 9 项，包含 4 种注入来源、跨 Session、空 receipt、3 种 Job | 包含在全量结果中 |
| Python 相关扩展回归 | 240 passed，2 条依赖 deprecation warnings | `artifacts/live-indicators-python-expanded.log`、`packages/web/test-results/live-indicators-python-expanded.xml` |
| ESLint | 改动的 6 个前端源码/测试文件通过 | `artifacts/live-indicators-lint.log` |
| 生产构建 | `pnpm build`：TypeScript + Vite + 72 个压缩资源校验通过；有 chunk size 提示 | `artifacts/live-indicators-build.log` |
| Python 编译 | core Job、web server、隔离 E2E fixture compileall 通过 | 命令退出码 0 |
| 补丁空白检查 | `git diff --check` 通过 | 命令退出码 0 |

Python 使用隔离入口，测试集合为：

```powershell
python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_session_message_jobs.py tests/test_background_jobs.py tests/test_session_summary_projection.py tests/test_jobs_api.py tests/test_handoff_job_retarget.py tests/test_scheduler_api.py tests/test_scheduler_engine.py tests/test_scheduler_dispatch.py tests/test_scheduler_integration.py tests/test_scheduler_store.py -q -o addopts= --junitxml=packages/web/test-results/live-indicators-python-expanded.xml
```

## 真实隔离 Chromium / API / WS 证据

复现命令（先构建 React）：

```powershell
cd packages/web
$env:PAN_E2E_PYTHON='D:/project/Pan-main/.venv/Scripts/python.exe'
node e2e/live-indicators.e2e.mjs
```

最终验证 runtime：`packages/web/test-results/live-indicators-1792314030298`。服务实际 PID **46912**，launcher PID **11044**，端口 **51972**。配置、Session、workspace、Job/scheduler registry、附件等均来自自有 runtime。服务正常退出且端口重新绑定验证成功，`portReleased=true`。

- 真实生产构建在 headless Chromium 打开，初始 hydrate 后只选中 A；B/C 始终未选中，Jobs 页面始终未打开，URL 不变。
- hydrate 后 Session/Job 列表 GET 被拦截为 503；发生过的 Session 列表补读也未能提供状态。后台 history GET 为 **0**。
- 经真实 API 持久化 B 的 agent receipt 与 assistant、C 的 system 消息，再经真实服务 WS 广播；B/C 预览即时更新，延迟重复 receipt 不覆盖 assistant。此部分使用受控事件注入，没有启动 provider。
- agent `/api/session-message-jobs` 创建真实 schedule，legacy PATCH 改目标：黄色 J 即时移动。
- agent `/api/background-jobs` 启动自有 Python 进程：蓝色 J、导航运行数 1 即时出现；`/api/jobs` 改目标后蓝色 J 移动并保持 running 高于 scheduled 的优先级。
- 自有进程真实终止，通过原有 recovery 发终态：运行数消失，目标回到已有 schedule 的黄色 J。以新 transport cursor 重放旧 running 快照不回退；取消 schedule 后 J 消失。
- 浏览器 page errors **0**，provider Worker 数 **0**。
- 原始精简 WS 事件及检查结果：runtime 下 `evidence.json`；PID/隔离路径：`server-identity.json`；截图：`background-previews.png`、`job-running.png`、`terminal.png`；服务日志：`server.log`。

之前两次 harness 尝试分别暴露 fixture 的 Worker 观察端点和独立 registry 布置问题，已修正测试 fixture；最终修复后构建的完整浏览器流程再次通过。

## 未验证项

- 未执行全量 Python suite；本次执行的是相关扩展集合。
- 未调用真实 Codex/provider；消息部分验证的是持久化 history → 真 server summary/WS → 真 store/组件路径。
- 未启动独立 Job 服务部署、未测远程 bridge 故障恢复或移动端/其他浏览器；这些不能由本次 Chromium 和单元结果代替。
- 尚待 MA 验收与快进；未发布到实用服务。
