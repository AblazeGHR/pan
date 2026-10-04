# Pan Terminal launcher r3 窄修 —— 实施与证据（2026-10-03）

- 树/分支：`D:/project/pan-worktrees/terminal-launcher-implement-20261003` @
  `implement/terminal-launcher-20261003`，起点 `7552319b`（r2 交付）。
- 依据：独立审查 ROUND2 `549f00bc` —— **接受**（F2–F7 窄修全部核实成立、无返工），
  遗留 2 项建议级发现：**N1**（stderr `print` 未捕获，低-中）与 **N2**（nan/inf 投影写出
  非标准 JSON，低）。本轮**只收尾 N1/N2**。
- 范围：仅 `launcher.py` / `test_terminal_launcher.py` /
  `PAN_TERMINAL_LAUNCHER_INTERFACES_20261003.md` / 本目录（新增 `r3/`）。
  共享模块、其它树、`.workflow`、P2、主线操作一律不碰；旧证据不改。
- **不撤回**已接受子集：r1 的 17 项、r2 的 46 项（含 29 项 F2–F7 门控）继续有效。
- 外部 `packages/web/.pan-validation-node-modules.junction` 为环境外部产物：保留、
  未提交、未删（故本树不称全 clean）。

## 1. N1 / N2 处置

| 项 | 修复 |
| --- | --- |
| **N1** | `_report_status_write_failure` 与 `_announce` 的 stderr 输出统一走新 helper `_emit_stderr`：`try/except` 包住，失败**只记内存静态类型诊断**（`stderr-write-failed`），**不向已坏的 stderr 递归报告**、不重试、**不引入线程**；`run()` / `main()` 的退出码与引擎收尾完全不受影响。**同类一行加固**：`main()` 顶层兜底公告也包 try/except（否则 stderr 坏 + `run()` 抛错会抛 traceback，既退化退出码又可能外泄异常文本） |
| **N2** | 新增 `_finite_seconds`：只接受 `int`/`float`（`bool` 不算）且 `math.isfinite()` 为真；`nan` / `±inf` / 转换异常（超大 int 的 `OverflowError`）/ 非数值 → `None`（保守 unknown，不造值）。状态文件因此保持**标准 JSON**（不写 `NaN` / `Infinity` 扩展）。**其余投影语义不变** |

可达性（与审查一致）：N1 需 stderr 与状态目录**同时**异常（同用户本地：fd 关闭 /
管道消费端退出 / 目录不可写 / 磁盘满），不需控制任何远程输入，不涉 token/secret；
N2 在生产路径**不可达**（emulator 的 `seconds` 来自单调时钟差或常量），属注入面加固。
**均未夸大为远程漏洞。**

## 2. 门控（新增 11 函数 / 21 用例）

- **N1（9）**：正常公告 + stderr 故障（单一条件）不逃逸、退出码维持、事件为类型名；
  status 写也失败 + stderr 故障（双故障）两类事件都在案；不递归/写入尝试次数有界 +
  哨兵零泄漏；**正常 stderr 仍公告**（退出公告行 + 写失败公告行，各一条）；
  `main()` 正常码 0 与非零码 6 原样维持；`main()` 顶层兜底仍只输出类型名（5）、
  哨兵不泄漏；`main()` 坏 stderr 下仍返回 5；用法错误仍为 argparse 退出码 2。
- **N2（12）**：`cleanup_seconds` 10 值矩阵（`nan`/`inf`/`-inf`/`10**400`/`"fast"`/
  `True`/`None` → `None`；`1.5`/`0`/`1e308` 保留）；status 严格标准 JSON
  （无 `NaN`/`Infinity` 字面量，`json.loads(parse_constant=拒绝)` 通过）。

## 3. 先失败 → 后通过

- **先失败**：`evidence/pre_fix_r3_tests.txt` —— `git archive 7552319b` 只读副本
  （副本 `launcher.py` blob `3f358bbd…` == 755 提交 blob，**equality TRUE**，
  见 `evidence/pre_fix_source.txt`）：**9 failed / 12 passed / 46 deselected**
  （N1 四项 + N2 五项在修复前不通过；通过的 12 项为正常路径与既有合法值，如实记录）。
- **后通过**：**定向集直连 57 passed / 10 deselected / rc 0**（10.63s）、
  **uv 0.9.14 隔离 57 passed / 10 deselected / rc 0**（11.06s）。
  定向集 = 新增 r3（21）+ r2 全部（29）+ r1 的 status/收尾/启动失败相关（7）；
  deselected 10 = 5 项真机跨进程用例（与 N1/N2 无关）+ 5 项不匹配关键词的 r1 门控。

## 4. 复跑入口

```bash
E:/software/miniforge/python.exe audit/terminal/implementation/launcher/r3/collect_r3_evidence.py
```

**未跑**：46 全量、13 组合、core / backend / IPC / emulator 全量、全库、浏览器、
provider、长稳（MA 令）。

## 5. 资源与残余

- 残留扫描：python launcher/runner **0**、node sidecar **0**；证据目录 `*.tmp` **0**。
- 755 只读副本与临时测试段已清理；未创建/终止任何非自有进程（无真机会话启动）。
- 残余（如实）：N1/N2 均为**加固**而非行为变更——正常路径输出与投影字段不变；
  `_emit_stderr` 的兜底诊断只进内存 events（进程退出即丢），这是刻意选择
  （避免向已坏 stderr 递归）。
- 未验证（门维持）：P2 服务/Web/MCP/registry、Ctrl-C、真实 durable detach、浏览器/
  provider/账号/跨用户/主机、长稳/背压、POSIX、跨 sidecar 重启恢复。
- 纪律：无 stash / reset / 广杀 / push / merge / build / restart / 子代理 / 全局记忆写；
  未触 8768 / 账户 / 凭据 / main / practical / `.workflow`。
