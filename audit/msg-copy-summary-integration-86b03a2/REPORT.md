# 86b03a2 + 消息复制／Session 摘要组合候选

本批次单独待 MA 验收，不替代或改变 `86b03a2a83798239a1deb3a8beabc87ce3935ddb` 的 CI 冻结门禁。

## 拓扑与源码

- 新注册工作树：`D:/project/pan-worktrees/msg-copy-session-summary-86b03a2-20261024`。
- 分支：`integration/msg-copy-session-summary-86b03a2-20261024`。
- 起点精确为 `86b03a2a83798239a1deb3a8beabc87ce3935ddb`，创建后核对 root/branch/HEAD/status clean，读取开发、TA 验收与测试隔离约定。
- 受测源码提交：`84a6d13defa72f27731d0e219375bad2fc672f61`。采用非快进 merge，双亲依次是 `86b03a2a83798239a1deb3a8beabc87ce3935ddb`、`bc37fd2a2416510a0b8248b3d6317625d38ff8df`，保留原候选历史，无冲突。
- 原树 `D:/project/pan-worktrees/msg-copy-session-summary-20261024` 仍 clean、HEAD 仍为 `bc37fd2a…`，原证据未覆盖。
- 最后的证据提交仅增加本审计目录；功能源码仍为上述精确受测 SHA。旧 `audit/msg-copy-session-summary-20261024` 是继承的原批次报告，不代表本轮组合证据。

## 兼容核对

`81415706… → 86b03a2a…` 没有修改 `session.py`、`history_read.py`、`web/server.py` 或 React 源码。因此 merge 后这些产品文件逐字保持 `bc37fd2a`，不存在同文件冲突或改写摘要协议的兼容补丁。

组合风险实测范围：

1. `history_search_index.py` 新增冷 schema 初始化锁；现有单消息原文接口通过 `_session_read_projection` → `_history_search_scan_source` → `history_read.project` 读 canonical 历史，不创建 FTS schema。定点运行原文读取的角色/source/身份/权限边界和新冷初始化并发回归，验证仍兼容。没有用展示投影或 FTS snippet 替换原文。
2. `host_server.py` 新增 Proactor peer-reset 清理。原浏览器 fixture 只继承 `uvicorn.Server`，不能据此声称覆盖此宿主组合。本次仅调整 `session-summary.server.py` 的测试宿主为当前生产 `PanServer`，把 logging 文件固定到本次临时 runtime 下；产品实现未加兼容变更。实际浏览器运行当前宿主并观察诊断 monitor 正常 stop、进程退出和端口释放。另运行 `test_host_peer_reset.py` 的 exact callback/owner/protocol 回归；普通浏览器流程不等于实际触发了 TCP reset 故障。
3. WS/store 摘要权威性保留原修复：assistant 先到后的迟到本地 Steer 回显不写 versioned 卡片，保留 transcript；同 revision、新旧 HTTP/WS、后台 Session、空正文、thinking/tool、user fallback 和既有角色/source 语义通过定点回归。未另造摘要系统。

核对命令的输出均为空：

```powershell
git diff --name-only bc37fd2a HEAD -- packages/core/session.py packages/core/history_read.py packages/web/server.py packages/web/src
git diff --name-only 86b03a2a HEAD -- packages/core/history_search_index.py packages/core/host_server.py packages/automation
```

## 命令与结果

所有命令 cwd 均为本新工作树。前端依赖使用 `CI=true; pnpm --dir packages/web install --offline --ignore-scripts --frozen-lockfile` 独立安装，exit 0，未改 lockfile 或共享 node_modules。

