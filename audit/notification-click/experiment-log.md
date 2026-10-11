# 通知点击深链实验日志（research/notification-click-session-20261011）

- 日期：2026-10-11（UTC 时间戳见各产物）
- worktree：`D:/project/pan-worktrees/notification-click-session-20261011`
- base：`b80b272c`；分支：`research/notification-click-session-20261011`
- 约束：未合并 main/practical、未 push、未部署、未重启真实服务、未改用户配置、未发真实 QQ 通知、未弹任何系统通知（原生路线仅模拟）

## 环境准备

1. 依赖哈希比对（worktree vs `D:/project/Pan`(practical) vs `D:/project/Pan-main`(main)）：
   - `packages/web/package.json` sha256 三方一致 `c7199021…`
   - `packages/web/pnpm-lock.yaml` sha256 三方一致 `249d2fc3…`
   - `packages/web/vite.config.ts` sha256 三方一致 `b819756b…`
   - 记录：`dependency-hashes.txt`
2. `tools/prepare_validation_env.ps1`（脚本自带安全模型：fail-closed / 幂等 / 不递归删除 / 不装依赖）：
   - 首次指向默认 canonical（Pan-main）发现其 node_modules 陈旧：缺 `@xterm/*`（tsc 报 TS2307，vitest 不受影响）。
   - `-Undo` 后重跑并显式 `-CanonicalNodeModules "D:\project\Pan\packages\web\node_modules"`（practical 的完整依赖，含 @xterm；与 worktree lock 哈希一致）。**只读复用，未执行任何 install**。
   - 日志：`validation-env-output.txt`（`*.log` 被仓库 .gitignore 忽略，故以 .txt 留存）；脚本 canonical Python 模块检查全 OK（Pan-main venv）。
3. 未提交本地标记 `packages/web/.pan-validation-node-modules.junction`（脚本 ownership marker，留在磁盘、不入 git）。

## 构建与静态检查

- `./node_modules/.bin/tsc -b` → 干净通过（切换到 Pan 依赖后）。
- `./node_modules/.bin/vite build` + `node e2e/verify-precompressed-assets.mjs` → `BUILD_OK`，66 个压缩产物校验通过。

## 测试

| 轮次 | 命令 | 结果 |
|---|---|---|
| 定向单测 | `vitest run src/utils/session-deeplink.test.tsx src/hooks/useWebSocket.test.tsx` | 95/95 通过 |
| 全量（改动后） | `vitest run` | 134 文件通过 / 2 文件失败；用例 1442 过 / 2 失（日志 `vitest-full-output.txt`，VITEST_EXIT=1） |
| 全量（基线 b80b272c 临时 worktree，同环境同依赖） | `vitest run` | 133 文件通过 / 2 文件失败；用例 1436 过 / 2 失（日志 `vitest-baseline-full-output.txt`） |

失败对照：两轮失败的**同名同因**——`App.test.tsx`（responsive WorkspaceRail / deferred detail panel 用例，全量并行下的 flake；单独运行 3/3 通过）与 `components/editor/EditorPane.test.tsx`（Monaco 预览 heading，环境相关）。基线复现 → **预存在，与本次改动无关**。改动后多出的 6 个用例（4 hook + 2 websocket 点击）全部通过。

## 隔离浏览器 E2E（真实 Chromium）

- 脚本：`packages/web/e2e/notification-deeplink.mock.mjs`
- 方式：`vite preview`（**生产构建**、真实 basename `/react/`）+ 动态端口（本轮 `127.0.0.1:61750`）+ `?mock=1`（patch fetch、跳过 WS，不触真实后端）+ headless Chromium `153.0.8010.12`
- 结果（`e2e-notification-deeplink.json`）：6/6 通过
  1. 对照：无参数不自动选中任何会话
  2. `?session=mock-alpha` 命中并选中、参数被消费、无降级 toast、历史消息渲染
  3. 未知会话 → toast 降级、不选中、参数被消费
  4. 非法引用 → "会话链接无效" toast、不选中
  5. 双 tab 各自解析各自 `?session=`，互不串台
  6. 全程 0 pageerror、0 真实后端（8768/8767）请求

