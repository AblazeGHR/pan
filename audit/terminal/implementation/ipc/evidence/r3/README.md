# r3 窄任务证据（R2A 修复 + R2B–R2F 处置）

- **起点**：`85d7650375c2fe702be620485dec314e0f904755`（本树核对 clean 后开工）。
- **输入（只读）**：复核树 round2 报告
  `PAN_TERMINAL_IPC_SECURITY_REVIEW_20261003_ROUND2.md`（证据提交 `21d287c1`）、
  audit/2 报告 `48c15231`（已接受 F1–F12 安全子集；`e018+85d765` 纳入隔离集成 `8af9b0f9`）。
- **写范围**：`tests/test_terminal_runner_ipc.py`、接口文档、`audit/terminal/implementation/ipc/**`
  （r3 新目录/工具）。**生产 3 模块零改动**（`git diff --name-only 85d765..HEAD -- packages/` 为空可核）。
- **未触碰**：复核树、其它 TA 目录、首轮/r2 证据（只新增 `evidence/r3/**`）。

## 1. R2A：先复现 → 后修复

### 1.1 复现（两条独立证据）

| 方式 | 结果 | 证据 |
| --- | --- | --- |
| **自然复现**（uv 全量 ×3） | run1 114 passed、run2 114 passed、**run3 `1 failed, 113 passed`** —— `tests/test_terminal_runner_ipc.py:1428: assert [b''] == [b'', b'']` | `pre_fix/uv_full_run_{1,2,3}.log` |
| **确定性复现**（本 TA 工具） | 客户端连上→身份拒绝→**断开之后**才 arm ⇒ 稳定得到 `transient=1, accepted=0`；**旧断言 FAILED**（`received==[]`，且 `sum(len(...))==0` 空集假通过）；新见证逻辑 PASSED | `pre_fix/r2a_transient_repro.{log,json}`、工具 `r3_repro_r2a_transient.py` |

根因（与 round2 一致）：冒充者 `accept` 在“客户端连上即断”时走 `ERROR_NO_DATA` 瞬时路径，
实例被丢弃重建、**不产生读取观测**；旧用例的精确计数断言在该路径下必然失败，而
“零凭据字节”若只对读取列表求和，会因**空集合**而假通过。

### 1.2 修复（仅测试；不删计数断言，改为可观测见证）

`_ImpostorWitness`：三类事实**分开记录** ——
`accepted`（真实 accept 次数）/ `transient`（库诊断记录的 `ERROR_NO_DATA` 事件数）/
`reads`（只对已 accept 的连接记录读取，含 0 字节）；`_SendFrameCounter` 计数客户端
`PipeConnection.send_frame`（证明“一个字节都没发”）。

断言口径（两次身份拒绝**各自可证**、服务端零凭据**可证**、瞬时与读取**分明**）：

- 每次拒绝：`witnesses() = accepted + transient ≥ 1`（有界等待，超时即失败并打印分布）；
- 每次拒绝：`"pid mismatch"` / `"identity mismatch"` 分别证明是**身份核验**拒绝；
- 每次拒绝：客户端 `send_frame` 计数不变；`credential_bytes() == 0`；`accept_errors == 0`；
- 两次拒绝合计 `witnesses() ≥ 2`，且读取中不得出现 `hello`；
- **反空集锚点**：对照段（身份精确匹配）先由读取见证**读到 hello 字节**
  （`b"hello" in hello_bytes`），证明读取观测真能捕获数据；随后冒充者会话因 token 不符
  MAC 校验失败（服务器侧认证失败）。

新增确定性回归：`test_impostor_transient_connect_is_recorded_separately_from_reads`
——瞬时事件与真实读取两类事实在同一用例内可分辨（`accepted==0 & transient>=1 & reads==[]`，
随后真实连接 `accepted>=1` 且能读到字节）。

### 1.3 修复后稳定性（全部本机实跑）

| 运行 | 结果 |
| --- | --- |
| 直连全量（80 原 + 34 r2 + 1 r3 = **115**） | **115 passed** |
| uv 隔离全量 ×2 | **115 passed / 115 passed** |
| 定向 impostor 直连 ×10 | **10/10** |
| 定向 impostor uv ×10 | **10/10** |

