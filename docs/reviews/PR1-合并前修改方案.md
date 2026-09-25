# PR #1 合并前修改方案与执行记录（scheduler 除外）

- **落地分支**：`integrate/pr1`（= main + PR #1，merge commit `1eb102d`）；方案最初基于 `pr1/pan-features` @ `9e839d0` 编制
- **编制与执行方式**：三条线（ts 打点 / 前端 751e68b / 微信缺口与卫生）各由独立 TA 出**只读方案** → MA 逐条 trust-but-verify 后汇总 → 用户逐项拍板 → TA 实施、MA 核验 → 迁入 `integrate/pr1`
- **性质**：审查结论 + 已实施改动 + 待办清单；**scheduler 部分不在本方案范围**（归「Scheduler→Job 统一立项」）
- **标注约定**：`[已核验]` = MA 独立读代码或实测确认；`[TA自述]` = 未独立复核；`[更正]` = 与先前结论不同
- **已落地提交**：`88049c6`（前端改造）/ `801a056`（ts 打点语义）/ `6ed6553`（调试探针与 BOM 清理）
- **当前状态**：非 scheduler 的**核心改造已实施并迁移**；剩余待办见 **第八节（导航轨优化）** 与 **第八之二节（opencode 实时缺口）**

---

## 〇、三条线的工作树与 session（实施时使用；改动已迁出）

| 线 | 工作树 | 分支 | session |
|---|---|---|---|
| ts 打点全局影响 | `D:\project\pan-worktrees\ts-stamp-impact-20260925` | `fix/ts-stamp-impact-20260925` | `ts-stamp-impact-glm53` (`ses_adb04e739f441e43`) |
| 前端 751e68b 复核 | `D:\project\pan-worktrees\frontend-port-751e68b-20260925` | `review/frontend-port-751e68b-20260925` | `frontend-port-review-glm53` (`ses_f1e55690497634be`) |
| 微信前端缺口 + 卫生 | `D:\project\pan-worktrees\wechat-frontend-gap-20260925` | `feature/wechat-frontend-gap-20260925` | `wechat-frontend-gap-glm53` (`ses_97352f173b439650`) |
| scheduler（另案，你另有决策） | `D:\project\pan-worktrees\scheduler-review-fixes-glm53-20260925` | `fix/scheduler-review-fixes-glm53-20260925` | **尚未建 session** |

全部基于 `9e839d0`；两个共享 checkout（`D:\project\Pan`、`D:\project\Pan-main`）与四个新工作树的 tracked 文件改动数**均为 0** `[已核验]`。

---

## 一、需要你拍板的 3 个决策（其余各项我按建议默认）

### D1（P0）**ts 打点语义**：导入/branch/reimport 的历史消息该不该显示时间？

**问题** `[已核验]`：打点挂在保存游标 `_hist_persisted` 上（`session.py:1809`），而 `replace_history`（`session.py:471-486`）**不重置该游标**（赋值点只有 `session.py:1515/1744/1833`）。后果：

| 场景 | 实际结果 |
|---|---|
| 等长整体替换（reimport） | `start == end` → 空切片 → **全部漏打**，且永不再补 |
| 短替换 | `start > end` → 空切片 → **全部漏打** |
| 长替换 | 只有 `[旧游标, 新长度)` 尾部被打上"现在"，头部永远无 ts |
| 全新导入 `create(history=...)` | 游标默认 0 → **整段外部会话被打上"导入时刻"**，显示成导入当天的日期 |

且**落在哪种取决于进程内缓存状态**：同一会话，服务端刚启动 vs 已被完整 GET 过，结果不同（`_from_data_without_history` 不设游标，`_from_data_with_history:1515` 设为行数）。

**选项**
- **A（TA 推荐）把打点从「save 时」移到「append 时」**：`append_history`（`session.py:462-469`）内 `setdefault("ts", now)`，删掉 `_save_body` 的 1813-1820 块。语义与游标彻底解耦；导入行不再被伪造 now（统一走"缺 ts 不显示"）；顺带缩小 D1 之外的 [P3] 时差根因。已 grep 生产代码确认除 `session.py:1761`（来自 append 的 pending 回填）与 worker 的 `del/pop`（只删不增）外无旁路。
- **B（最小侵入）给 `replace_history` 加纪元标记**：需覆盖 `worker.py` 的 6 处调用点，边界更多。
- **C（零改动）明确接受现状**：把"导入/branch/reimport 的 ts 行为取决于缓存状态"写进 PR 描述。风险：行为不可预测，外部会话可能整段显示导入日期。

