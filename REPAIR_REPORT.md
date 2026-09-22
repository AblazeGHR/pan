# REPAIR_REPORT — Pan 前端消息一致性修复与 E2E

- 工作树：`D:\project\pan-worktrees\frontend-consistency-repair-ds-20260922`（git root 已核验即本目录）
- 调查基线：`591367a65f88e9d5270e8e99920ef5448d1d68c9`（未自动跟进他人 main 变动）
- 执行方案：`frontend-reaudit-astra-20260921/audit/REPAIR_EXECUTION_PLAN.md`（含 MA 后补的「已到达的正式红测」「协议/持久化补充」纠偏）
- 执行人：本 TA（唯一产品修改负责人）；未创建子 TA；未 merge/push；未访问 8768 / practical / Pan-main canonical / 其他 TA 工作树（只读借用了 t062-12 的 node_modules）

---

## 1. 提交清单

| commit | 内容 |
|---|---|
| `782b16e` | 红测与契约冻结：观察探针 8 项不变量转正式 vitest（+15 个验收矩阵场景）；协议/身份字段表 `audit/FIELD_TABLE.md` |
| `2205bcd` | 核心修复：身份/投影/终态/history（新增 `packages/web/src/stores/messageOrdering.ts`，重写 sessionStore 的归并/分页/epoch/terminal 路径） |
| `d2c754c` | 独立红测复验补修：history TA 22 条红测 14 FAIL → 22 PASS（3 个真实产品缺口） |
| `50d74a5` | 浏览器/协议 TA 复核脚本与证据入树（含 harness 断言作用域修正） |
| （本次） | 协议前端红测转正/钉住更新 + 本报告 |

## 2. 变更映射（方案条目 → 实现）

| 方案要求 | 实现 | 文件 |
|---|---|---|
| §1 身份契约：消息身份至少限定 session+epoch+task+block；无 provider id 不得按正文/前缀作身份 | 删除 `projectionKeys` 的 `role:legacy:{content}` 键；live 行身份 = 显式 provider 身份，否则「当前任务 + 明确 block 槽位」（`taskScopeKey`/`liveProjectionKeys`） | `messageOrdering.ts` |
| §1 字段表 | `audit/FIELD_TABLE.md`：epoch/revision/taskSeq/generation、`_api_history` 合成 `legacy:{sid}:{epoch}:{offset}`、CBC 无 id 形状、Codex item_id 仅挂 tool、durable flush → result(terminalCoverage) → idle | audit/FIELD_TABLE.md |
| §2 单一权威投影 | `currentMessages` 为唯一渲染权威；`session.history` 改为 canonical 投影（镜像）；durable 行按绝对 offset 存于 `SessionTranscript.window`，未落盘行在 `runtime` 区 | sessionStore.ts / messageOrdering.ts |
| §2 缓存索引必须校验身份与结构版本 | `projectionRefs[key]` 记录上次写入的**同一对象**；复用前校验 index 越界 + role + 对象同一；失效后回退「上一 buffer 行对象定位 → 显式身份定位 → 追加」 | `projectLiveRows` |
| §2 结果只更新协议引用的 final block | result 只替换**本任务 live buffer 的最后一个 assistant block**（按槽位），不追加大 history、不重排 | `reconcileWorkerResult` |
| §2 无覆盖证据的已完成任务 overlay 保留到下一轮 | 任务行留在 runtime 区直到 durable 行按序对齐替换（`projectTranscript` 的对齐投影） | sessionStore.ts |
| §3 terminal 是收敛屏障 | 重复/旧 result 幂等（sameCursor / taskKey / 无游标同文）；消费 `terminalCoverage`（此前完全未消费）触发权威 history 恢复 `recoverSessionHistory` | sessionStore.ts |
| §3 DONE 按 task identity 锚定 | DONE 是 runtime 行，保持创建位置；投影不按非 system 计数重插 → 实测 DONE-1 在轮 1 末、question2 前 | `addMessage`/`projectTranscript` |
| §4 history 事务/分页 | 新增按**绝对 offset** 的 loaded window（`mergeWindowPage`）：同 epoch 旧 revision 不覆盖较新重叠内容但允许缺口填充；**更旧 epoch 的页直接拒绝**；epoch 替换（revision 不降）才整体替换并清旧 canonical+runtime；尾页刷新不改最早 offset | messageOrdering.ts |
| §4 summary 不写 transcript | `loadSessions` 恢复路径走同一 `applyHistoryPageToState`，summary-only 快照不再覆盖投影 | sessionStore.ts |
| §6 delta 热路径 | live 更新走 key→index 缓存 + 对象校验；投影仅在结构变更时重算；无全历史文本扫描/全表 sort/hash | `projectLiveRows` |
| §5/§6 其余（queue/Steer 深层协议、虚拟列表测量） | 见 §6「未完成」 | — |

