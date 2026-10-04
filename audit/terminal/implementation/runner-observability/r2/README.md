# F5 r2 收尾证据（诚实性窄化；起点 e822e0b7）

- 工作树：`D:/project/pan-worktrees/terminal-runner-observability-20261003`（本树 e822 clean 起点）。
- 依据：F5 独立 audit `cc3011af`（43 双环境/来源矩阵接受、无新增安全阻塞）+ MA 收尾口径。
- 写范围：`runner.py` / `test_terminal_runner.py` / 接口文档 / 本目录；旧证据（上层 `evidence/`）零覆盖。

## 本次窄化（三点）

1. **严格 bool 来源**：`cursors_valid` / `reset_unconfirmed` 只接受**真正 bool**
   （`isinstance(v, bool)`）；真值型非 bool（str/int/float/list/dict…）→ `null`（unknown），
   **不做 bool 强转**。`cursors_valid=true` 的来源契约=**自含 reset 未确认判定**
   （真实引擎属性在 reset 未确认时为 False）——来源契约，非 runner 核验；doc §4.4 写明。
2. **baseline 交叉（同一次 diagnostics 读取）**：取 `baseline_cursor`，仅**合法 int（非 bool）**
   或 **ASCII 十进制字符串**参与比较（未知/非法/空白**不造值**、不加约束）；
   `baseline > snap.cursor` → 快照早于最近一次已确认 reset → `cursors_valid=null`；
   Python int 精确比较（2^53 大整数门控）。
3. **口径与来源契约（doc）**：由"一致可证明"改为**保守近似**——明确
   **没有历史世代原子绑定**（来源值在快照之后读取），不能称一致可证明；
   来源需**无 IO、有界短临界区**（真实引擎成立）；runner **不隔离慢源**，
   客户端 `timeout_ms` **不含**确认面额外延迟；`detail` 缩减（`truncated_fields`）或
   上下文键缺失时，机器字段**不得单独**解释为完整恢复；**不解析 note/reasons**。

## 先失败 → 后通过

| 阶段 | 证据 |
| --- | --- |
| e822 旧代码 | `evidence/pre_fix_r2_tests.txt`：4 项新门控**全部失败**（强转/stale/大整数/reset 门控） |
| 修复后直连 | `r2_direct.txt`：**47 passed / rc 0** |
| 修复后 uv 隔离 | `r2_uv.txt`：**47 passed / rc 0** |

新门控（`tests/test_terminal_runner.py`，`f5_r2` 前缀）：

| 用例 | 覆盖 |
| --- | --- |
| `test_f5_r2_confirmation_sources_strict_bool_types` | 真值型非 bool（"true"/1/0.0/[]/{}/"yes"）→ null；真 bool 正常 |
| `test_f5_r2_baseline_cursor_stale_snapshot_and_no_coercion` | int/字符串 stale → null；`True` 基线**不得**强转为 1；非法/空白/None 不造值不加约束 |
| `test_f5_r2_baseline_cursor_legal_big_integer` | 2^53 级十进制字符串精确比较：stale → null、追平 → true |
| `test_f5_r2_reset_completion_gate_snapshot_before_after` | reset 前快照（cursor<基线）→ null；reset 完成后 → true |

保留：审查 5 疑点原证据（上层 `evidence/`，含 `pre_fix_f5_tests.txt` 与 43 项双环境日志）**零覆盖**。

## 复跑

```bash
E:/software/miniforge/python.exe audit/terminal/implementation/runner-observability/r2/collect_r2_evidence.py
```

只读查询与自建资源清理（同 handle raw FILETIME + Wait）；残留扫描见 `summary.json`；
无 stash/reset/广杀/子代理/全局记忆写/主线 push-build-restart；不接 P2、不实现 launcher。
