# Pan Terminal launcher 生产宿主 —— 实施与证据（2026-10-03）

- 树/分支：`D:/project/pan-worktrees/terminal-launcher-implement-20261003` @
  `implement/terminal-launcher-20261003`，seed `3f8ef045`。
- 范围：**生产 launcher**（独立 runner 进程内拥有真实 HeadlessEmulator，借入
  TerminalRunner；引擎归属 / 启动失败清理 / 收尾 owner 保留重试闭环）。
  P2 服务 / Web / MCP / registry 不在本轮。
- 唯一可写：新增 `packages/core/terminal/launcher.py`、`tests/test_terminal_launcher.py`、
  `docs/design/PAN_TERMINAL_LAUNCHER_INTERFACES_20261003.md`、本目录。runner / client /
  contracts / backend / IPC / emulator / sidecar / registry / `__init__` / server 与既有
  13 组合测试**只读**（生产签名零改动）。

## 0. 前置缺口（本轮的对照事实，不倒写历史）

1. 组合 R2 接受记录“三层收尾事实”：引擎对象可重试已验证；**测试宿主只调用一次
   close、失败 owner 消费未闭环**；生产 launcher 未实现/未批准。
2. 独立审查 `326a646e` MA 静态注意 1/2：内联测试 launcher 收尾段无重试/消费链；
   `close(0)→close(15)` 用例只证明**引擎对象语义**（无 runner/launcher 代码引用）。
3. 本轮以此为目标实现生产 launcher，并用**走生产 launcher 对象/入口**的门控把
   “owner 实际消费 / 收尾重试 / 两结局状态”钉死。launcher 为新增模块，无“修复前
   运行”可做；缺口以 1/2 两项已接受记录为锚，不另造基线。

## 1. 交付

- `packages/core/terminal/launcher.py`：`TerminalLauncher`（run / engine_cleanup /
  startup_report / events）+ `main()`；入口
  `python -m packages.core.terminal.launcher --terminal-id … --secret-file …`。
  退出码：0 透传 / 2 usage / 3/4/6/7 透传 / **5 internal / 6 cleanup-unproven /
  **8 engine-startup-failed（owner 已消费收敛）**。预算：总 20s（含等待）、单次
  close 8s、重试间隔 0.5s；单飞 worker 不叠加；失败保引用。
- `tests/test_terminal_launcher.py`：12 项注入门控 + 5 项真实跨进程（见 §3）。
- 接口文档：`docs/design/PAN_TERMINAL_LAUNCHER_INTERFACES_20261003.md`。

## 2. 口径（与冻结文档对齐）

- 引擎归属（边界 I1）：TerminalRunner 不关闭借入对象；宿主 finally 有界收尾、
  保留失败 owner、记录重试；Pan 服务只是 IPC 控制者。
- 两结局显式：确证收敛 → 透传 runner 码 + `phase=finished`；未收敛 → 6 +
  状态文件（真实 pid/raw FILETIME 字符串、保留资源、失败原因类型、尝试次数、
  in-flight 事实）；**对象引用不可跨进程重试**；Job 内核退出不算清理已证明。
- 脱敏：状态/公告只含类型名与静态串；token 哨兵门控钉住。
- 非承诺：write budget 非硬 SLA；partial 常态；F5 无历史世代原子绑定；
  硬死 Job 兜底仅已测布局；Ctrl-C / 真实 durable detach / 浏览器 / 长稳 /
  跨机 / POSIX 未验收。

## 3. 门控清单（17 项）

注入门控（走生产 `TerminalLauncher` 对象；仅 emulator/runner 工厂替身）：

| # | 用例 | 断言焦点 |
| --- | --- | --- |
| 1 | pre-spawn owner=None | exit 8；不伪造消费；status `owner=none` |
| 2 | ready 前 EOF → owner 消费回升 | owner.calls=2；exit 8；consumed |
| 3 | fatal 帧 owner 不收敛 | 有界（< 总预算+ε）；exit 6；`unproven` |
| 4 | 握手 timeout | 与 EOF 同契约（owner 消费） |
| 5 | runner 构造抛错 | 引擎仍收尾；exit 5；runner_error=类型名 |
| 6 | runner.run 抛错（token 哨兵） | status/stderr 零哨兵；引擎收尾 |
| 7 | close false→重试成功 | attempts=2；max_concurrent=1；透传 0 |
| 8 | close 抛异常→重试成功 | 类型名记录；不泄漏异常文本 |
| 9 | close 永久阻塞 | 有界非零（6）；in_flight；不叠加（max=1） |
| 10 | 预算含等待 + 之后同 owner 重试 | elapsed∈[预算, 预算+ε]；释放后收敛 |
| 11 | status 写失败 | 不抛、不假成功（收敛 0 / 未收敛 6 不变） |
| 12 | 非法 id（含失败路径） | status-path-rejected；**零派生文件** |