## 3. 红绿测试证据（命令与退出码，cwd=`packages/web` 除注明外）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `node node_modules/vitest/vitest.mjs run`（**基线 591367a**） | 0 | 68 files / **586 tests 全过**（绿基线） |
| `node node_modules/vitest/vitest.mjs run src/stores/sessionStore.consistency.test.ts`（红） | 1 | **18 failed / 5 passed** |
| `AUDIT_DEPS=<t062-12 node_modules> node audit/probe-baseline.cjs`（仓库根） | 0（观察脚本） | **8/9 FAIL**（与 astra 报告逐字一致） |
| `node node_modules/vitest/vitest.mjs run`（修复后） | 0 | **72 files / 631 tests 全过** |
| `node node_modules/typescript/bin/tsc -b` | 0 | 通过（pre-commit 钩子同项校验） |
| `AUDIT_DEPS=<…> node audit/probe-after-repair.cjs`（仓库根） | 0 | **9/9 PASS**（`audit/evidence/probe-after-repair.json`） |
| `node node_modules/vite/bin/vite.js build` | 0 | 产物 `packages/web/dist`（gitignored，未手改） |
| `E:/software/miniforge/python.exe -m pytest tests/ -q` | 1 | 仅 `test_codex_quota_api.py::…permission_boundaries…` 1 失败，**基线既有**（断言的字符串 `account/rateLimits/read` 在产品源中不存在，与本 diff 无关；本 diff 未改任何 Python 产品文件，已用 `git diff 591367a..HEAD -- '*.py'` 核验为空） |
| `E:/software/miniforge/python.exe -m pytest tests/test_reaudit_frontend_reconcile_order.py -q -rA` | 0 | **2 PASSED + 1 XFAIL**（见 §5.3） |

## 4. 真实 Chromium + 隔离 FastAPI + 真 WS 证据

两套独立 E2E，均为**真实浏览器 / 真实 FastAPI 路由 / 真实 `/ws` 广播**；`e2e/server.py` 同源提供构建产物+API+WS，**无 Vite dev server、无 proxy**，故不存在误连 8768 的路径；端口 8796/8797，启动前核验空闲，服务进程为本 TA 自己启动并核对 PID/命令行后停止。

### 4.1 本 TA E2E（`audit/run-e2e-consistency.mjs`，8796，**8/8 断言 PASS，exit 0**）

CBC 原始形状（`thinking`+`tool_use`+`text`，**无 item_id/turn_id/delta**），两整轮，每轮按真实契约顺序注入：stream → durable flush(`__e2e/append-history`) → `worker.result`(带 terminalCoverage) → `worker.status idle`。逐阶段导出三视图（`audit/e2e-runtime/e2e-consistency-evidence.json`）：

