# Pan Terminal 组合验证与接口冻结提案（真实 ConPtyBackend + Runner + IPC + HeadlessEmulator）

- 日期：2026-10-03。工作树：`D:/project/pan-worktrees/terminal-composition-20261003`
  （branch `audit/terminal-composition-20261003`，seed `46af86d2`）。
- 性质：**组合验证报告 + 接口冻结提案**。未改生产模块、未接 P2 服务；写范围仅
  `tests/test_terminal_composition.py`、`audit/terminal/implementation/composition/**`、本文件。
- 上游权威（只读）：Runner 接受记录（`a48e729d`）、Emulator 接受记录（`cd876291` 链）、
  `PAN_TERMINAL_RUNNER_INTERFACES_20261003.md`、`PAN_TERMINAL_EMULATOR_INTERFACES_20261003.md`、
  实施计划 §7（背压/gap/resize）、§8（权威快照/诚实性）、§11（T3/T4/T7/T17/T19）。
- 诚实声明：全程 **headless↔headless**，不声明浏览器渲染兼容；不涉及 provider/账号/网络服务；
  `full` 只在引擎判定矩阵内出现，未测特性一律 partial。

## 1. 装配与复跑

- 测试专属 launcher（`tests/test_terminal_composition.py` 内嵌）：同进程组合
  `TerminalRunner(..., emulator=HeadlessEmulator(cols=80, rows=24))`；runner 内起真实
  ConPTY shell；IPC 走真实命名管道 + DPAPI 秘密 + HMAC；**不注入 identity_probe**
  （必须装配 retained-handle `backend.probe`）。
- launcher 提供文件控制通道（test-only）访问同一 emulator 实例的确认面
  （`snapshot/resize_wait/diagnostics/reset_baseline/restore_screen/test_stall`）。
- **emulator 生命周期由组合方承担**：`runner.run()` 返回后显式 `emulator.close(timeout=8)`；
  硬死路径由 emulator 自持 Job guard 覆盖（实测，不泛化）。
- sidecar 依赖：`packages/core/terminal/emulator_sidecar/` 内 `npm ci`（exact pin
  `@xterm/headless@6.0.0` + `@xterm/addon-serialize@0.14.0`，node v24.15.0；`node_modules/`
  被 .gitignore，未入库、未改总依赖/锁）。pin 事实：`audit/.../composition/sidecar_pins.json`。
- 复跑：`E:/software/miniforge/python.exe audit/terminal/implementation/composition/collect_composition_evidence.py`
  （直连 = `python -m pytest tests/test_terminal_composition.py -q -rA`；uv = `uv run --no-project
  --python <python> --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout
  -- python -m pytest ...`；**不跑**旧 38/42/129/8、全库、provider/浏览器）。

## 2. 结果（直连与 uv 分列）

| 环境 | 结果 | 日志/证据 |
| --- | --- | --- |
| 直连 miniforge | **11 passed / rc 0**（87.1 s） | `pytest_direct.txt` + `evidence_direct/`（11 份用例 JSON + 10 份清理轨迹） |
| uv 0.9.14 隔离 | **11 passed / rc 0**（89.3 s） | `pytest_uv.txt` + `evidence_uv/` |
| 资源清理 | 10 会话：**0 个在清理时仍存活、0 次强杀、0 残留**（python runner 与 node sidecar 均 0） | `summary.json`、`composition_cleanup_*.json` |