> **我的建议：A**。理由：这是本 PR 唯一会**写错数据**（把历史消息标成 now）且**不可复现**（依赖进程缓存）的点；A 用更少代码同时解决它和 P3。

用户：

---

### D2（P1）**会话切换的阅读位置语义**：切回旧会话该"回到上次位置"还是"到最新"？

**问题** `[已核验]`：`ChatMessages.tsx:227-239` 有 render 期采用 `scrollSnapshots` 的块，注释明写 *"Unlike a route round-trip, selecting another session reuses this mounted component."* → 移植版让**切回任何浏览过的会话都回到上次阅读位置**。而 PR 自带文档 `docs/reports/前端UI优化记录-2026-09-23.md:355-356` 的场景 7 验证的是"切回 → **到最新**"。**两者直接矛盾**，且提交信息未提及此语义扩展 → TA 判定"无法从代码单独裁决"。

**选项**
- **A（TA 推荐，改动最小）回归文档语义**：去掉 `ChatMessages.tsx:230-239` 的 render 期采用块（该块只服务会话切换；路由重挂载由 `:107-118` 的 mount 块覆盖），使会话切换恒走 `:788` 起的置底路径，并在该路径 `scrollSnapshots.delete(currentSessionId)`。代价：失去"切会话往返也保位"（路由往返保位不受影响）。
- **B 接受新行为**：保留代码，但必须（i）在提交说明/文档记录此语义变化，（ii）补单测——当前 33 项 ChatMessages 测试**无一覆盖**"切回已浏览会话"的落点。

> **我的建议：A**。理由：与 PR 自带文档的验证结论相反的行为变化，若无明确产品意图，应按文档语义收口；且 A 改动更小。

---

### D3（P1）**微信前端接入是否在本 PR 内补齐？**

**问题** `[已核验]`：后端微信通道已完整就绪（`/api/wechat/*`、`_channel_*` 泛化、`wechatSubscriptions` 已进 session 摘要 `server.py:1768`），但 `packages/web/src/` 里**搜不到任何 wechat 引用** → 微信订阅目前只能靠 API/MCP 手工调用，Web UI 用不了。

**选项**
- **A 本 PR 补齐**（TA 给了具体改法，见 P1-9）：工作量集中在前端一个新 tab + 类型补齐 + 一处兜底逻辑修正。
- **B 本 PR 只做最小正确性修复**（只修 `AgentQueueKind` 误分类 + 补类型，不做 UI），微信 UI 另开 PR。
- **C 完全不做**，仅记录为已知缺口。

> **我的建议：A**。理由：后端已就绪而前端零接入属"功能半成品"；且 `queueStore` 把未知 kind 兜底成 `task` 是**当前就会显示错标签**的问题（`SendQueuePanel.tsx:78` 渲染成 "wechat task"），无论如何都该修。

---

### D4（P1）**聊天页面外观：是否接受"整宽+左侧色条"→"左右分栏气泡"？**

**事实** `[已核验]`：751e68b 的第 1 项**改变了默认聊天视图的外观**——这是**用户可见的产品改版**，不是内部优化。技术上是"无需修改"（虚拟化安全、单测全绿），但产品上是"要不要接受"。

| | BEFORE（merge-base `3cef25d`） | AFTER（751e68b） |
|---|---|---|
| user | `width:100%` 整宽；绿左边条 + 上下分隔线 + **`>` 前缀**（`.msg.user::before`） | **右对齐**（`.message-row-user{align-items:flex-end}`），`max-width:75%`（窄屏 94%），accent 调色背景 + 1px 边框 + `border-radius:1rem`（右下角 0.3rem） |
| assistant | 整宽；仅 `border-left:3px` 灰边条 | **左对齐**，`max-width:85%`（窄屏 96%），`bg-tertiary` 背景 + 边框 + `border-radius:1rem`（左下角 0.3rem） |
| 分栏机制 | 无 | `.message-row` flex + `align-items` |

PR 自带文档 §1.1 自述动机：*"原先 user 与 assistant 都是整宽排版，仅靠左侧 3px 颜色条区分角色…很难一眼分辨"*；§1.2 改动栏明写"**删除旧的整宽规则与 `>` 前缀**"。

**命名混乱提示（易误记）**：代码里**保留的**分支叫 "TUI-style view"（`index.css` 注释 + `tuiViewEnabled`），**弃用的**分支叫 `bubble-mode` —— 但改完后**保留的那个才是气泡外观**，弃用的反而是扁平/终端风。`uiStore.ts:304-305` 注释确认 *"The old names were reversed"*。