- **store 终态顺序**：`user:question one → thinking:analysis one → tool:Bash({"command":"echo one"}) → assistant:final one → system:[DONE] Task completed → user:question two → thinking:analysis two → tool:… → assistant:final two → system:[DONE]` —— 上一轮 analysis/tool 未被吞，final 在 DONE 之前，DONE-1 锚在轮 1 末/轮 2 前。
- **server canonical**：8 行（每块一行），`historyEpoch/historyRevision` 随 durable 前进。
- **DOM**：与 store 同序（工具行折叠为「1 tools」，已渲染正文相对顺序一致）。
- **reload 后**：重新选中会话，store 恢复为与服务端 canonical 完全一致的 8 行 + 正确顺序（冷加载路径复验通过）。

### 4.2 浏览器 TA strict E2E（`packages/web/e2e/audit-cbc-strict.mjs`，8797，**8/8 PASS，exit 0**）

基线为 7 FAIL（exit 2）；修复后 `S:alpha/bravo/charlie:turn-sequence…` 3 条、`D:held-stale-snapshot-then-new-CBC-turn`、`P:no-history-row-replaced-by-live-delta` 全过。证据：`evidence/cbc-strict/{cbc-strict.json,final.png,trace.zip}`。

其中 2 条 `DOM-paints…` 在首轮复跑仍 FAIL，**根因是 harness 而非产品**：每轮都渲染相同的「N tools」组标签，脚本用全局 `findIndex` 定位工具组，轮次 ≥2 会命中前一轮的组（其自身记录的 `domTail` 顺序实际正确）。已把 DOM 搜索改为以该轮自己的 user 行为锚做**轮内**搜索后 8/8 通过；该修正只缩小断言的搜索范围，未放宽任何产品顺序契约（store 侧 turn-sequence 断言本来就全过）。

## 5. 独立红测复验（按派发顺序处理的三份 TA 交付）

### 5.1 history TA（`reaudit.history/queue/orderedEvents`，22 条）——14 FAIL → **22 PASS**

复制到本树复验，暴露 3 个真实产品缺口并补修（`d2c754c`）：
1. **R2**：分页重建只在「页带来新 offset」时执行，重复投递同一窗口的页不刷新显示。改为每次接受页都重建，并把仅存在于渲染投影的行**采纳**进 runtime 区，使重建不丢行。
2. **transcript 过期守卫**：Session canonical 投影缩水（history 被替换）或 epoch 变化时旧 transcript 必须重建。
3. **R3**：epoch 门禁补强——epoch 不同的页仅在 revision 不低于已应用值时才可整体替换；更旧 epoch 页直接拒绝。

测试隔离：TA 红测早于新增的 `sessionTranscripts` 状态字段，其 `beforeEach` 未含该字段导致跨用例串状态；仅在其重置块补该字段（**未改任何断言**），另为 copied queue 测试补 2 处 `Record<string, unknown>` 类型断言（该文件在 `tsc` 下本就不编译）。Q1–Q6 六条队列正控在基线与本树均全过。

### 5.2 浏览器 TA —— 见 §4.2（7 FAIL → 8/8 PASS）

### 5.3 协议 TA（`test_reaudit_frontend_reconcile_order.py`）—— 1 条 strict-xfail 转真，1 条如实保留

探针适配（只改 harness，不改产品契约）：打包真实 `messageOrdering`；live buffer 改由 `applyLiveStream` 产出（与真实管线一致，原探针把 display/buffer 手工种成互不相干的克隆——该状态管线不可能产生）；逐 case 清空 transcript。

- `test_partial_history_does_not_reorder_the_turn`：**strict-xfail → PASSED**。修复后 result 不再先入 history，provider 顺序 `[user, analysis, tool, final]` 保持。
- `test_documents_the_current_reorder_and_duplication`（作者注明「fix 后必须更新」）→ `test_pins_the_repaired_reorder_result`：钉住修复后输出（顺序正确 + 无重复 final/analysis）。
- `test_idless_turn_is_not_duplicated`：**仍 XFAIL（strict）**，原因已改写为实情：重排已消除，但该 case 在「canonical 窗口已覆盖该轮 + live 重放」时仍会再次追加——`applyLiveStream` 没有「该 live 行已是 offset X 的 durable 行」的识别路径（无 id provider 无法按身份匹配）。需按 MA 要求先定 provider result 语义、再由 terminalCoverage/权威恢复路径收敛，属于**待办而非已修复**（见 §6）。

