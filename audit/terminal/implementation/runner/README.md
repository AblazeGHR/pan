# P1 Terminal runner 实现证据（布局 B）

- 任务：T-TERMINAL-PTY-20261003 的 **P1 独立 Terminal runner 生产实现**。
- 工作树：`D:/project/pan-worktrees/terminal-runner-implement-20261003`
  （branch `implement/terminal-runner-20261003`，起点 `8af9b0f9`）。
- 交付物：`packages/core/terminal/runner.py`、`runner_client.py`、
  `tests/test_terminal_runner.py`、
  `docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md`（接口冻结）+ 本目录证据。
- 未改任何共享文件（`__init__/contracts/runtime/ipc/win_pipe/secret_store/backend/
  guard/identity/spawn_win/requirements/lock/server/MCP/frontend/background_jobs`）。

## 1. 复跑方式

```bash
# 全量隔离真机（Windows；约 60–90s）
E:/software/miniforge/python.exe -m pytest tests/test_terminal_runner.py -q

# 一键重跑证据（含只读探针 + 汇总 + 残留扫描）
E:/software/miniforge/python.exe audit/terminal/implementation/runner/collect_runner_evidence.py --label <label>
```

## 2. 证据目录

| 路径 | 内容 |
| --- | --- |
| `evidence/<label>/pytest.log` | 全量测试输出（含退出码） |
| `evidence/<label>/<test>.json` | 每个用例写入的机器可读断言证据 |
| `evidence/<label>/cleanup_<test>.json` | 每个用例的资源清理轨迹（身份核验后终止） |
| `evidence/<label>/runner_runs.json` | 环境、退出码、耗时、清理汇总、残留进程扫描 |
| `evidence/<label>/ambient_constraint.json` | 宿主 ambient Job / breakaway / durability 能力 |
| `evidence/<label>/dead_process_probe.json` | `probe_process` 三态在“引用保留/完全回收”下的实测 |
| `evidence/<label>/response_whitelist.json` | 冻结响应白名单拒绝未知顶层字段（为什么 detail 折叠） |
| `probes/*.py` | 上述三个只读探针源码（可独立复跑） |

## 3. 覆盖矩阵（`tests/test_terminal_runner.py`，19 项）

| 任务要求 | 用例 |
| --- | --- |
| 管理正常心跳保活 / 崩溃 2s 死期整树自停 | `test_managed_lease_heartbeat_then_expiry_stops_tree` |
| 浏览器/观察者断连不改变 runtime、不续约 | `test_observer_rotation_does_not_change_runtime` |
| detach（受限显式拒绝 / 注入能力下同 PID+FILETIME+shell 变量+新 controller） | `test_detach_refused_when_ambient_job_constrains_durability`、`test_detach_semantics_same_pty_reconnect_and_shell_variable` |
| 根死孙活 → 整树清理 | `test_root_exit_with_live_grandchild_is_tree_cleaned` |
| runner 硬死 → guard 内核清整树 | `test_runner_hard_kill_closes_guard_and_cleans_tree` |
| startup / 身份 / 认证拒绝 | `test_startup_refuses_identity_mismatch_without_listening`、`test_wrong_token_handshake_rejected_without_touching_runtime` |
| token 无 argv/env/日志/文件泄漏（含 env 自检拒绝） | `test_no_token_leakage_in_argv_env_logs_and_files`、`test_runner_refuses_token_in_environment` |
| cleanup-failed 保 owner + 重试收敛 | `test_cleanup_failed_keeps_owner_and_retry_converges` |
| 阻塞 write 不堵 watchdog / 不叠加 | `test_blocked_write_does_not_stall_watchdog_or_other_connections` |
| 跨进程 DPAPI secret 重连 | `test_new_process_reconnects_with_dpapi_secret` |
| 固化期限不过期执行 + terminal_id 绑定 | `test_terminal_binding_and_expired_deadline_never_execute` |
| 快照协议 A（serialized + applied cursor） | `test_snapshot_protocol_a_with_injected_emulator` |
| 输出日志驱逐 gap + 绝对偏移续流 | `test_output_log_eviction_publishes_gap_and_exact_totals` |
| 真机交互往返（输入/输出/关闭） | `test_real_pty_roundtrip_read_input_and_close` |
| 仿真器桥接（feed_at 扩展 / 缺口粘滞降级） | `test_emulator_bridge_gap_detection_and_feed_at_extension` |
| durability 能力与 ambient 事实一致 | `test_durability_capability_matches_ambient_job_reality` |

## 4. 本机环境事实（2026-10-03）

- Python 3.12.12（`E:/software/miniforge/python.exe`，MSC v.1944 64bit）。
- **宿主 ambient Job 存在**：`IsProcessInJob(GetCurrentProcess(), NULL) = True`；
  `CREATE_BREAKAWAY_FROM_JOB` 被拒（WinError 5）→ 无法在进程内清除该限制。
  因此本环境 `durability.capable = False`，runner **显式拒绝 detach**（fail-closed）；
  detach 语义的机制验证使用测试注入的 `durability_probe`（wrapper 模式 `capable`），
  真实环境下的 durable 承诺**未实证**（不得宣称）。
- 真实 ConPTY 不会因输入量阻塞：实测连续写入 2.7 MB 输入全部被吸收（每块 96 KiB，
  单块 ~60–90 ms）。因此“阻塞 write”的行为验证使用测试注入 `write_hook`（gate 文件）
  把阻塞放进真实 runner 进程的 write worker；生产不注入（默认 `runtime.write`）。
  同一探测还顺带暴露：停止心跳约 2 s 后 runner 正确进入 `closing`（死期语义生效）。

## 5. 开发期缺陷与修复（先失败后通过）