**选项**
- ~~A 接受新外观~~
- **B 只取技术修复、不动外观**（把第 1 项回退为整宽+色条，保留其余六项）
- ~~C 保留旧外观作为可切换项~~

> ✅ **已决策（2026-09-25，最终版）：保留两套真正独立的视图** —— TUI 恢复原样（默认视图，必须"一动都不动"），Bubble 采用本 PR 的新样式，**重新启用切换按钮**，并让命名与行为对齐。
> （此前曾定为"单纯回退气泡"；现升级为"两套视图分离 + 恢复切换"，**已按最终版派发实施**。）
>
> **责任划分（已核验）**：*命名反了* + *切换按钮被 `hidden`* + *`bubble-mode` 实际不是气泡外观* —— 这三项是**既有历史遗留**（merge-base `3cef25d` 上同源文字俱在，`git diff --name-only` 显示本 PR **未碰** `uiStore.ts` / `TopBar.tsx`）；本 PR `751e68b` 做的是**给默认（名为 TUI-style）那条分支套上了气泡皮**，从而让矛盾显形。

#### D4 落地清单（两套视图分离）

**BEFORE 基准**（merge-base `3cef25d`，已取原文，可逐字恢复）：
```css
.msg.assistant { border-left: 3px solid var(--color-text-secondary); padding-left: 10px; }
.msg.user {
  border-left: 3px solid var(--color-success);
  border-top: 1px solid var(--color-success);
  border-bottom: 1px solid var(--color-success);
  padding: 8px 16px 8px 28px; width: 100%; margin: 6px 0; position: relative;
}
.msg.user::before { content: '>'; position: absolute; left: 4px; top: 7px;
  color: var(--color-success); font-weight: bold; font-size: 0.85rem; line-height: 1; }
```
BEFORE 的 TSX wrapper（`3cef25d:MessageBubble.tsx`）：user `className={\`${mt} px-3 sm:px-6 lg:px-8\`}` + 内层 `className="msg user w-full text-sm"`；assistant 同 wrapper + `className="msg assistant text-sm leading-relaxed"`。

**要改的（两套视图分离）**

| 文件 | 位置 | 动作 |
|---|---|---|
| `index.css` | 基选择器 `.msg.user` / `.msg.assistant`（`:523-547`） | **恢复 BEFORE 原文**（整宽 `width:100%` + 3px 左色条 + `>` 前缀 + 上下分隔线）→ 服务 **TUI 视图** |
| `index.css` | `.bubble-mode` 作用域 | **承接** PR 的气泡规则：`width:fit-content` + `border-radius:1rem` + 背景 + `max-width:75%/85%`；`.bubble-mode .msg.user`（`:366`）改为气泡样式；**删除** `.bubble-mode .msg.user::before`（`:378` —— 气泡里不该有 `>`） |
| `index.css` | `.message-row-user` / `.message-row-assistant`（`:499-505`） | 左右对齐**收进 bubble 作用域**：`.bubble-mode .message-row-user { align-items:flex-end }` 等 —— 否则 **TUI 视图也会被左右分栏** |
| `index.css` | 窄屏 `@media`（`:571-579`） | 选择器跟随 bubble 作用域 |
| `MessageBubble.tsx` | `:116` / `:132` | **保留** `message-row` 与 `message-row-worker-report`（worker-report 选择器依赖前者）；宽度交给 CSS，**不恢复** `w-full` |
| `TopBar.tsx` | `:168` | **去掉 `hidden`**，恢复切换按钮（按钮逻辑与 title 已正确） |
| `uiStore.ts` | `:304-305` | **删除**过时注释 *"The old names were reversed…"*，换成描述两套视图的正确注释（**不改字段名** —— 两视图修好后 `tuiViewEnabled` / `bubble-mode` 即与行为一致） |

**必须保留（这些**不是**气泡，是别项功能，删了会砸坏）** ⚠️
- `index.css:493-497` `.message-row`（`display:flex; flex-direction:column; min-width:0`）—— 视觉上与原来的块级堆叠等价，且是下面两条的选择器前提
- `index.css:507-521` `.worker-report-label` + `MessageBubble.tsx:113-115` 的 `{workerReportLabel}` —— **item 4** 的 "Worker report" 胶囊标签
- `index.css:549-555` `.message-row-worker-report .msg.user/.assistant { border-left-width:3px; border-left-color:success }` —— **item 4** 的绿左边框。**注意它依赖行上的 `message-row-worker-report` 类**，所以 TSX 里那个类不能删
- `ChatMessages.tsx:885` 与 `ChatView.tsx:21` 的 `min-w-0`（**item 5**）—— 不是外观，是布局防御，无视觉影响，建议保留
- `index.css:552-566` 的 `.prose-kimi min-width/max-width/overflow-wrap` —— 溢出防护，非外观，建议保留