## Windows 原生路线（安全模拟，无 UI）

- 原型：`notifyicon-click-prototype.ps1`（`-Mode simulate` 不建 NotifyIcon、不弹 balloon、不开浏览器；`-Mode live` 保留供评审，本次未执行）
- 运行：
  - loopback origin：`SIMULATE_OK url=http://127.0.0.1:8768/react/?session=ses_0123456789abcdef`（`native-simulate-result.txt`）
  - tunnel origin：`SIMULATE_OK url=https://pan.example.ts.net/react/?session=ses_0123456789abcdef`（`native-simulate-result-tunnel.txt`）
  - 非法 sessionId（`bad id!`）→ 拒绝，exit 1（allowlist 生效）
- 明确声明：这不是真机点击验证。balloon/toast 的实际点击行为、焦点、系统通知中心留存均未真机验证（按约束不打扰用户），仅有 MS Learn 源码级依据 + 本源码证明现状无回调链路。

## 产物清单

- `dependency-hashes.txt`、`validation-env-output.txt`、`vitest-full-output.txt`、`vitest-baseline-full-output.txt`、`e2e-notification-deeplink.json`、`native-simulate-result*.txt`
- 原型源码变更见研究分支提交；`notifyicon-click-prototype.ps1`

## 阶段一正式实现（2026-10-11，追加提交，基线 ca81ed58）

范围：仅"Pan 页面存活（含后台标签）"的 Web Notification 点击。系统 NotifyIcon/SW/Push/浏览器关闭后通知不在本阶段，行为未改。

### 实现（按 MA 收紧扣分）

- 权威判定：`lookupSessionRef` 仅当"我们发起、未被 supersede、同 epoch、已完成"的列表请求不含该 id 才判 missing；加载失败=unknown（去删化，不误判删除）。
- 守卫：点击单调 intent 计数 + 起始 selection/位置快照；异步完成后校验，晚到者不覆盖用户后续操作；路由先于选择，navigate 完成且选择已应用才返回 true；router 异常 toast 上报不吞。
- `?session`：冷启动仅在权威确认为 missing 时清参；加载失败保留参数并在后续成功刷新时恢复；仅删除 session 参数，其余 query（含既有 `panE2E`）保留。
- 安全：正文不进 URL；E2E 断言 0 外部请求（应用既有字体 CDN 在 E2E 中封禁，计数 5）。

### 过程中发现并修复

- 冷启动 supersede 竞态（mock E2E 首先捕获）：hook 的权威加载与 Layout 初始 `loadSessions` 竞速被 supersede 后落入 unknown 且不再重试。修复：`settleLoads` 等待 + 有界两轮重试；新增单测 `recovers when a concurrent refresh supersedes its own load`。
- 观察到（非本阶段修复，范围外）：删除 worker 存活/刚完成的 session 时存在竞态——DELETE 先删存储、worker 清理异步，worker 侧迟到保存可把 session 写回（服务端列表复活；E2E 诊断转储 server 列表为证）。E2E 以 kill→delete 循环建立确定前置。建议独立任务核查该后端删除竞态。

### 阶段一验证（全部在隔离环境）

| 验证 | 结果 | 证据 |
|---|---|---|
| 定向单测（deeplink 6 + useWebSocket 95） | 101/101 | 本次运行 |
| 全量 vitest（phase1 首跑） | 1447 过/3 失（多出一次 SessionList.drag.realBackend flake） | `vitest-phase1-full.txt` |
| 全量 vitest（phase1b 复跑） | 1448 过/2 失，与基线同两名（App.test flake + EditorPane Monaco） | `vitest-phase1b-full.txt`（基线 `vitest-baseline-full-output.txt`） |
| 隔离真实后端 E2E（真实 React 生产包 + 真实 FastAPI/Worker/WS + 假 CLI；动态端口） | 10/10 | `e2e-notification-deeplink-real.json` |
| mock/URL E2E | 6/6 | `e2e-notification-deeplink.json` |
| 后端通知/提醒回归 `tests/test_notifications_reminders.py` | 12 passed | 终端输出（Pan-main venv） |
| tsc / scoped eslint（7 文件）/ vite build + 66 资产校验 / git diff --check | 全过 | 本次运行 |

