# P1 Terminal 组合验证证据（真实 ConPtyBackend + Runner + IPC + HeadlessEmulator）

- 工作树：`D:/project/pan-worktrees/terminal-composition-20261003`
  （branch `audit/terminal-composition-20261003`，seed `46af86d2`）。
- 上游接受记录（只读）：`docs/design/PAN_TERMINAL_RUNNER_ACCEPTANCE_20261003.md`（a48 子集）、
  `docs/design/PAN_TERMINAL_EMULATOR_ACCEPTANCE_20261003.md`（emulator 子集）、
  接口文档 `PAN_TERMINAL_RUNNER_INTERFACES_20261003.md` / `PAN_TERMINAL_EMULATOR_INTERFACES_20261003.md`、
  实施计划 §7/§8/§11。
- 结论与接口提案：`docs/design/PAN_TERMINAL_COMPOSITION_REVIEW_20261003.md`。
- 复跑入口：
  `E:/software/miniforge/python.exe audit/terminal/implementation/composition/collect_composition_evidence.py`

## 装配（测试专属 launcher，非生产模块）

- `tests/test_terminal_composition.py` 内嵌 launcher：同进程组合
  `TerminalRunner(terminal_id, secret_file, emulator=HeadlessEmulator(...))`；runner 内部
  经 `ConPtyBackend.spawn` 起真实 ConPTY shell；IPC 走真实命名管道 + DPAPI 秘密 + HMAC；
  **不注入 identity_probe**（必须装配 retained-handle `backend.probe`）。
- launcher 额外提供**文件控制通道**（test-only）：对同一 `HeadlessEmulator` 实例调用
  `snapshot/resize_wait/diagnostics/reset_baseline/restore_screen/test_stall` 等确认面
  （runner IPC 不暴露这些方法）。
- **emulator 生命周期归组合方**：runner 不关闭注入的 emulator；launcher 在 `runner.run()`
  返回后显式 `emulator.close(timeout=8)` 并记录 `EmulatorCloseReport`；runner 硬死路径由
  emulator 自持 Job guard 的内核兜底覆盖（实测记录，不泛化）。
- sidecar 依赖：在 `packages/core/terminal/emulator_sidecar/` 执行 `npm ci`（exact pin
  `@xterm/headless@6.0.0` / `@xterm/addon-serialize@0.14.0`；`node_modules/` 被 .gitignore，
  未入库、未改总依赖/锁）；pin 事实见 `sidecar_pins.json`。

## 证据清单

| 文件 | 内容 |
| --- | --- |
| `pytest_direct.txt` / `evidence_direct/` | 直连 11 项组合套件 + 每用例 JSON |
| `pytest_uv.txt` / `evidence_uv/` | uv 隔离（`--no-project` + minimal-requirements + pytest/pytest-timeout）同套件 |
| `summary.json` | 环境、退出码、耗时、清理汇总、残留扫描（python runner / node sidecar 两类） |
| `uv_env.json` | uv 版本与两条实际命令行（仅本组合套件；不跑旧 38/42/129/8/全库） |
| `sidecar_pins.json` | sidecar 依赖 pin 与 node_modules 存在性/忽略事实 |
| `source_blobs.json` | 测试/文档/本 README 的暂存 blob id 锚定 |
| `probe_serialized_size.py` / `.json` | 协议 A serialized 载荷与引擎吞吐量化探针（真实引擎，无 runner） |
| `composition_cleanup_<test>.json` | 每个用例的清理轨迹（同 handle 身份核验；含 `alive_before_cleanup`） |

## 开发期真实发现（先失败/发现按真实当前证据，不造基线）

1. **managed lease 语义**：首版用例在大输出/停滞步骤里没有心跳 → runner 按 2s 死期自停、
   管道 EOF（测试失败现场）。这不是产品缺陷（契约要求 Pan 持续心跳），修正为 launcher
   会话自动启动**独立连接心跳保持者**（也顺带覆盖"心跳独立连接"）。
2. **引擎吞吐与 op 队列（F2）**：快照控制 op 会排在积压 feed 之后 → 单个 `snapshot()`
   在积压期超时；等待追平改用本地 `diagnostics()` 轮询，线上快照只在追平后取一次。
   量化见 `probe_serialized_size.json`（65 ops/s ≈ 8.8 KB/s；307800B 需 35.1s 排空）。
3. **测试侧假阳性**：最初用"尾部 marker 出现次数==1"判单次消费，但 marker 在原始流中
   合法出现多次（cmd 回显 + 输出 + ConPTY OSC 标题）；改为结构性断言（字节数守恒 +
   cursor 严格推进 + 无 gap），双消费由 headless↔headless 对拍兜底。
4. **证据命名修正**：清理证据首版同名覆盖 → 改为按用例命名；teardown 先等 launcher
   退出窗口收敛再判存活（消除"正在退出"被记为存活的竞态）。

## 纪律

- 只写允许路径（`tests/test_terminal_composition.py`、本目录、组合评审文档）；未改生产
  runner/emulator/backend/IPC/共享契约/依赖/服务；无 stash/reset/push/restart/主线操作、
  无按名广杀、无子代理、无全局记忆写；未跑旧全量/全库/provider/浏览器/账号/网络服务。
- 清理只针对自有资源（同 handle raw FILETIME + Wait 核验）；硬看门狗（launcher
  `PAN_COMP_HARD_TIMEOUT` + pytest timeout）。
