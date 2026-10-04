# P1 Runner F5 机器确认字段透出证据（runner-observability）

- 工作树：`D:/project/pan-worktrees/terminal-runner-observability-20261003`
  （branch `implement/terminal-runner-observability-20261003`，seed `734ad758`）。
- 依据：MA 冻结 `docs/design/PAN_TERMINAL_COMPOSITION_BOUNDARIES_20261003.md` §I3（F5）+
  组合独立审查 `cabacc7f` 报告 B5（只读 review 树 `terminal-composition-review-20261003`）。
- 写范围：`packages/core/terminal/runner.py`、`tests/test_terminal_runner.py`、
  `docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md`、本目录。

## 本事件交付

1. **F5 实施**：`snapshot` 响应 `detail` 新增机器确认字段 `cursors_valid` /
   `reset_unconfirmed`（顶层白名单**未增**、共享契约**未改**、client/IPC/emulator/sidecar/
   service 零改动）：
   - 可信来源 = emulator 已知轻量确认面（`cursors_valid` 属性 + `diagnostics()`）；
     缺失/异常 → `null`（unknown，不默认有效）；探测不阻塞、异常只记类型名、异常文本
     不入响应（防泄漏）。
   - **一致性规则**：`cursors_valid=True` 仅在能与本次响应载荷一致可证明时透出
     （快照存在、`recovery ∈ {full, partial}`、无 `feed_lag`、reset 未确认→不含）；
     否则 `null` —— 不机械读"当前 True"覆盖过时/降级/超界降级快照。
   - 超界（serialized > 128 KiB）与 barrier 异常路径同样保守（unknown）。
   - detail 字段级缩减**全级别保留**这两类字段（`_PRESERVE_DETAIL_KEYS`），极简骨架与
     最终回退都携带。
2. **文档对齐**：接口文档 §3.2 I1–I4 更新为 MA 冻结的内部接线约束（仍 P2 未批准）：
   reason 词表 + 引擎宿主归属（runtime 宿主 = 独立 Runner 进程内 launcher/bootstrap；
   `TerminalRunner` 不隐式关闭借入对象；宿主 finally 有界关闭并保留失败 owner；Pan 只是
   IPC 控制者）、128 KiB **原始字节**/256 KiB 帧、applied 被驱逐 gap→fresh-view（非自动
   reset）、stable service client_id、原因来源分层（node reasons 只在 snapshot.note，
   `diagnostics().reasons` 为空不代表无降级，不解析 note 伪造结构化 reasons）；
   §4.4 增加 F5 字段语义与一致性规则；§10 变更记录。
3. **测试** 38 → **43**（新增 5 项 F5 门控），先失败后通过（下节）。

## 先失败 → 后通过

| 阶段 | 证据 |
| --- | --- |
| 旧代码（seed 734ad758 的 runner.py） | `evidence/pre_fix_f5_tests.txt`：5 项 F5 门控**全部失败**（`KeyError: 'cursors_valid'`） |
| 修复后直连 | `evidence/post_fix_direct.txt`：**43 passed / rc 0** |
| 修复后 uv 隔离 | `evidence/post_fix_uv.txt`：**43 passed / rc 0** |

F5 五类分列门控（`tests/test_terminal_runner.py`）：

| 用例 | 覆盖 |
| --- | --- |
| `test_f5_snapshot_detail_machine_fields_absent_engine` | **无引擎**：两字段 null(unknown)、顶层白名单未变 |
| `test_f5_snapshot_detail_old_protocol_fake_unknown` | **只旧协议 fake**（无确认面）：null(unknown)、快照可用 |
| `test_f5_snapshot_detail_trusted_source_and_consistency_rule` | **真实确认面**：partial 下 True/False 透出；degraded 快照**不得**透出当前 True（stale 保护）；reset 未确认 → unknown；源 False → False |
| `test_f5_snapshot_detail_source_exceptions_are_unknown_and_leakless` | 属性/diagnostics **异常** → null；barrier 异常路径保守；响应无异常文本 |
| `test_f5_snapshot_detail_survives_oversize_and_long_note_reduction` | **超界**（>128 KiB）降级保留字段且不假 True；**长 note 缩减**后字段仍保留、detail 合法 ≤3800 |

未跑（按 MA 口径）：11 组合 / 42 emulator / core129+8 / 全库 / 浏览器 / provider /
长稳 / 账号网络服务；emulator F1/F2 修复在并行进行，未追其在途代码。

## 复跑

```bash
E:/software/miniforge/python.exe audit/terminal/implementation/runner-observability/collect_observability_evidence.py
```

只读查询与自建资源清理；残留扫描（python runner 类）见 `summary.json`；无 stash/reset/
广杀/子代理/全局记忆写/主线 push-build-restart。