E2E 用例：真实 worker.result→通知创建（正文=结果）；onclick→回聊天+选择（其它视图出发）；browser=off 静默无通知；删除前置+点击删除 toast；加载失败保留深链+恢复（其余 query 保留）；慢加载不覆盖用户抢占；连击只生效一次；双 tab 隔离；后台标签 handler；0 外链/0 pageerror/无正文入 URL。
未验证（如实）：真实 OS 通知点击与焦点（浏览器/OS 边界，stub 捕获+程序化触发 onclick，未弹真实系统通知）；浏览器关闭后的 SW 冷启动（范围外）；headless 下后台标签 visibilityState 记录为 `visible`。

## MA 复核阻塞修复（2026-10-11，e771bef2 之后窄修，保留历史）

MA 复核 e771bef2 指出两个阻塞：

1. `openSessionFromNotification` 在 lookup 之后检查 intent/currentSession/location，但 `await navigateToChat`（lazy import + router.navigate）期间**完全未复查**：用户在该窗口选 B、或第二个通知点击 B，旧 A 返回后无条件 `selectSession(A)`；且仅比 `currentSessionId` 无法发现 A→B→A。
2. `useSessionDeepLinkParam` 的异步 lookup 无 effect 生命周期/参数代际守卫：raw 被删时 effect 早退、`retry.current.raw` 不更新，旧 lookup 仍可 select/clear 新 URL；unmount 后仍可迟到生效。

修复（`packages/web/src/utils/session-deeplink.ts`）：

- 选择代际：`selectionEpoch()` = `_selectionSeq` 全值和（`selectSession` 是唯一写入者，任何用户选择都使其变化，含 A→B→A）。
- 点击路径三段复查：lookup 后（intent/epoch/location）、lazy import 后导航前（intent/epoch/location）、navigate await 后（intent/epoch + `isAtChatRoot()`——自身导航落点为聊天根，其它位置=用户改道，用户胜出）；router 失败仍 toast 上报。
- hook：`generation` ref（参数值变化含 →null 或 unmount 时递增）使旧 continuation 失效；`baselineEpoch` 记录链接出现时的选择代际，用户在其后做过任何选择即静默消费链接、不覆盖；unmount 经独立 effect 失效一切待决结果。

红→绿证据（新增 7 个确定性回归，修复前失败/后通过；断言未削弱）：

| 阶段 | 结果 | 证据 |
|---|---|---|
| 修复前（对 e771bef2 源码） | 108 中恰 7 失败（4 点击窗口 + 3 hook 生命周期），旧 101 全过 | `blocker-red-before-fix.txt` |
| 修复后 | 108/108 通过 | `blocker-green-after-fix.txt` |

新增回归：deferred lazy import（首测门控 `@/router` 模块解析）+ deferred navigate 下——用户在 import 窗口选择胜出且**不再发起导航**；第二个通知点击胜出；A→B→A 仍判为用户接管（以 `_selectionSeq` 增量证明迟到流程未二次选择）；用户在 navigate 窗口改道 `/jobs` 时中止。hook：raw 移除后旧 lookup 不作用于新 URL；lookup 中用户选择获胜且链接被静默消费；unmount 后迟到结果不生效。

复验（修复后，全部隔离）：定向 108/108；全量 1455 过/2 失（与基线同两名，`vitest-blockerfix-full.txt`）；tsc、scoped eslint（7 文件）、`vite build`+66 资产、`git diff --check` 全过；mock E2E 6/6；隔离真实后端 E2E 10/10（`e2e-notification-deeplink-real.json`）。修复补丁：`blocker-fix.patch`。