| 用例 | 计划条目 | 断言要点（全部实测通过） |
| --- | --- | --- |
| `browserless_feed_eviction_protocol_a_single_consumption` | T17/T7 | 无浏览器持续供料；`total=335135` 触发日志驱逐；applied cursor 追平后 `read(cursor)` 无 gap；`[cursor,total)` 单遍按 `next_cursor` 严格推进（字节数守恒）；**headless↔headless 对拍**（checker `restore_screen`+单次 `feed_at` 的 serialized 与线上逐字节一致） |
| `utf8_osc_boundaries_no_fake_full` | §8.3 | 120 KB UTF-8（跨 64KiB 读块分割）+ OSC + 结尾标记：标记与中文字符完好；runner 快照不得优于引擎（no upgrade）；full 仅当 reasons 为空 |
| `resize_pty_and_engine_recorded_separately` | T3/§7.3 | runner `resize` 分列 `pty_resize`/`emulator_resize`；PTY 实际 30×100（runtime）；`resize_wait` 引擎确认 `[30,100]`；**过期拒绝**（stall 队列中过期不执行）后引擎/PTY 均未被假改 |
| `feed_lag_no_fake_full` | T19 | tight 队列 + 引擎停滞 → `feed_lag=true` 粘滞；runner/engine 快照均 degraded（不假 full） |
| `reset_unconfirmed_no_fake_full` | §7.2 | （stub sidecar 确定性迟到 ack；真实 runner/IPC/emulator Python 层）`cursors_valid=false`、`reset_unconfirmed=true`、快照降级；迟到 ack **一次**回填（`reset_count=1`）、reset 后仍 partial |
| `startup_engine_failure_keeps_owner` | §8/接口 | 三态：spawn 前校验（owner=None 合理）／**ready 前 EOF（缺陷 F1）**／fatal 帧（owner 保留 + `retry_cleanup` 收敛 + 无残留） |
| `pipe_disconnect_and_slow_snapshot_do_not_kill_runtime` | T6/§7.2 | 观察者断连 0.6s 后 runtime 同 PID 存活；引擎停滞期慢 snapshot 返回 degraded，另一连接心跳 5×0.0s |
| `write_and_close_workers_do_not_block_loop` | T4 | 3×96KB 输入（done/done/done）期间独立连接心跳 5×0.0s；close `exited`；emulator 显式关闭 `closed=true` |
| `clean_stop_leaves_no_sidecar_or_tree_residue` | T8/§8 | exit 0；`emulator_close{closed,graceful,process_exited,guard_closed,job_verified=true}`；shell 与 sidecar 均核验死亡 |
| `runner_hard_kill_leaves_no_sidecar_or_tree` | T12 | 同 handle 身份核验终止 runner → PTY 整树与 sidecar（emulator 自持 Job guard）均消亡；退出码非 0 |
| `detach_refused_under_constraint_zero_change` | T9（受限面） | ambient Job 下 `durability.capable=false` → `detach-refused`、runtime 同 PID、零状态变化 |

## 3. 发现（真实证据；不改生产）

### F1（中）spawn 后、ready 前 EOF 不是启动错误（无 owner）

- 实测（`compose_startup_failure_owner.json`）：注入"spawn 后立即 exit"的 stub → 构造
  **不抛** `EmulatorStartupError`、无 owner/重试入口，直接得到 `engine_dead=true` 的
  emulator；下游快照**诚实** `unavailable/none`（不假 full），`close()` 仍可收敛。
- 与 `fatal` 帧路径（同场景抛错并带 owner）**不一致**；文档 §3.1 的"握手失败抛
  `EmulatorUnavailableError`"在 EOF 分支未覆盖。
- **最小方案（报 MA，不在本轮改）**：`_spawn_and_handshake` 在 `_ready_event.wait` 返回后
  增加判定——`_startup_failure is None and not self._ready_info`（即 EOF/异常早于 ready）
  → 走 `_fail_startup("handshake-eof")`（携带 owner/residual）。一行级判定，不改协议。

### F2（高，容量）引擎排空吞吐远低于 PTY 产出：单飞 applier + 小读块

- 探针（真实引擎，135B/op 模拟真实小读块）：**307,800 B 用时 35.1 s ≈ 65 ops/s ≈ 8.8 KB/s**；
  组合 T1 生产端突发结束时 **applied=3,979 / total=331,156（1.2%），积压 2,385 ops / 331 KB**。