**回退后的连带影响（实现时需一并验证）**
- `MessageBubble.tsx:130` 的注释 "Assistant messages — no bubble, left-aligned, full-width markdown flow" 在回退后**才成立**（当前代码与注释矛盾）→ 可顺手校正
- `ChatMessages.test.tsx:233-234` 断言 `.message-row-worker-report` **仍在**（我们保留了该类）→ 不会失败
- 单测里的几何/落点断言（33 项 ChatMessages）需全量重跑
- 与 D2（会话切换语义）无耦合，可独立实施

---

## 二、修改清单（按风险分层）

### P0 — 必修（确定性 bug、写错数据）

| # | 项 | 位置 | 说明 | 核验 |
|---|---|---|---|---|
| 1 | ts 打点语义 | `session.py:1809-1833`、`:471-486` | 见 **D1**（需先决策） | `[已核验]` |
| 2 | **导航轨跳转中途切会话 → 全部标记永久禁用** | `MessageNavigationRail.tsx:56, 64-74, 157, 184-186, 245` | `jumpingFromEnd` 是跳转互斥锁；会话切换 effect 重置了 hovered/jumpError/activeFromEnd/fullIndex/indexTotal/indexMetrics，**唯独漏它**；`finally` 只在 `currentSessionId === sessionAtClick` 时复位 → 切走后锁永不释放，标记全 disabled、入口守卫直接 return、Loader 永久旋转，只有离开 Chat 路由才能恢复。**修法**：在 `:64-74` 的 effect 里加 `setJumpingFromEnd(null);` | `[已核验]` |
| 3 | 会话切换恢复语义 | `ChatMessages.tsx:227-239, 298, 795-798` | 见 **D2**（需先决策） | `[已核验]` |
| 4 | **行高缓存 key 失配（整段死代码）** | `ChatMessages.tsx:127-130`（读）vs `:137-138, 633-634`（写） | 读用**裸** `getDisplayItemKey(...)`；写存的是 `measurement.key`，而 `getItemKey` 产出**带 session 前缀**的 `"<sid>:<key>"` → `.get()` 必然 miss，永远 fallback `?? 100`。即"重挂载用实测行高消除漂移"的机制**从未生效**。**修法**：读侧加 `${currentSessionId ?? ''}:` 前缀 | `[已核验]` |

### P1 — 建议修（一致性 / 最小权限 / 正确性小缺口）

| # | 项 | 位置 | 说明 | 核验 |
|---|---|---|---|---|
| 5 | **生产代码两个无界调试数组** | `ChatMessages.tsx:166-167`（`__panSnapshotDebug`，每次滚动 push）、`:535-536, 549-552`（`__panRestoreDebug`，每次 layout effect push） | 挂在 `globalThis` 上**永不清理** → 长会话内存泄漏 + 每次滚动无谓分配。**无任何测试依赖**（grep 为空）。**修法**：删除（若要保留排查能力，改成显式开关） | `[已核验]` |
| 6 | `ensureMessageLoaded` 不校验会话身份 | `sessionStore.ts:1689-1713` | 只查 `!state.currentSessionId`（`:1695`），不校验是否仍是发起时的会话。跳转 A 途中切到 B → 用 A 的 `absoluteIndex` 驱动 **B** 的 `loadOlderMessages`，给 B 无谓加载多页。**修法**：入口捕获 `sid`，循环内 `if (state.currentSessionId !== sid) return null;`（与同文件 `:1735/:1641-1643` 守卫风格一致） | `[TA自述]` |
| 7 | 导航轨降级路径 `total` 错配 | `MessageNavigationRail.tsx:144-146, 162` | `allTargets` 降级时用**实时** total 算 fromEnd，而 `jumpTo` 的 `const total = indexTotal \|\| currentHistoryTotal` 优先取**建索引时**的 total → 静默跳偏「新到消息数」k 条。触发前提较窄。**注意**：fullIndex 正常路径下"fromEnd × indexTotal 同时点"是**自洽且正确**的，不要顺手改成实时 total | `[TA自述]` |
| 8 | **opencode 完整事件不打 ts** | `server.py:1346-1348` 判定 vs `opencode/adapter.py:371-372` | 判定是 `role = ev.get("role") or ev.get("type")` 且只认 `"assistant"`；而 opencode 完整事件是 `type: text/tool_use/reasoning`、无 role → **opencode 用户实时全程看不到时间**，刷新历史后才出现。**修法**：`etype == "text"` 且 `part.type != "reasoning"` 也视为完整 assistant。**前置**：需先抓一份真实 opencode 事件确认形状（仓库内无该 fixture） | `[已核验]`（形状与判定均已确认） |
| 9 | 微信前端接入 | 见「三、微信前端详细方案」 | 见 **D3**（需先决策） | `[已核验]` |
| 10 | 测试缺口 | `tests/test_session_incremental.py:336-360`、`_stamp_ws_event_ts` **零测试** | D1 定案后补三类最小断言：replace 三形状（短/等长/长）、`_stamp_ws_event_ts` 事件矩阵（可直接 import 调用，`test_ws_backpressure.py` 有先例）、前端 `formatMessageTs` 今天/非今天/非法输入 | `[已核验]`（确认现有测试只覆盖迁移路径） |

