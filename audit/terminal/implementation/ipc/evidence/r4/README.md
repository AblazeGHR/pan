# r4 批量测试收尾证据（P5 测试卫生：R3-A 修复 + R2A 口径收紧）

- **起点**：`f524ee188faaea526098b5288bec1c5d882d5589`（本树 clean 后开工；MA 已把 f524
  cherry-pick 到隔离集成 `45a8acbb`）。
- **输入（只读）**：复核树 round3 报告
  `PAN_TERMINAL_IPC_SECURITY_REVIEW_20261003_ROUND3.md`（HEAD `aeb523b1`，证据 `cfc825f5`）。
- **写范围**：`tests/test_terminal_runner_ipc.py`、IPC 接口文档、`audit/terminal/implementation/ipc/**`
  的 r4 工具与证据。**生产 `packages/` 零改动；未改 secret_store 测试；未覆盖 r2/r3 旧
  JSON/日志；未碰其它树**（`git diff -- packages/` 为空可核）。
- 本任务是**非阻塞 P5 测试卫生**，不重开已接受的生产安全子集（F1–F12 仍属 `e018`）。

## 1. R3-A：child-report 写协议（先失败 → 后修）

### 1.1 失败阶段（确定性）

`audit/terminal/implementation/ipc/r4_repro_r3a_race.py`（真实文件 + 真实读句柄）：

| 形态 | 结果 | 证据 |
| --- | --- | --- |
| **旧单发协议**（写 tmp → 一次 `os.replace`）在**持读句柄**时 | **FAILED**：`PermissionError(WinError 5)` | `pre_fix/r3a_legacy_race.{json,txt}` |
| 释放句柄后再试（同一协议） | OK —— 失败纯由并发读句柄引起 | 同上 |
| 新协议（有界重试）在持读句柄时 | **attempts=5 后成功**，内容一致 | `post_fix/r3a_protocol.{json,txt}` |
| 新协议 + 注入持续失败 | **显式抛 `ReportWriteError`**（"after 3 attempts"，不静默、不吞） | 同上 |

机制（round3 §3 已坐实）：父子共享 `data/secrets`，父进程 `Path.read_text()` 的读句柄不含
`FILE_SHARE_DELETE`，与子进程 `flush()` 的 tmp+replace 在 20ms 轮询窗口重叠即 WinError 5；
旧代码把 `reason=fatal` 写入报告或直接崩掉，"重试"并不存在。

### 1.2 修复（仅测试；同源实现，测的就是子进程用的代码）

- 新增 `write_report_payload(path, payload, *, attempts=40, retry_seconds=0.05)`：
  tmp + `os.replace` 的**有界重试**；**耗尽抛 `ReportWriteError`**（带尝试次数与最后一次错误），
  只做“清理自己的 tmp”这一尽力动作，错误绝不吞；返回**实际尝试次数**。
- `CHILD_SOURCE.flush()` 改为调用同一实现（`sys.path` 加 `tests/` 后 import），并把
  `replace_attempts` 回填进报告 —— 两个 child-report 用例断言 `≥1`，**证明子进程走该协议**。
- 子进程 `main()` 的致命路径：`flush()` 也失败时**两个错误都不吞**（`raise flush_exc from exc`）。

### 1.3 新增确定性回归（3 项）

| 用例 | 断言 |
| --- | --- |
| `test_r3a_report_write_retries_bounded_then_succeeds` | 注入前 3 次共享冲突 → 第 4 次成功；`attempts==4`；无 tmp 残留 |
| `test_r3a_report_write_exhaustion_fails_explicitly` | 注入持续失败 + `attempts=3` → `ReportWriteError`；**不留目标文件/tmp** |
| `test_r3a_report_write_with_real_reader_handle` | 真实读句柄下旧单发协议**必失败**（R3-A 现场）；新协议重试成功且 `attempts≥2` |

## 2. R2A 见证口径收紧（每案例增量）

- 两次身份拒绝改为**每案例增量** `Δ=(accepted+transient) - before ≥ 1`（不再只做累计 ≥2）；
  每次拒绝仍有：客户端 `send_frame` 计数不变、`credential_bytes()==0`、`accept_errors==0`。
- `_SendFrameCounter` 注明**有效范围**：类级计数只在“窗口内无其它发送者”时支撑“零帧发送”；
  对照段结论由**字节级 hello 锚点**给出（`b"hello" in hello_bytes`），不依赖计数方向性。

## 3. r3 post_fix 快照边界（澄清，不补造历史）

`r3_freeze.json` 中 23 条 `post_fix` 引用（exists/sha256/summary）是**采集时实现树本地日志的
快照**；这些 `*.log` **未入库**（仓库 `.gitignore:77`），且重生成日志的哈希必然不同
（时间/时序）。**不得**据 `exists=true` 把它们当成已提交内容，也**不补造**历史日志；
本轮新复跑日志一律写 `evidence/r4/**` 的 UTF-8 `.txt`，r2/r3 旧 JSON 与日志未覆盖。

## 4. 运行矩阵（一次批量收尾，不做 10 轮重复）

| 运行 | 结果 | 证据 |
| --- | --- | --- |
| 直连全量 | **118 passed**（80 原 + 34 r2 + 1 r3 + 3 r4） | `post_fix/direct_full.txt` |
| uv 隔离全量 | **118 passed** | `post_fix/uv_full.txt` |
| 定向 `-k "r3a or impostor"` 直连 | **5 passed** | `post_fix/direct_targeted_r3a_impostor.txt` |
| 定向 `-k "r3a or impostor"` uv | **5 passed** | `post_fix/uv_targeted_r3a_impostor.txt` |

`r4_freeze.json` 固化以上逐运行摘要与日志 sha256，以及 R3-A 先失败后通过、R2A 增量口径、
r3 快照边界澄清、观察项与**未测清单**。

## 5. 观察（非缺陷；生产不改，测试侧消除抢跑）

一次批量运行中出现过 `SecretSecurityError: secret path escapes the secrets directory`
（`_guard_path` 的两次 `resolve()` 在**目录首次创建瞬间**的规范化窗口；生产 fail-closed
语义不变）。同批复跑 6 次与单测隔离均未再复现（**一次观察，不夸大**）。测试侧由
`child_runner.spawn()` **先建好 secrets 目录**消除父子抢跑；生产 3 模块未改动。

## 6. 未测（如实）

1. 跨用户/远程拒绝未实测（沿用 R1/R2 声明）；
2. 瞬时分支的确定性依赖“连接落在首个未 arm 实例”的本机内核行为（不外推）；
3. R2D/R2E 低危非阻塞项按指令不做生产修复；
4. 未做长稳/压测；未重跑全库（按指令）。

## 7. 复跑

```bash
cd D:/project/pan-worktrees/terminal-ipc-implement-20261003
E:/software/miniforge/python.exe audit/terminal/implementation/ipc/r4_repro_r3a_race.py        # 失败阶段 + 新协议
E:/software/miniforge/python.exe audit/terminal/implementation/ipc/collect_r4_evidence.py     # 批量矩阵 + r4_freeze.json
```
