# 被取消全量 pytest 的两处 F — identity 与基线对照

## 原始日志（已保全）

- `evidence/shortcuts-settings-20261019/full-pytest-cancelled.txt`
- 来源 Job：`job_d4a0978911f224addbafbb26`
- 取消时进度：1152 个测试字符，约 27%
- 进度字符中 `F` 位置（1-based，按 pytest collection/progress 顺序）：
  - **104**
  - **603**
- 跳过位置：726、934、1150、1151、1152

## 按 collection 顺序定位

采集命令（只收集，不执行测试；隔离入口）：

```text
python scripts/run_tests_isolated.py -- \
  python -m pytest tests/ packages/qq packages/wechat \
  -o addopts= -q --collect-only
```

结果：`4125 tests collected`；完整顺序保存为 `pytest-collect-order.txt`。

- 104 →
  `tests/test_backend_perf_opt.py::test_result_flushes_debounced_blocks_through_read_stdout`
- 603 →
  `tests/test_codex_resumed_init.py::test_resumed_init_marks_worker_ready_without_replaying_queue_or_history`

相邻节点（用于核对顺序）：

```text
103 tests/test_backend_perf_opt.py::test_stream_block_count_cap_flushes
104 tests/test_backend_perf_opt.py::test_result_flushes_debounced_blocks_through_read_stdout
105 tests/test_backend_perf_opt.py::test_idle_status_broadcast_on_result

602 tests/test_codex_resumed_init.py::test_large_resumed_thread_init_survives_real_worker_framing
603 tests/test_codex_resumed_init.py::test_resumed_init_marks_worker_ready_without_replaying_queue_or_history
604 tests/test_codex_session_conversion.py::test_live_conversion_stages_config_preserves_thread_history_queue_and_never_interrupts[codex-True]
```

## 定向复现（当前分支）

命令只跑上述两个节点，未改断言/skip：

```text
python scripts/run_tests_isolated.py -- \
  python -m pytest <node-104> <node-603> \
  -o addopts= -vv --junitxml=.../pytest-suspects-rerun.xml
```

结果：**2 failed in 0.75s**

- 104：`TypeError: fake_save() got an unexpected keyword argument 'offline_override'`
- 603：`AssertionError: assert False`（`Worker.native_kernel_ready` 仍为 False）

## 基线对照（e4078c18）

用 `git archive e4078c18` 解到临时目录（未切分支、未动 main/practical，检查后删除），在同一隔离入口、同一解释器下只跑这两个节点：

```text
git archive e4078c18 | tar -x -C <temp>
cd <temp>
python scripts/run_tests_isolated.py -- \
  python -m pytest <node-104> <node-603> \
  -o addopts= -vv --junitxml=.../pytest-suspects-baseline.xml
```

结果：**2 failed in 0.69s**，错误签名与当前分支一致。

## 结论

两处 `F` 是 **基线 e4078c18 已存在的失败**，不是本 Shortcuts 分支引入；与本次改动文件（前端、settings API 测试、e2e launcher）无交集。

证据：

- `pytest-suspects-rerun.xml` — 当前分支
- `pytest-suspects-baseline.xml` — e4078c18
- `pytest-suspects-baseline.txt` — 基线完整输出
- `pytest-collect-order.txt` — 4125 个节点的收集顺序
- `full-pytest-cancelled.txt` — 原始被取消日志

未执行全量本地 pytest 重跑；未修改断言、未 skip。