- 后果（均为诚实行为，无假成功）：reader 不被阻塞 ✓；积压可见 ✓；但**积压期间快照 barrier
  会超时**（实测 degraded），`feed_lag` 仅在 4 MiB 打满时 latch（≈ 数分钟级积压），
  驱逐后"完整恢复"的可达性取决于引擎追平时长。
- **最小方案（报 MA）**：applier 在出队时**合并相邻连续 feed**（`abs_start` 与前一块
  `abs_end` 相接 → 单 op 至字节上限）——减少每 op 往返；属 `emulator.py`（TA-B）内部改动，
  不改共享契约/协议帧。替代或补充：P2 层对突发输出不承诺即时 full，UI 显示恢复延迟。

### F3（中，产品口径）真实 ConPTY 会话 `full` 不可达（当前已验证矩阵内）

- 实测快照 note：`reasons=PRIVATE_MODE_9001_UNVERIFIED, CURSOR_VISIBILITY_DECTCEM_UNVERIFIED,
  WINDOW_TITLE_OSC_UNVERIFIED` → `fidelity=partial / recovery=partial`（`?9001` 与 `?25` 由
  ConPTY 自身产生，任何 cmd.exe 会话都会出现）。
- 影响：计划 §11 **T17 期望的 `recovery=full`（引擎已验证）在真实 shell 上不成立**；
  正确表述应为 "partial + 显式基线重置提示"。要么接受 partial 作为常态（推荐，保守分类器
  设计使然），要么以实测扩充白名单（需另跑引擎矩阵，属 emulator TA）。

### F4（低，接口归属）注入 emulator 的生命周期未被 runner 接口定义

- 实测：`runner.run()` 返回后 emulator 仍存活；组合层必须显式 `emulator.close()`（实测
  `closed/job_verified=true`）。硬死路径由 emulator 自持 Job guard 的**内核兜底**覆盖
  （`compose_hard_kill_no_residue.json` 实测；不泛化）。
- **最小方案**：在 runner↔emulator 接口文档明确"runner 不 close 注入 emulator；由宿主
  （服务层/组合层）在 runner 退出后以有界预算关闭，硬死由 Job guard 兜底"。若希望 runner
  内聚关闭，需新增生命周期命令（见 I1）。

### F5（低，可观测面）引擎确认面不在 runner IPC 上

- `resize_wait`、`diagnostics.cursors_valid`、`reset_unconfirmed` 只存在于 emulator
  Python 层；runner IPC 的 `snapshot` 响应只有 `fidelity/recovery/feed_lag/engine/note`
  （组合的控制通道因此是 test-only 旁路）。P2 若需 fail-closed 判定，`recovery != full`
  已足够；若要**区分原因**（lag / reset 未确认 / 引擎死亡），需要 runner 透出更多字段
  （见 I3）。

## 4. I1–I4 最小冻结建议（依据实际接线发现约束；不擅自升级 IPC/共享契约）

> 现状：I1–I4 为 runner 侧内部提案（见 runner 接口文档 §3.2）；本轮组合验证给出与
> **真实接线**相关的约束与最小建议。任何一项落地都需 MA 冻结后由对应文件所有者执行。

**I1 独立生命周期 op vs 现有映射**
- 观察：`stop.reason` 承载 close/detach（字符串词表），而 emulator 生命周期**没有**任何
  op/命令（F4）；组合验证必须由宿主旁路编排关闭顺序。
- 最小建议：不新增 op 的前提下，**冻结 reason 词表**（`close`/`explicit-close`/
  `service-shutdown`/`lease-expired`/`detach` 已实测可用）并在 runner 接口文档写明
  "runner 不管理注入 emulator 生命周期"；若 P2 需要单命令完成"停终端+关引擎"的确定性
  顺序，再评估新增独立 `shutdown` op（协议版本升级，属 ipc.py 所有者）。

**I2 snapshot 载荷上限**
- 观察（真实引擎）：80×24 + scrollback=1000 的 serialized = **82,863 UTF-8 字节**
  （probe），远超响应 `snapshot` 字段 4096 字符上限 → 必须走 `data_b64`（≤128 KiB）；
  余量 ≈ 1.6×，更浅的 scrollback/更长行会逼近上限（runner 已按 degraded+note 拒绝超界，
  不截断假恢复）。