### P2 — 卫生 / nit

| # | 项 | 位置 | 说明 | 核验 |
|---|---|---|---|---|
| 11 | 两个文件带 UTF-8 BOM | `MessageNavigationRail.tsx:1`、`messageFilter.test.ts:1` | 首 3 字节 `ef bb bf`；仓库其余源文件无 BOM，tsc/vite 容忍。另 `index.css:638` 有 emoji 变乱码的注释 | `[已核验]` |
| 12 | `test_ilink.py` docstring 两处问题 | `packages/wechat/test_ilink.py:9`（`E:/python/python.exe`）、`:11-12`（**过期**声明"pytest.ini 的 testpaths 暂未含 packages/wechat"——实际 `pytest.ini:7` **已含**） | 机器路径泄露 + 过期注释会误导后来者 | `[已核验]` |
| 13 | `wechat-bridge` 模板 `system_prompt` 为空 | `manifest.json:47-57` | 见「五、更正记录」——它**不是**死配置，会出现在新建会话模板选择器里；空 prompt 使预览为空白。**建议**：保留 + 补一句说明 | `[已核验]` |

### P3 — 可选（体验 / 一致性，可不做）

| # | 项 | 说明 |
|---|---|---|
| 14 | 同一消息两个 ts（WS vs 落盘） | 两处独立 `datetime.now()`（`worker.py:2003` vs `:2110-2118`）；分钟粒度下通常不可见，handoff 退避后可跨分钟 → 时间会跳变。D1 选 A 会顺带缩小 assistant 侧时差；user 侧可选"从刚写的历史行反查 ts 复用" |
| 15 | 前端 ts 不随历史刷新收敛 | `sessionStore.ts:1244-1253` 的 `unchanged` 只比 role/content/messageId/nativeItemId，ts 不在其中 → live 行一直保留 WS ts。**注意**：把 ts 纳入比较会导致"每次历史刷新都重建"（因 live ts 与 canonical ts 必然不同），需权衡 |
| 16 | 导航轨全量索引成本 + 索引不含新到消息 | `MessageNavigationRail.tsx:81-108` 每次进会话全量拉整段历史（含全部正文）；索引建好后新消息不出现在标记里直到重进。**建议**：惰性启动（首次 hover 才索引）+ 长度上限；`allTargets` 合并 fullIndex 与 loadedWindow |
| 17 | **弃用 Bubble 视图（`bubble-mode`）的属性渗漏** | `TopBar.tsx:168` 的切换按钮是 `hidden`，且 `tuiViewEnabled` **未持久化**（uiStore 手动 localStorage 键里没有它；`:307` 字面量 `true`）→ **当前 UI 不可达，无用户可见影响**。但 `751e68b` 重写了基规则 `.msg.user/.msg.assistant`（`index.css:523-547`）：`.bubble-mode .msg { border-radius: 0 }`（`:357`）与新增的 `.msg.user { border-radius: 1rem }`（`:523`）**特异性相同**（均 (0,2,0)），后者源序在后 → **胜出**。即**将来重新启用 Bubble 视图时气泡会带圆角**。**建议**：现在只记欠账（或顺手给 `.bubble-mode .msg` 的 `border-radius` 加 `!important`）；`TopBar.tsx:166` 注释已预告"for a future re-enable" `[已核验]` |