（修复前自然复现率：uv 全量 1/3；修复后定向 20/20 + 全量 3/3 全绿。）

## 2. R2B–R2F 处置（不扩生产修复）

| # | 处置 | 落点 |
| --- | --- | --- |
| **R2B** | **文档**：`wait_response` 超时 ≠ 放弃（保留响应槽与 pending，迟到响应仍投递、可被后续 `wait_response` 取回）；`call` 超时清槽 + `late_responses`；槽位有界 ≤ `DEFAULT_MAX_PENDING_REQUESTS=32` | 接口文档 §7-9 |
| **R2C** | **文档**：实例池只在 `accept()`/`cancel_accept()` 内补齐；服务循环不 accept 时新客户端需等下一次 accept（客户端有界重试） | 接口文档 §7-10 |
| **R2D** | **保留低危观察**：`PipeServer.close` 无 already-closed 早退（detail 恒 `"closed"`，幂等未伪造） | 接口文档 §7-11 |
| **R2E** | **保留低危观察**：`named_mutex` 的 `ReleaseMutex` 失败在 `finally` 抛出、`write/create` 的 `CloseHandle` 失败在 `finally` 抛出可能遮蔽体内原异常（关闭语义已如实上报） | 接口文档 §7-11 |
| **R2F** | **证据卫生**：pre-fix 日志中 `test_f7_unknown_ace_type_and_unparsable_allow_are_rejected` → 最终树改名 `test_f7_unknown_and_non_allow_ace_types_are_rejected`，已在 r2 README 标注映射 | `evidence/r2/README.md` |

## 2b. 日志入库策略

- **自然复现的 uv 全量日志**（修复后不可重跑，失败阶段证据）以 git add -f 入库：
  pre_fix/uv_full_run_{1,2,3}.log（run3 即 R2A 失败现场）；
- 确定性复现器的机器可读结果 pre_fix/r2a_transient_repro.json 入库（.log 可重生成）；
- post_fix/*.log 可由 collect_r3_evidence.py --rerun 重生成，按仓库 .gitignore
  （*.log）不入库；其逐运行摘要与日志 sha256 已固化在 r3_freeze.json。

## 3. 冻结产物与复跑

```bash
cd D:/project/pan-worktrees/terminal-ipc-implement-20261003
# 只解析已有日志（默认，快）
E:/software/miniforge/python.exe audit/terminal/implementation/ipc/collect_r3_evidence.py
# 重新执行同一命令矩阵（全量直连 1 + uv 2；定向 impostor 直连 10 + uv 10）
E:/software/miniforge/python.exe audit/terminal/implementation/ipc/collect_r3_evidence.py --rerun
# 确定性 R2A 复现器（瞬时路径 → 旧断言失败 / 新见证通过）
E:/software/miniforge/python.exe audit/terminal/implementation/ipc/r3_repro_r2a_transient.py
```

`r3_freeze.json` 为机器可读冻结报告：R2A–R2F 逐项（状态/根因/修复/测试 nodeid/稳定性）、
pre-fix 自然与确定性复现、post-fix 全部逐运行摘要与日志 sha256。

## 4. 局限与残余（如实）

1. **瞬时路径的确定性依赖内核 FIFO 分配**（连接落在首个未 arm 实例）：本机 20/20+ 稳定；
   其它 Windows 版本/负载下若分配顺序变化，该用例会**显式失败并打印分布**（不会假通过）。
2. 修复后的主用例**同时接受两种见证**（瞬时事件或真实读取）——这是设计目的（消息驱动的
   真实时序不可控），但两类事实本身**始终分开记录**，且对照段的字节级 hello 锚点保证
   读取观测有效（零字节结论不空集假通过）。
3. R2B/R2C 为语义说明，未改行为；R2D/R2E 未修复（按指令保留为后续整理项）。
4. 跨用户/远程拒绝仍未实测（沿用 R1/R2 声明）；本任务未触碰生产模块，未新增生产行为。