- 最小建议：冻结"协议 A 载荷 = `data_b64` + `cursor`（applied）"，`snapshot` 字符串字段
  保持不使用；在接口文档把 128 KiB 上界的**降级路径**写成产品语义（超界 → 显式
  `degraded`，客户端 fresh-view），并明确 scrollback 与上界的关系。

**I3 describe 结构字段**
- 观察：runner 所有响应携带 `status`+`detail`（JSON 字符串，字段级缩减 ≤3800）；组合实测
  需要的 `fidelity/recovery/feed_lag/engine/note/cursor/rows/cols` 均在其中且可用；但
  `cursors_valid`（引擎侧权威判定）与 `reset_unconfirmed` 不在 runner 面（F5）。
- 最小建议：保持 `detail` 兼容演进，在接口文档**冻结 detail 的键名与语义**（至少
  `fidelity/recovery/feed_lag/engine/note` + `lifecycle.*`）；可选增补 `cursors_valid`
  （bool，取 `emulator.cursors_valid`）以支持 P2 区分"lag 降级"与"cursor 不可信"；
  不引入嵌套结构，不做 breaking 变更。

**I4 owner heartbeat 与 attachment 区分**
- 观察：runner 侧 `lease` 是 **IPC 所有者租约**；组合实测**任意已认证连接**的
  `lease(control)` 会接管（client_id 变化 → generation++，二次连接心跳 0.0s 生效）；
  浏览器 attachment lease（`attachments.py`）不在 runner 面。
- 最小建议：在接口文档冻结——① 浏览器/前端**永不**直连 runner、不持 token；② `lease`
  仅由 Pan 服务使用；③ Pan 重连应保持**稳定 client_id**（避免把同服务重连记为接管），
  接管语义（generation++）保持现状；④ 若未来需要连接级角色，走协议版本升级而非复用
  `lease`。

## 5. 未验证 / 边界（不得当作已解决）

1. **浏览器渲染/前端 fit**：全部对拍为 headless↔headless；P3 门。
2. **自然长稳/背压**：未做分钟级突发、慢客户端 ack、WS 队列（§7.2 的服务面）验证；
   F2 的吞吐数字来自 135B/op 的确定性探针。
3. **跨 sidecar 重启恢复**：引擎崩溃后只能从 OutputLog 重放（窗口内）；未验证。
4. **POSIX 宿主 / 非 Windows**：不支持（emulator fail-closed）。
5. **P2 服务/REST/WS/MCP/registry/前端接线、Ctrl-C 产品能力、真实 durable detach、
   跨用户/跨主机、provider/账号/网络服务**：未验收（沿用上游口径）。
6. **detach 仅验证受限环境拒绝**（零状态变化）；**未做**任何环境逃脱实验。
7. F1/F2 的修复方案未实施（本轮不越界改生产）；需 MA 决策后由对应文件所有者执行。

## 6. 资源与纪律（实测）

- 全量夹具：10 会话 **0 存活/0 强杀**；残留扫描（python runner + node sidecar 两类）**0**；
  硬看门狗（launcher 240s + pytest timeout）；清理只用同 handle raw FILETIME + Wait 核验的
  自有资源；未按名广杀、无 stash/reset、无主线/PR/push/restart、无子代理、无全局记忆写。
- 未跑旧 38/42/129/8、全库、provider/浏览器/账号/网络服务；本轮仅本组合套件（直连与 uv 各一次）。

## 7. 证据索引

`audit/terminal/implementation/composition/`：`pytest_direct.txt`、`pytest_uv.txt`、
`evidence_direct/`、`evidence_uv/`、`summary.json`、`uv_env.json`、`sidecar_pins.json`、
`source_blobs.json`、`probe_serialized_size.{py,json}`、`README.md`（含开发期真实发现记录）。