| 命令 | 冻结源码结果 |
| --- | --- |
| `pnpm --dir packages/web exec vitest run <下列13文件> --reporter=json --outputFile=../test-results/integration-frozen-vitest.json` | 287 passed，0 failed，0 skipped；exit 0；[文件级结果](vitest.json) |
| `python scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_session_summary_projection.py tests/test_history_read.py tests/test_replay_resync.py tests/test_history_search_content.py tests/test_host_peer_reset.py -q -o addopts= --junitxml=packages/web/test-results/integration-frozen-pytest.xml` | 91 passed，0 skipped；exit 0；[JUnit](pytest.xml)、[输出](pytest.txt)，3 个依赖 warning |
| `pnpm --dir packages/web build` | exit 0；包含 `tsc -b`；94 个压缩资源通过；[输出](build.txt) |
| `pnpm --dir packages/web exec eslint <改动的10个TS/TSX文件>` | exit 0；0 error、3 个既有 Fast Refresh warning；[输出](eslint.txt) |
| `$env:PAN_E2E_PYTHON='D:/project/Pan-main/.venv/Scripts/python.exe'; node packages/web/e2e/session-summary.e2e.mjs` | 冻结 `84a6d13d…` 后 exit 0，真实 HTTP/WS + 生产 Chromium + fake CLI，7 项通过；[事件证据](browser.json) |
| merge commit pre-commit `npx tsc -b --noEmit` | exit 0，React frontend OK |
| `git diff --check` | exit 0 |

前端 13 文件（相对 `packages/web/src/`）：`hooks/useWebSocket.test.tsx`、`hooks/liveIndicators.test.tsx`、`stores/sessionStore.snapshot.test.ts`、`stores/sessionStore.refresh.test.ts`、`stores/sessionStore.interleaving.test.ts`、`stores/sessionStore.terminalTransient.test.ts`、`stores/sessionStore.t055.test.ts`、`components/chat/MessageBubble.bookmark.test.tsx`、`components/chat/MessageBubble.manualReport.test.tsx`、`components/chat/MessageBubble.rewind.test.tsx`、`components/session/SessionItem.test.tsx`、`utils/clipboard.test.ts`、`services/api.messageText.test.ts`。ESLint 为这些目录中的 10 个本功能改动 TS/TSX 文件，完整 warning 输出已保留。

## 实际组合浏览器证据

- 源码 SHA 记录于 `browser.json.sourceSha`，为 `84a6d13defa72f27731d0e219375bad2fc672f61`。
- 生产资源 `index-Cy7RZf9J-pc2.js`，Chromium `153.0.8010.12`，临时私有 context/profile。
- runtime：`packages/web/test-results/summary-1792783038416`，随机端口 `56226`。launcher PID `11592`，实际宿主 PID `47892`，均已退出，`portReleased=true`；[宿主身份](server-identity.json)。本地 fake Worker 的生命周期由 fixture shutdown 清理。
- 真实 WS/HTTP 乱序、旧 history response、后台卡片实时 assistant 更新、切换和冷加载、paused Send、handoff/stream/result/reconnect、fake Force restart 均通过。
- 实际点击复制菜单并读取 Chromium clipboard：展示链接已投影，复制仍为 canonical Markdown，含代码、长正文尾部和尾空格。clipboard 长度 2591，Windows CRLF 仅在断言中归一化换行，无截断或去尾空格。
- 空 assistant + thinking/tool 后卡片仍保持有效 assistant；页面错误 0。
- 截图及宿主诊断日志保留于该 runtime 的 `copy-menu.png`、`final.png`、`logs/runtime-diagnostics.jsonl`；诊断最后一条为本次 PID 的 `runtime.stop`。

## 范围与待验收边界

本轮未改产品源码相对于原候选的实现；只增加了生产宿主测试适配与本轮审计证据。没有访问活动 CI 工作树或操作 CI Job，没有运行全仓或全量 CI，没有 push/合并 main/practical/操作真实服务和数据。收尾核对 main 仍为 `86b03a2a…`，practical 仍为 `2652ac8e6d0ff312a255666bc5e459890346e6e5`。

原红绿复现和未变产品边界可复用原报告。本轮没有重新制造全部红灯；实际重新运行了上述定向回归和组合浏览器。未测真实 provider/账号/native TUI/native Steer、移动端、其他浏览器、实际 peer-reset 网络故障注入及既有坏摘要数据批量迁移。TA clean 候选不等于 MA 验收通过，也不改变独立 CI 冻结门禁。