真实跨进程（生产入口 `-m packages.core.terminal.launcher`，真实 ConPTY / IPC /
HeadlessEmulator；sidecar 依赖仅专属目录 `npm ci`）：

| # | 用例 | 断言焦点 |
| --- | --- | --- |
| 13 | bootstrap + 心跳 + 协议 A + stop | 自证 pid==进程 pid；F5 bool-or-null；独立心率低延迟；exit 0；status 身份 == bootstrap 自证；shell+sidecar 消亡 |
| 14 | lease 丢失自停 | 无心跳 → lease-expired → exit 0；资源无残留 |
| 15 | bootstrap 失败收尾 | 未首绑 → exit 4；**引擎仍被收尾**（converged） |
| 16 | 硬死布局 | 同 handle 核验终止 → shell+sidecar 消亡；退出非零（不泛化） |
| 17 | ambient detach 拒绝 | detach-refused、零状态变化、shell 同 PID 存活 |

## 4. 复跑入口

```bash
E:/software/miniforge/python.exe audit/terminal/implementation/launcher/collect_launcher_evidence.py
```

跑 launcher 套件（直连 / uv）与 13 组合相邻回归（直连 / uv）各一次；产物见本目录
`pytest_*.txt`、`evidence_*/`、`summary.json`、`uv_env.json`、`pins.json`、
`source_blobs.json`。不跑 50/48/core/全库/10 轮稳定性。

## 5. 纪律

- sidecar 依赖仅 `packages/core/terminal/emulator_sidecar/` 内 `npm ci`（exact pin
  6.0.0/0.14.0；未改总锁/全局环境）。
- 清理只对**自有**资源做同 handle 身份核验（raw FILETIME + Wait 消亡），不按名广杀；
  只读进程扫描仅用于“残留=0”事实记录。
- 无 stash/reset/广杀/子代理/全局记忆写/主线 push-build-restart；未触既有服务 8768/
  账户/凭据/main/practical/.workflow。

## 6. 结果（2026-10-03 最终证据跑）

| 段 | 结果 | 日志 |
| --- | --- | --- |
| launcher 直连 | **17 passed / rc 0**（39.27s） | `pytest_launcher_direct.txt` |
| launcher uv 0.9.14 隔离 | **17 passed / rc 0**（39.25s） | `pytest_launcher_uv.txt` |
| 13 组合相邻回归（直连） | **13 passed / rc 0**（59.64s） | `pytest_composition_direct.txt` |
| 13 组合相邻回归（uv） | **13 passed / rc 0**（59.98s） | `pytest_composition_uv.txt` |

- 清理：真机会话 5×2，`alive_before_cleanup=0`、资源残留 0；非零退出会话均为**预期**
  （bootstrap_refused=4、hard_kill=1）；残留扫描（python runner/launcher + node
  sidecar）**0**。
- 关键实测（evidence_launcher_direct）：
  - T1：快照 `cursor=366==total`、`partial/partial`、F5 `cursors_valid=true /
    reset_unconfirmed=false`（bool）；独立心跳 8 次最大 0.062s；exit 0；status 身份与
    bootstrap 自证交叉核验一致；shell+sidecar 消亡。
  - T2：lease 丢失自停 exit 0、engine cleanup converged、无残留。
  - T3：bootstrap 失败 exit 4、engine cleanup converged（attempts=1、0.015s）。
  - T4：硬死 `filetimeMatch=true` 核验终止、shell+sidecar 消亡、退出码 1。
  - T5：ambient `durability.capable=false` → `detach-refused` 零状态变化。
- uv 环境口径（实测发现并如实记录）：uv venv 的 python 为 **shim**（Popen 句柄 pid ≠
  真解释器 pid）——身份权威 = **bootstrap 自证**并与 `launcher-status.launcher_identity`
  交叉核验；sidecar 归属按“真解释器 pid 的直系子进程”核对（证据 `process_pids`）。
  测试修正：sidecar 扫描从 bootstrap 关键窗口移出（延迟不再影响 lease 建立）。
- 先失败/期望修正记录：launcher 为新增模块，无“修复前运行”；前置缺口以组合 R2
  “三层收尾事实”与 `326a646e` 静态注意 1/2 为锚（§0）。uv shim 与扫描时序两处为
  测试自身修正（非产品缺陷），修正前后差异见 git 历史与本文记录。