### 判定为「无需修改」的项（要点）

- **ts 不进任何等值/去重/身份路径**：后端 `_meta_signature` 显式剔除 history（`session.py:1450-1454`）；终态防重只比 role+content（`worker.py:1029-1033`）；前端 `explicitMessageIdentity`/`messageShapeKey`/`runtimeRowCompatible` 全不含 ts → **不会产生重复消息或丢合并** `[已核验]`
- **摘要投影 / `history_total` 快路径 / 分页 / 导入导出 API / QQ·微信·MCP 消费方**均不受 ts 影响（投影只消费 `_SUMMARY_PROJECTION_KEYS`；`_api_history` 透传任意键；插件只读 role/content）`[已核验]`
- **`_save_body` 新增开销可忽略**：一次切片 + 一次 `now()` + O(k)，k 由防抖窗口限定（≤20 块或 0.5s）`[已核验]`
- **旧数据缺 ts 两侧兼容成立**：后端全链路透传缺失键；前端 `MessageTimestamp` 对缺 ts/解析失败返回 null，排序按 window offset、分组只按 role `[TA自述]`
- **`messageFilter.ts` 对既有可见性零影响**：`filterVisibleMessages` 本体一行未改，仅 3 个前缀常量 `const`→`export` `[TA自述]`
- **`index.css` +322 行无全局/元素选择器**，全部类作用域；`.msg.user`/`.msg.assistant` 这两个**类名**全库只由 `MessageBubble.tsx:118,134` 产出（无其他组件消费）。**但注意**：这两个类在 TUI 与 Bubble **两个分支**都会匹配，基规则重写对弃用的 `bubble-mode` 有渗漏——见 P3-17（原 TA 的"特异性更高保住共有属性"表述不完整，已更正）`[已核验]`
- **前端基线全绿**：`npx tsc -b` exit 0；`npx vitest run` 83 files / 814 tests 全绿（文档提到的 Toast/NewSessionModal 预存失败已不存在）`[TA自述]`
- **后端基线全绿**：21 个相关测试文件全绿（唯一 error 是 `test_real_history_coldload_e2e.py` 的 8767 端口被你正在跑的 Pan 占用——环境问题，非回归）`[已核验]`（我此前的全量跑得出同样结论）

---

## 三、微信前端接入详细方案（D3 选 A 时执行）

**A1 PostboxModal 加微信 tab**（推荐内联轻量实现，不抽通用组件）
1. `services/api.ts` 镜像 QQ 四件套：`wechatSubscribe/wechatUnsubscribe`（**不传 `bot_uin`**，微信单通道）、`fetchWechatContacts`、`fetchWechatChannels`
2. `types/index.ts:522` 附近新增 `WechatContact` / `ApiWechatContactsResponse` / `ApiWechatChannelsResponse` / `ApiWechatSubscribeResponse`（响应字段为 `wechatTarget`/`wechatSubscriptions`，由 `server.py:6898-6903` 的模板保证）
3. `PostboxModal.tsx:71` 的 tab 联合加 `'wechat'`；新增分支（**无需** QQ 的多 bot merge 逻辑）；订阅集合读 `detailSession?.wechatSubscriptions`；复用 `contactKey`（`user:<id>`/`group:<id>`）
4. 顶部加说明"联系人列表由本地记录推导（iLink 无权威联系人接口）"（`channel.py:107-110`）
5. **降级**：插件未运行时 5s 超时（`server.py:7074`）→ 显示"微信插件未运行"空态；微信 tab 的数据独立于 QQ 的 loading，避免互相阻塞

**A2 `AgentQueueKind` 加 `'wechat'`**（推荐显式扩联合）
- `types/index.ts:894` → `'task' | 'report' | 'qq' | 'wechat'`
- `queueStore.ts:177` → `rawKind === 'qq' || rawKind === 'wechat' ? rawKind : ...`
- `queueStore.ts:184-191` 的 source 兜底链加 `kind === 'wechat' ? 'wechat'`
- `SendQueuePanel.tsx:13` 加 `if (item.source === 'wechat') return '微信';`
- `[已核验]` `AgentQueueKind` 全仓仅 3 处引用、**无穷举 switch** → 扩联合零破坏

**A3 补类型**：`meta` 加 `channelTarget?` / `channel?` / `canReply?`（`types/index.ts:921`）；`Session` 加 `wechatSubscriptions?: string[]`（`:102`）