| # | 现象 | 定位 | 修复 |
| --- | --- | --- | --- |
| 1 | detach 拒绝响应把 `durability` 放顶层 → `build_response` 抛 `MalformedFrameError` → 请求以 `handler-error` 结束（先失败：`test_detach_refused_...`） | 冻结响应白名单不含该字段（`probe_response_whitelist.py` 复现） | `runner._payload` 只输出白名单字段 + 未知字段**自动折进 detail**；detach 信息改走 detail |
| 2 | close 后 shell “未死”断言失败（先失败：`test_real_pty_roundtrip_...`） | 测试侧只认 `probe==DEAD`；完全回收的进程给 UNKNOWN（`probe_dead_process_unknown.py` 复现） | 测试死亡判定叠加 PID 存在性 + FILETIME（防 PID 复用） |
| 3 | 阻塞写用例未观测到阻塞（8×96 KiB 全 done） | 真实 ConPTY 吸收输入（§4） | 用例改为注入 gate 的真实 worker 阻塞，并断言 busy 不叠加 + 心跳延迟 |
| 4 | bootstrap 首轮探针遇到一次 `SecretSecurityError: secret path escapes the secrets directory`（全量运行中出现 1 次，随后 3 次全量 + 30 次 spawn 压力均未复现） | 疑为 secrets 目录“首轮探针之后才出现”造成的路径解析窗口 | 测试夹具改为先 `ensure_secrets_dir()`（P2 等价动作）再 spawn；并加入失败路径诊断取证（`bootstrap_failure_*.json`）。**根因未确证，如实记录**；失败方向是 fail-closed（未读秘密、未启动） |

## 6. 未验证 / 边界（不得当作已解决）

1. **durable detach 的真实环境实证**：本机 ambient Job 不可清除（§4），仅在能力
   注入下验证机制；真实宿主 Job 下的 detach 未实证（产品侧必须按能力位拒绝或声明）。
2. **权威仿真器引擎**：`emulator.py`/sidecar 属并行 TA；本层只提供桥接与降级
   （无引擎快照 `fidelity=unavailable/recovery=none`）。测试用替身验证协议 A 编码。
3. **kill 路径的退出码**：runner 硬死由外部终止（本机实测 exit code=1），
   `RUNNER_EXIT_CLEANUP_FAILED=3`（死期清理未收敛）路径未在真机触发。
4. **`stop` 超时后的迟响应**：依赖 ipc 的 `late_responses` 计数；本层只断言
   `closing` 语义与恢复路径。
5. **跨用户/跨主机拒绝、旧 build ConPTY、长稳压测**：沿用 P1 IPC/backend 报告边界，
   本 TA 未新增实测。
6. Service/REST/WS/MCP/registry/前端接线未见（P2/P3）。

## 7. 资源清理声明

- 测试与探针只创建**自建**资源（临时数据根、runner 子进程、ConPTY shell、命名管道、
  DPAPI 秘密文件）；所有子进程在用例收尾以 **同 handle 身份核验**（`probe_process`）
  后经 `terminate_verified_process` 终止，不按命令行广杀、不触碰既有父进程。
- 收集器附带残留扫描（PowerShell 只读查询命令行含
  `packages.core.terminal.runner` 的 Python 进程），期望计数 0。
- 进程内 runner 的 Job 守卫为 `KILL_ON_JOB_CLOSE`：runner 硬死时由内核清理整树
  （用例 `test_runner_hard_kill_closes_guard_and_cleans_tree` 断言）。

## 8. 验证记录（发布前）—— **含证据更正（O6）**

> **更正（r2，2026-10-03）**：下表首版引用的 `evidence/final/pytest.log`、
> `evidence/final/stability_run_{1,2}.log` 与开发期 4 项失败阶段日志，
> **未入库**（`.log` 被 `.gitignore:77` 拦截；审查树/提交物中不可见）——
> **不可核，不再引为证据、不再声称其存在**；本工作树中同名未跟踪副本同样不作为证据
> （r2 依据一律为 `r2/` 的 `.txt` + JSON）。其中"19 passed ×3、探针 exit 0、清理 0 残留"
> 这些**结论**有独立审查复跑（`runner-review` 树）与 `runner_runs.json` 交叉支持；
> 完整日志与失败阶段原文当时未保存，历史缺失如实记录。

| 项 | 结果 | 证据（r2 复跑见 `r2/`） |
| --- | --- | --- |
| 本套件全量（隔离真机，d48 版 19 项） | **19 passed / exit 0**（75.2 s；独立审查复跑一致） | `runner_runs.json`、`evidence/final/*.json`（日志未入库，见更正） |
| 发布前稳定复跑 ×2 | **19 passed / exit 0** ×2（结论有独立审查复跑佐证） | 日志未入库，见更正 |
| 只读探针 ×3 | 全部 exit 0（ambient 限制 / 死进程三态 / 白名单拒绝） | `evidence/final/*.json` |
| 资源清理 | 17 个句柄、0 个在清理时仍存活、0 个残留进程 | `cleanup_*.json`、`runner_runs.json` |
| 相邻套件回归（非本 TA 交付，仅作不回归证据） | terminal core/driver：73 passed + 7 skipped（主环境缺 pyte）；IPC/secret/backend/guard/identity/broadcast：176 passed | 未随附日志（见下注） |

注：相邻套件的一次**后台批跑**出现 2 例时序 flake
（`test_impostor_pipe_server_identity_is_rejected_before_credentials`、
`test_f9_concurrent_writes_and_identity_updates_preserve_fields`）；隔离复跑 2/2 通过、
前台同批复跑 176 passed 全绿。其中“冒充服务”用例的瞬态观测冲突已由 IPC 验收文档
记录（`PAN_TERMINAL_IPC_ACCEPTANCE_20261003.md`）；本 TA 未改他人测试与任何共享模块。
