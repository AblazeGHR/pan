# F5 r3 观测兜底证据（独立审查 68e0656e MA 注意 1/2）

- 工作树：`D:/project/pan-worktrees/terminal-runner-observability-20261003`
  （自树 `6ca24bb6` clean 起点 → 本目录对应提交）。
- 依据：独立审查 `68e0656e`（固定 `6ca24bb6`）——47×2 与严格 bool/baseline/reset 子集
  **已通过不撤回**；本事件只做低危观测兜底与口径修正，**不接 P2**。
- **分级**：受信观测面（同用户、已认证组件注入/损坏才可达）低危，**不是远程绕过**；
  真实 emulator 的 baseline 为短十进制字符串 → 真实路径不可达，仅注入/损坏组件可达。
- 写范围：`runner.py`（仅 `_ledger_int_or_none`）、对应 F5 定向测试、接口文档、本目录；
  其余树与旧证据（上层 `evidence/`、`r2/`）零覆盖。

## 修复（最小）

1. **超长 ASCII 数字**：`int()` 在 CPython 整型转换默认 **4300 位**上限之上抛 `ValueError`
   ——`try/except ValueError -> None`（**不抛错**、未知 baseline 按现有确认来源规则
   **不约束、不机械降级**；未改任何 cursors_valid 判定规则）。
2. **解析口径**（doc 如实写明）：字符串**允许两侧空白**，去空白后须为 ASCII 数字、
   **可带一个正号 `+`**；**负数（int 或字符串）统一未知**（非法绝对偏移）；
   bool 仍 None。**不扩大解析器、不改共享契约**。

## 先失败 → 后通过

| 阶段 | 证据 |
| --- | --- |
| 6ca **固定只读副本**（`git archive 6ca24bb6 packages tests`） | `evidence/pre_fix_r3_tests.txt`：3 项 r3 门控 **2 failed / 1 passed / 47 deselected**（失败点：`return int(text)` 抛 `ValueError: Exceeds the limit (4300 digits)`；解析矩阵负数期望） |
| 副本同源证明 | `evidence/pre_fix_source.txt`：副本 `runner.py` blob == 6ca 提交 blob `070b6c05…`（hash-object 等值） |
| 修复后直连（定向集合） | `r3_direct.txt`：**12 passed / rc 0** |
| 修复后 uv 隔离（定向集合） | `r3_uv.txt`：**12 passed / rc 0** |

r3 门控（`tests/test_terminal_runner.py`，`f5_r3` 前缀）：

| 用例 | 覆盖 |
| --- | --- |
| `test_f5_r3_ledger_int_parser_matrix` | 空白/+ 兼容；负数（int/str）None；bool None；非法/纯空白/浮点/全角/内嵌空白/孤立符号 None；4300 位合法、4301/5000/20000 位 **None 不抛**；2^53 精确 |
| `test_f5_r3_long_digit_baseline_snapshot_no_raise` | 端到端 `snapshot()` **不抛**、detail 可解析；未知 baseline **不约束**（cursors_valid 保持 True，不机械降 False/null） |
| `test_f5_r3_whitespace_plus_negative_baseline_behavior_lock` | 空白/+ 照旧参与 stale（→unknown）；负数统一未知（不约束→true）；2^53 回归 |

运行口径：**仅 F5/ledger 定向集合**（`-k f5`，12 项）；**不重复** 47 全量 / 11 组合 /
48 emulator / core / 全库 / 浏览器 / 长稳。旧日志不补造。

## 复跑

```bash
E:/software/miniforge/python.exe audit/terminal/implementation/runner-observability/r3/collect_r3_evidence.py
```

只读查询与自建资源清理（同 handle raw FILETIME + Wait）；残留扫描见 `summary.json`；
无 stash/reset/广杀/主线/push/build/restart/全局记忆。
