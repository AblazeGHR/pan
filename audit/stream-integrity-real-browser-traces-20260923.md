# 2026-09-23 Codex 消息重复与错位：真实浏览器轨迹

范围：用户在 practical 的 `session-1`（`ses_8a7dacbd23488c6c`）使用专用 Chrome 采集。下面的两条轨迹是在修复候选运行到 practical 之前取得，因而是故障证据，不是修复后的真实 provider 验收。

## 完成后重复

- 轨迹：`packages/web/test-results/browser-trace-20260923-171136-028/trace.ndjson`；标记截图 `marker-1790154969531.png`。
- 2026-09-23 09:16:00.885–.897 UTC：`worker.result` 后，本地 DONE 行使 store 由 8 增到 9 行；紧接着历史页合并使 store 由 9 增到 11 行，DOM 也渲染 11 行。新增的持久用户行和助手行与 runtime 中的同一轮用户/助手副本内容摘要相同。
- 在该时刻只读检查该 Session 的历史 JSONL，共 6 行，其中第二轮用户与助手各 1 行。因此这一次截图里的额外副本出自前端投影。
- 前一轮 runtime 把已完成助手行放在 thinking 行前，持久历史把 thinking 放在该助手行前，并含另一条先前助手行。`projectTranscript()` 的单序列对齐在这里失配，后续轮次的持久行也被再次追加。

## 直播中正文错位

- 轨迹：`packages/web/test-results/browser-trace-20260923-172102-140/trace.ndjson`；标记截图 `marker-1790155681233.png` 等 5 张。
- 当前任务 `taskSeq=11`、`turn_id=01a0cd97-54ce-7711-a3b1-93625d9b1f9b` 中，Codex 按不同 `item_id` 发出多段正文，段间有工具项。
- 09:27:57.859 UTC、事件序号 274395：新正文 item `msg_02e6...9dc2fc...` 的首段累计 delta 长 2 字；下一次 store 变化却把旧正文 item `msg_02e6...b81525...` 的内容长度从 142 改为 2，该行仍位于后续工具行之前。接下来的累计 delta 持续改写这条旧行，直到新 item 的完成事件到达。
- 这是前端 turn 别名把不同显式 item ID 归并到首条已完成助手行的路径；原始 WS 帧和 store 行身份直接支持该判断。它不同于上面的历史合并重复。

## 隔离树候选修复及验证边界

- `useWebSocket.ts`：累计 `stream_text` 严格沿显式 `item_id` 更新；保留首个 delta 与不同 ID 的首次完成事件之间的窄别名桥，以及旧版无累计文本的迟到 delta 兼容。已完成正文的迟到累计前缀视为回声，不抢占新 item 身份。
- `sessionStore.ts`：完成标记界定任务范围，在新历史页覆盖整个任务时按绝对历史顺序对齐该任务的 runtime 行；持久行随后优先。历史页插入造成缓存索引失效时，仍按任务局部 runtime key 找回原直播行。
- 轨迹导出的定向 Vitest 与现有相关回归通过；完整前端 E2E 36 阶段通过，但它在最后一笔 turn 别名修复之前启动。最终代码另通过定向真实 FastAPI/Worker/JSONL/WS/Chromium E2E：同一 turn 五段正文、四个交错工具，每段直播中 DOM 1 行，结束及刷新后持久历史与 DOM 都各 5 行。该 E2E 的 provider 边界是确定性假 CLI。
- 定向 E2E 证据：`packages/web/test-results/codex-multi-item-1790156575453/evidence.json`；独立数据根在同目录，端口 8765，服务 PID 记录于证据，退出后端口空闲。
- 最终构建后的首次定向 E2E 启动曾以 Windows 退出码 `3221226505` 在写出证据前退出；当时 8765 无监听。直接重跑同一脚本通过，并写出上面的完整证据。该首次异常没有可用的应用失败轨迹，不能归因为修复代码。
- 这些验证尚未覆盖“修复后的真实 Codex provider + 用户工作浏览器”组合；当前 practical 未合并此隔离树改动。
