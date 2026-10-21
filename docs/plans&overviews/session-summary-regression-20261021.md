# Session 列表最新消息摘要回退修复

## 候选与范围

- 工作树：`D:/project/pan-worktrees/session-summary-regression-20261021`
- 分支：`fix/session-summary-regression-20261021`；基线：`18261dabce8981c4854c87c7898756d4e81a7c7b`。
- `bb843966`、`eea3a1b8` 均通过 `git merge-base --is-ancestor <commit> HEAD`，不是待合并修复。
- 本次 summary 专指 Session 列表卡片最新消息预览。模型压缩摘要不在范围内。
- 已读 CODEBUDDY、constraints-and-acceptance、TESTING、统一队列计划及现行 `docs/design/queue-at-most-once.md`，并沿旧报告继续排查。用户提供的旧报告在本树实际位置为 `docs/plans&overviews/live-session-job-indicators-20261018.md`。
- 状态：修复和定向验证完成，提交为 clean 候选，等待 MA 独立验收与快进。未移动 main/practical，未 push/release，未触碰用户 Session/历史、既有服务或 8767/8768。

## 根因：实测与代码结论

1. **旧 WS patch 污染新 HTTP 投影（真实生产浏览器修前失败）**：`loadSessions` 在比较 revision 前，将 `_sessionEventPatches` 中的旧完整 Session patch 套到新列表对象上，使较新的 `summaryRevision` 和 preview 一并退回旧值。新 HTTP 明明带有 assistant answer，卡片仍停在 MA 输入。现在先还原完整的服务端摘要字段，再与当前摘要比较，窗口游标独立保留；合法性等同属摘要的字段也不再被旧 patch 拼接覆盖。
2. **loaded window 与卡片摘要混用（定向修前失败）**：history hydration 从显示尾行（可能是部分旧窗口或本地 MA 后缀）重写 `lastMessage`；列表合并又长期保留本地 user projection 的预览。现在已有权威 `lastDisplayPreview` 时不由 loaded window 重算卡片；仍保留 legacy 无摘要场景的 fallback。新增客户端 `summaryHistoryRevision`，独立于 loaded window 的 `historyRevision`，避免相同 revision 的不同窗口被当成完整摘要。
3. **delta 与持久化投影版本不同（定向修前失败）**：delta 修改预览而不增加 durable summary revision。同 revision 的旧投影、或仅 Worker 元数据变化而增加 summary revision 的投影，会把 streaming 覆盖回输入。现在相同正文 history 游标的快照保留已看到的预览，真正更新的正文 history 仍可推进；未加轮询。
4. **交接 receipt 晚于 delta（真实 fake CLI 首版修复仍失败，追加修前用例）**：stdin write/drain 与持久化 await 允许 stdout 先送出 delta，之后的输入 receipt 带更高 history revision。复用既有 `workerId / generation / executionSeq` 的执行身份，只保护同一次 execution、覆盖其输入前缀的 receipt，保留其 canonical cursor 与 `lastUserPreview`，卡片保留后到达逻辑顺序上的响应。`summaryBaseTotal` 记录首个 stream 时看到的摘要前缀，不能用可能尚未加载的 transcript anchor 代替。不同 execution 或覆盖后续 history 的摘要不受此保护。
5. **乐观排队伪装为最新 context（定向修前失败）**：排队/编辑路径直接写 `lastMessage` 并增加 `historyTotal`。版本化 Session 现在保留权威卡片预览和计数，正文仍按原有 pending identity 留在本地消息投影中等待交接；新列表/窗口也不再用乐观行数污染 canonical count。

没有按 MA source 隐藏消息。agent/report/automation/system_prompt/user 来源保留；真实交接后的输入仍可成为卡片预览。报告、system 等来源继续沿用后端既有投影约定。没有改变队列交接、幂等 ID、provider/Steer 安全语义。

后端检查 `append_history`、summary projection 更新/落盘、冷 summary、detail、完成块 WS publication：本次受控案例中均正确推进，无需改动后端产品源码。真实 Send 暂停入队时 history/count/preview 不变；write/drain 交接后输入才进入 history。

## 修前失败证据

- [初始定向用例](../../evidence/session-summary-regression-20261021/unit-before.txt)：3 failed / 3 passed，分别为旧 patch、新摘要与窗口混用、同 revision streaming。
- [晚交接用例](../../evidence/session-summary-regression-20261021/late-handoff-before.txt)：1 failed / 7 passed，receipt 将 `partial` 覆盖成 `MA input`。
- [乐观排队用例](../../evidence/session-summary-regression-20261021/optimistic-before.txt)：1 failed / 14 passed。
- [基线浏览器失败](../../evidence/session-summary-regression-20261021/browser-before.json)、[断言输出](../../evidence/session-summary-regression-20261021/browser-before.txt)：生产 baseline bundle `index-wv-krtco-pc2.js`，`bundleHasFix=false`；真实 HTTP 包含新 answer，但卡片持续 `MA send old input`，5 秒断言失败。PID 14248，端口 52766，`portReleased=true`。
- 早期浏览器流程未触发缺陷；增加明确的“旧 WS patch 已摄入、HTTP 在新正文持久化后读取”屏障才稳定复现。没有把那些通过流程当作修前失败证据。