## 6. 未完成 / 未验证 / 风险（不标完成）

1. **canonical 已覆盖轮次的 live 重放重复**（§5.3 XFAIL）：唯一未关闭的顺序/重复类缺陷，触发条件是重放/重连场景；主方案红测与两套 E2E 均未覆盖该场景。建议：在 terminalCoverage 语义确定后，让权威恢复路径以「已 durable 的 offset 区间」收敛 runtime 区。
2. **queue/Steer（方案 §5）只做到回归级**：Q1–Q6 全过 + 既有 Steer/草稿事务测试全过，但方案要求的 200+{error}、timeout outcome-unknown、Steer receipt 重试协议、partial hint + equal-rev full GET 等**未新增红测**（MA 也已指出 Q6/Steer 现有证明强度不足）。未改动 queueStore 逻辑。
3. **虚拟列表/性能（方案 §6、矩阵 6）未做**：未新增 5000 消息长历史 E2E、未采浏览器 frame/long-task/内存样本；仅保留既有 `sessionStore.stage3.performance.test.ts`（reducer 级 H/L 矩阵）并通过。anchor 以 message ID+offset 定位等要求未实现/未验收。
4. **WS 断线重连/gap/cursor expired（矩阵 3）未做 E2E**：单连接 snapshot 顺序已由两套 E2E 覆盖；旧 socket/延迟 HTTP/断线恢复未验证。
5. **未用真实 provider**：全部使用真实事件**形状**（CBC 无 id、Codex item_id 仅挂 tool）+ harness 注入；MA 已说明当前无真实模型验收，故未声称 provider 级验收。
6. **E2E inspection seam 留在产品代码**（`sessionStore.ts` 末尾 7 行）：仅当 URL 显式带 `?panE2E=1` 时把 store 挂到 `window.__panSessionStore`。保留理由：store 是模块内单例，minified bundle 外部无法取得引用，外部注入/改写 bundle 都拿不到闭包内标识符；该 seam 对正常 URL 完全惰性。如 MA 要求零产品痕迹，替代方案是放弃 store 级导出、只以 DOM+React fiber 读取（browser TA 的做法）。
7. **anchor 精度依赖**：无 id provider 的块→durable offset 对齐依赖「append-only、按序落盘」契约与建组时的 `historyTotal`；多客户端并发写同一 session 未测。

## 7. 保留的既有正确修复

稳定 render key（`getDisplayItemKey`）、tool-group 折叠、`flow-root`、filtered-empty 加载入口、乐观用户行/投递对账、`preserveNewerSummary`、queue 版本门禁与 Steer 草稿事务、Codex 工具投影规范化、持久化屏障与游标——均未回退，相关既有测试全绿。

## 8. 边界遵守

- 全程未访问 8768（隔离 E2E 用 8796/8797，`assertHarnessSafety` 亦拒绝 8768）；未操作 practical / Pan-main canonical / 其他 TA 工作树；只读借用 t062-12 的 `node_modules`（junction 指向，未改其内容，未安装依赖）。
- 未 merge / 未 push / 未改共享依赖；仅本工作树本地提交。
- 停止的进程均为本 TA 启动（8796/8797 的 `packages/web/e2e/server.py`，停止前核对 PID 与命令行）。
- 证据文件保留于 git：`audit/FIELD_TABLE.md`、`audit/evidence/{current-main-probes,probe-after-repair}.json`、`audit/e2e-runtime/e2e-consistency-evidence.json`、`evidence/cbc-strict/*`、`audit/ta-evidence/**`（含三份 TA 报告与原始 evidence 日志）；一次性运行时数据已 gitignore。