**A4 `config.example.json` 补 `wechat` 段**：仿 `qq` 段的 `_字段说明` 风格（`:163-166`），显式写 `"enabled": false`（`main.py:230` 默认关）；`mode`/`host`/`port`/`qrcode_timeout` 文档化；iLink 协议参数一句话带过

**B 卫生三项**：见 P2-12 / P2-13，及 `pytest.ini` **维持现状**（`packages/wechat` 已在 testpaths；`pytest tests/ -q` 覆盖不到插件套件是**存量行为**，`packages/qq` 同样如此，非本 PR 引入 → 建议不改约定命令）

---

## 四、验证与回归计划（实现后执行）

| 层 | 命令 / 方式 |
|---|---|
| 后端 | `D:\project\Pan-main\.venv\Scripts\python.exe -m pytest tests/ -q`；新增断言见 P1-10 |
| 后端（插件套件） | `.venv\Scripts\python.exe -m pytest -q`（裸跑，走 testpaths 收 qq+wechat） |
| 前端 | `packages/web` 下 `npx tsc -b` + `npx vitest run` |
| 端到端 | D1/D2 定案后按 `docs/reports/前端UI优化记录-2026-09-23.md` 的 6.5 场景表复测场景 6/7 |
| 注意 | `test_real_history_coldload_e2e.py` 等三个 real-HTTP E2E 需 8767 空闲；跑之前先确认你本机的 Pan 实例 |

---

## 五、更正记录（我此前说过但**不准确**的话）

1. **`wechat-bridge` 不是死配置** `[更正]`——我此前 grep 按名字搜不到就下了"无任何引用"的结论，**搜法错误**。实际链路：`config.py:22` 的 `DEFAULT_PLUGIN_MANIFESTS` 含根 `manifest.json` → `character.py:280-283` `list_session_templates()` → `GET /api/session-templates` → 前端 `NewSessionModal.tsx:60/364` 模板选择器。**它会出现在"新建会话"的模板下拉里**。（附带：TA 把该端点写成 `/api/session/templates`（斜杠）是**错的**，实际是 `/api/session-templates`（连字符，`server.py:8239`）——实现时按错的会 404。）
2. **我的 brief 说"node_modules 已就绪"是错的**——只有 `Pan-main` 有，工作树里没有。TA 的披露正确。
3. **TA 的一处描述不准**：它称 `Pan-main` 的 node_modules 是"断链"，实测是**真实目录**（`LinkType` 为空）且我早先在彼处 `tsc -b` 通过。
4. **环境操作披露**：`frontend-port-751e68b-20260925/packages/web/node_modules` 是 TA 建的 **Junction → `D:\project\Pan\packages\web\node_modules`**。已核验两个 checkout 与四个工作树 tracked 改动数均为 **0**，非破坏性。注意点：该工作树跑 build/vitest 会把缓存写进 `Pan` 的 node_modules（共享缓存，无害）；`git clean -xdf` 时留意 junction。

---

## 六、不确定项（我无法单方面裁决）

1. **D1 / D2 / D3 三个决策**（见第一节）
2. **opencode 原生事件是否真不带 `role`**：已确认 adapter 契约与打点判定不匹配，但仓库内无真实 opencode 事件样例 → 修 P1-8 前建议抓一次真实会话
3. **非 Chromium 浏览器对 `datetime.now().isoformat()` 的解析**：6 位微秒 + 无时区；V8 已实测正确（按本地时间），Safari/Firefox 未验证
4. **行高缓存修好后的隐性依赖**：不排除某测试恰好依赖"恒为 100 估算"的时序 → 修后需全量重跑 vitest（当前 814 绿是**失配状态**下的基线）
5. **真机/双标签页行为**：jsdom 无几何，滚动/恢复类结论依赖代码审查 + 文档实测数据 + 单测；文档自列的未覆盖项（真实双标签页、流式跟随真机）移植后仍未覆盖

---

## 七、待你回复的其他事项（上轮提问，尚未答复）

1. **"确保 main 工作树属于 main" 你指哪个？** `main` 当前检出在 `D:\project\pan-worktrees\main-integration-20260925`，且**本地领先 origin/main 1 个提交**；而主工作树 `D:\project\Pan` 在 `practical`。若要切换主工作树分支，会影响正在跑的 8768 服务，我不会擅动。
2. **`scheduler-discuss-glm53` 怎么处理？** 它的 workdir 是 `Pan-main` 且**不可修改**（`session_update` 无 workdir 参数）。建议：讨论留原地（只读），scheduler 的**实现**另建 session 挂到已建好的 `fix/scheduler-review-fixes-glm53-20260925` 工作树。
3. **4 个指向 `Pan-main` 的历史 session**（工作流设计 ×3 + pan-workflow-lean-migration-20260919）要不要处理？当前全部 idle，不动就不会改文件。