## 最终验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 定向 Vitest：15 个相关 store/hook/component 文件 | **314 passed，0 skipped，exit 0** | [输出](../../evidence/session-summary-regression-20261021/vitest-final.txt)、[JUnit](../../evidence/session-summary-regression-20261021/vitest-final.xml) |
| Python：summary projection / history read / replay resync | **61 passed，0 skipped，exit 0**；3 条依赖警告 | [输出](../../evidence/session-summary-regression-20261021/python-final.txt)、[JUnit](../../evidence/session-summary-regression-20261021/python-final.xml) |
| 改动 TS/TSX 类型及 Node harness ESLint | exit 0 | [输出](../../evidence/session-summary-regression-20261021/lint.txt) |
| `pnpm build`：`tsc -b` + Vite + 预压缩资源校验 | exit 0；74 assets；既有 chunk size 提示 | [输出](../../evidence/session-summary-regression-20261021/build.txt) |
| Python compileall / `git diff --check` | exit 0 | 新 fixture、fake CLI、Python 回归；提交前再次检查 |
| 真实隔离 HTTP/WS + 生产 Chromium | exit 0、page errors 0、端口已释放 | [结构化证据](../../evidence/session-summary-regression-20261021/browser-final.json)、[输出](../../evidence/session-summary-regression-20261021/browser-final.txt)、[截图](../../evidence/session-summary-regression-20261021/final.png) |

Vitest 覆盖相关 summary/queueDelivery/refresh/stage3/race/interleaving/consistency/live/execution/terminalTransient/rewind/stateProjection、useWebSocket/liveIndicators 与 SessionItem。新增用例还覆盖较新正文在旧窗口之后到达、metadata revision 与正文 revision 分离、相同执行的 5 种来源，以及其他 execution / 后续 history 的负控。

Python 使用隔离入口：

```powershell
python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_session_summary_projection.py tests/test_history_read.py tests/test_replay_resync.py -q -o addopts= --junitxml=artifacts/summary-python-final.xml
```

### 真实浏览器流程及资源

最终 runtime：`packages/web/test-results/summary-1792587104559`，实际服务 PID **37640**，隔离端口 **58279**，生产 bundle `index-CnYuy3g3-pc2.js`。配置、Session、工作目录、附件/草稿/registry 均为 harness 自有临时根。[服务身份](../../evidence/session-summary-regression-20261021/server-identity.json) 与结构化证据已入库；原始 server.log 留在 runtime。

1. A 选中，B 后台：旧 MA WS patch 与较新真实 HTTP 列表乱序，新 answer 获胜。
2. 真实旧 history HTTP 回包在新 assistant WS 后放行，卡片不回退；切 Session 与冷刷新保持 durable answer。
3. 真实 `/api/send`，`source=agent`，B 暂停：queued body 不改变 canonical history/count/preview；启动本地 fake CLI 并解除暂停后，交接、delta、完成块与 terminal 更新卡片。
4. 相同正文的旧摘要带新 WS cursor、断线重连及 resync/HTTP 读取均不盖掉 streaming。
5. 再次 Send，delta 先于 receipt 的窗口仍显示响应；运行中 Force 经本地 fake 的 capability-absent restart 路径交接，替代 execution 的流式、终态、切 Session 与冷刷新正确。
6. DOM MutationObserver 记录的预览轨迹：`Current assistant answer → probe-one → probe-one streaming → probe-one answer → probe-interrupt streaming → probe-force → probe-force streaming → probe-force answer`。每轮出现 streaming 后未回到该轮输入；后续新一轮输入是合法的新消息。
7. browser/page errors 0；正常关闭自有 Workers 和服务，端口重新绑定成功；最终查询没有本 harness 的 Python 服务/fake 进程残留。

复现：先 `pnpm --dir packages/web build`，再执行：

```powershell
$env:PAN_E2E_PYTHON='D:/project/Pan-main/.venv/Scripts/python.exe'
node packages/web/e2e/session-summary.e2e.mjs
```

fake CLI 仅本地 stdin/stdout，不调用收费模型。Force 验证的是 capability-absent 的重启路径；未声称验证真实 native Steer receipt 或真实 provider 行为。

## 已有失败与未验证边界

- 扩展来源集合出现 **95 passed / 1 failed**：[JUnit](../../evidence/session-summary-regression-20261021/source-expanded.xml)。失败 `tests/test_source_metadata.py::test_send_session_accepts_future_external_sources`，期望 `meta-agent`，实际归一化为 `agent`；[独立复跑](../../evidence/session-summary-regression-20261021/existing-source-failure.txt) 同样失败。相关 backend 产品与该测试都未改，工作区 blob 与基线相同：worker `d992589560d34e6cd6163dae04d78a8b3c5d24f0`，test_source_metadata `195a464f793801ddab25d103f16fc16d8687994c`。没有改断言、skip 或扩大修复范围掩盖此项。
- 未跑全量前后端测试，未访问/修改用户实际数据和已有服务，未验证真实 CLI/provider、native TUI 的真实模型输出、移动端或其他浏览器。
- Typecheck/build、隔离 HTTP/WS、真实 Chromium、MA 独立验收和实用服务发布是不同层次。本候选等待 MA 验收与快进，不代表用户运行中的服务已更新。

## MA 独立验收（2026-10-21）：已验收

整合当前 main 7e30d649，无冲突。独立审阅 sessionStore 摘要合并、加载窗口、乐观队列及同 execution 晚交接保护；后端 history replace 保持 revision 递增。
独立复验上述 15 文件 Vitest：314 passed；summary/history/replay pytest：61 passed，均 exit 0，无 skip。
整合树 pnpm build exit 0，74 个压缩资源通过。独立运行生产 Chromium HTTP/WS harness exit 0，运行目录 packages/web/test-results/summary-1792587628166，隔离端口 51596；旧 MA patch、新 HTTP、旧 history 回包、Send/Force、重连、切换和冷刷新全部通过。未使用真实模型，未访问既有服务。
TA 记录的 meta-agent 来源兼容失败保留为既有独立问题，本次未改变该归一化契约。
验收仅覆盖本报告列明范围；用户级 skill 按当前指令未同步，现有服务未重启。