---

## 八、待办：导航轨优化（用户指示 —— **等 scheduler 完成、整个 PR 处理完毕后再考虑**）

> 用户 2026-09-25 明确：「导航轨有优化空间，将优化记录为待办，等到完成 scheduler、整个 PR 处理完毕再考虑优化。」

| # | 优化项 | 位置 | 现状与动机 |
|---|---|---|---|
| **N1** | **惰性启动索引** | `MessageNavigationRail.tsx:65-110` | 每次进入会话（以及每次改 settings）就**全量拉取整段历史建索引**：1301 条 ≈ **7 次请求 / 实测 938ms**；改为"首次 hover 或点击 rail 才索引" |
| **N2** | **索引加会话级缓存** | 同上 | 目前**无跨会话缓存**，切回来就重拉一遍 |
| **N3** | **索引不含新到的消息** | `:144-146` | `fullIndex` 非空后不再合并 `loadedWindowTargets` → 会话中新发的消息**永远不出现在标记里**，直到重进会话 |
| **N4** | `ensureMessageLoaded` 缺会话身份校验 | `sessionStore.ts:1689-1713` | 跳转 A 途中切到 B → 会拿 A 的 `absoluteIndex` 驱动 **B** 的 `loadOlderMessages`，给 B 无谓加载多页（即方案 P1-6） |
| **N5** | 降级路径 `total` 错配 | `MessageNavigationRail.tsx:144-146, 162` | `fromEnd`(实时) × `total`(建索引时) 的组合可能**静默跳偏 k 条**（即方案 P1-7） |
| **N6** | 超大会话的恢复精度未实测 | — | 文档只测到 1301 条；>2000 条的表现未验证（TA 自报未覆盖项） |

**已缓解的部分**：本次新增的 Appearance 开关 `Show message navigation rail`（默认开）关闭后组件**不挂载** → N1/N2 的索引成本**完全不再发生**。

**参考数据**：索引成本 1301 条 → 约 7 请求 → 938ms（PR 文档实测）。

### 八之二、待办：opencode 实时内容/时间缺口（用户指示：**先记录成待办**）

> 用户 2026-09-25：「opencode 的问题先记录成待办。」**不阻塞本次合并。**

**实测结论（2026-09-25，5 次真实 opencode 1.18.25 运行）**：

| 判定 | 结论 |
|---|---|
| `type: text` 是完整消息还是流式片段？ | **完整消息** ✅ —— 258 / 347 字符两次长输出均为**单个 part**；每个 text 事件带 `part.time={start,end}`（已完成）；`part.id` 全流程唯一不重复；`--format json` 下无 delta 形状 |
| 补后端打点是否有意义？ | ❌ **当前是死代码** —— 前端 `useWebSocket.ts:869-870` 的 `extractBlocks` 判定 `role = event.role ?? event.type`，opencode 的 `'text'` 既非 `assistant` 也非 `thinking` → **直接返回空块** |
| 真实症状（比"没时间戳"更大） | **opencode 的实时流内容根本不渲染**；刷新历史后内容与时间都有 |
| 归因 | **既有缺口，非本 PR 引入** —— `origin/main` 的 `useWebSocket.ts` 里 "opencode" 出现 **0** 次；本 PR 对该文件只加了 `eventTs` 管道 |

**修复需两处（先前端后后端，同一改动内）**：
1. **前端**（前置）：`packages/web/src/hooks/useWebSocket.ts:827-905` `extractBlocks` 增加 opencode 分支，映射对齐 `packages/core/adapters/opencode/adapter.py:374-407`（`text→assistant` / `reasoning→thinking` / `tool_use→tool`，仅 `part.state.status === "completed"` 成块）
2. **后端**：`packages/web/server.py:1338-1348` `_stamp_ws_event_ts` 扩展 `type == "text"` 且 `part.type == "text"` 也算完整 assistant

**可选更优**：opencode 事件自带 `part.time.end`（毫秒）→ 用它替代广播时刻可**顺带消除「实时 ts vs 落盘 ts」的时差**。

**素材已就绪**：`tests/support/opencode_events/`（4 个 jsonl + README，含捕获命令与事件 taxonomy）—— 位于 `ts-stamp-impact-20260925` 工作树，**尚未迁入 `integrate/pr1`**。
