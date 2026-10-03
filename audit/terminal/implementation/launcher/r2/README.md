# Pan Terminal launcher r2 窄修 —— 实施与证据（2026-10-03）

- 树/分支：`D:/project/pan-worktrees/terminal-launcher-implement-20261003` @
  `implement/terminal-launcher-20261003`，起点 `5c44b36c`（r1 交付）。
- 依据：独立审查 `5681ef8e`（**接受**，无必须修复级失败；F1–F8 均为低危建议）
  与 MA 窄修口径：**F2–F7 集中最小修**（状态安全/交付承诺不符）；F1 只补边界；
  F8 保留现状。
- 范围：仅 `launcher.py` / `test_terminal_launcher.py` /
  `PAN_TERMINAL_LAUNCHER_INTERFACES_20261003.md` / 本目录（新增 `r2/`）。
  共享代码、既有测试、其它树、`.workflow` 只读；P2 不接；旧证据不刷新。
- **不撤回**已验证基础功能子集：r1 的 17 项 launcher 与 13 组合相邻回归成果继续有效。

## 1. 审查项处置（逐条）

| # | 审查发现 | 本轮处置 |
| --- | --- | --- |
| **F2** | 收敛用真值判断（`closed="true"` → 假收敛 exit 8） | **修**：两个入口都只认真 bool `True`（正常 close 的 `_reported_closed`、startup owner 的 `outcome["closed"]`）；反向断言真 bool 仍收敛/透传 |
| **F3** | `residual` 原样透传可带入上游自由文本（哨兵进 status） | **修**：launcher 侧 **白名单投影**（不转嫁上游）：`reason`→`reason_class`（四个契约静态值原样/其余归类）、pid/identity_filetime/可信 bool/cleanup_seconds、`cleanup_errors`→计数、`cleanup_detail`/`stderr_digest`→存在性、未知键→数量；owner 报告的 `reason`/`detail` 不再成为 `last_error_type`（改静态 `owner-not-converged` / `invalid-owner-report`） |
| **F4** | `os.replace` 失败后自有 tmp 残留目录 | **修**：tmp `<name>.<pid>.<hex>.tmp` 以 `O_CREAT\|O_EXCL` **独占创建**；`finally` 只清本进程成功创建的那一个；不破坏旧 target、不删他人 tmp；清理失败记 `status-tmp-cleanup-failed` 且不掩盖主失败 |
| **F5** | `status-write-failed` 不进 stderr（文档承诺已不符） | **修**：写失败输出单行静态脱敏公告 `pan-terminal-launcher: status write failed (<TypeName>)`；不抛、不改退出码 |
| **F6** | 非法 id 在 `status-path-rejected` detail 回显 | **修**：detail 只记 `ValueError` 类型名；零派生文件；events/stderr 不回显 id（5 种非法形态门控） |
| **F7** | tmp 名与文档 hex 口径不符 | **修**：实现改为 `<name>.<pid>.<hex>.tmp`（随机 8 位 hex），文档同步 |
| **F1** | `retry_cleanup` 违约阻塞时 launcher 无独立兜底上界 | **仅文档**：§5 写明本层同步直调、有界性是**上游原语契约**、不提供独立兜底上界、非 OS 硬 SLA；**不新增线程封装** |
| **F8** | close worker 晚结果被新 invocation 覆盖（幂等重发） | **保留现状**，§4 写明口径（close 幂等、`max_concurrent=1`、不引入结果缓存） |

可达性口径：F2/F3 涉及的面是**受信同进程来源**（注入的替身 / emulator 内部构造的
residual），本轮按“即使上游被注入或损坏也不外泄文本 / 不伪造收敛”收紧，
**不夸大为远程漏洞**；F4/F5/F6 的触发条件为同用户本地（状态目录不可写、句柄占用、
本地非法 argv）。

## 2. 门控（新增 12 函数 / 29 用例；原有 17 项保留）

新增（走生产 `TerminalLauncher` 对象，仅工厂替身 + 注入 os 层故障）：

- **F2（10）**：正常 close 入口 8 种非真 bool（`"true"/1/"false"/0/[]/{}/None/0.0`）
  不得收敛（exit 6 + `retained` + `close-not-converged`）+ 真 bool 收敛；startup owner
  入口 5 种非真 bool 不得假收敛（`unproven` + 有界重试）+ 真 bool 消费收敛。
- **F3（5）**：residual 白名单投影（多哨兵来源零落盘 + 保留结构化事实）；坏类型保守；
  startup `reason` 与 owner `last_error_type` 分类化；跨来源哨兵（residual/owner/runner/
  close）status+events+stderr 零泄漏。
- **F4/F7（4）**：replace 失败 → 无 tmp 残留 + 旧 target 不变 + 静态公告；他人占用同名
  tmp → 写失败但**不删他人文件**；清理也失败 → 两次事件都在案；两次写 tmp 名唯一。
- **F5（1）**：写失败输出静态脱敏 stderr（无路径）、退出码不变。
- **F6（5）**：5 种非法 id（含含哨兵串）→ detail 仅 `ValueError`、零派生文件、stderr 无 id。

期望修正（2 处，随 F3 语义收紧，**非回归**）：`residual["reason"]` → `reason_class`；
owner 未收敛的 `last_error_type` 由注入 reason 文本改为 `owner-not-converged`。

## 3. 先失败 → 后通过

- **先失败**：`evidence/pre_fix_r2_tests.txt` —— 在 `git archive 5c44b36c` 只读副本上
  （副本 `launcher.py` blob `0cbcaa08…` == 5c 提交 blob，**equality TRUE**，
  见 `evidence/pre_fix_source.txt`）：**20 failed / 9 passed / 17 deselected**。
  通过的 9 项为真 bool 有效路径与巧合 falsy 值——如实记录，未人为制造失败。
- **后通过**：直连 **46 passed / rc 0**（45.19s）、uv 0.9.14 隔离 **46 passed / rc 0**
  （46.45s），均含汇总行。

## 4. 复跑入口

```bash
E:/software/miniforge/python.exe audit/terminal/implementation/launcher/r2/collect_r2_evidence.py
```

只跑 launcher 套件直连 / uv 各一次；**不跑** 13 组合 / core / backend / IPC / emulator
全量 / 全库 / 浏览器 / provider / 长稳（MA 令）。

## 5. 结果与资源

| 项 | 结果 |
| --- | --- |
| r2 直连 / uv | 46/46 rc 0（45.19s / 46.45s） |
| 真机会话清理 | 5×2 会话 `alive_before_cleanup=0`、资源残留 0；非零退出为预期（bootstrap_refused=4、hard_kill=1） |
| 残留扫描 | python launcher/runner 0、node sidecar 0 |
| status tmp 残留 | 证据目录 `*.tmp` = 0（F4 口径） |
| npm ci | 未新增（sidecar 依赖已在本树专属目录，exact pin 6.0.0/0.14.0，未改总锁） |

## 6. 残余与未验证

- 残余（如实）：F8 幂等重发保留（`max_concurrent=1`，close 幂等）；`_safe_residual`
  的分类表依赖 emulator 的四个纯静态 reason 字面量，上游改文案时退化为
  `engine-startup-failed`（安全退化，不泄漏）。
- 未验证（门维持）：P2 服务/Web/MCP/registry、Ctrl-C、真实 durable detach、浏览器/
  provider/账号/跨用户/主机、长稳/背压、POSIX、跨 sidecar 重启恢复。
- 纪律：只对自有进程做同 handle raw FILETIME + Wait 核验；无按名/命令行广杀；未触
  8768 / 账户 / 凭据 / main / practical / `.workflow`；无 stash / reset / push / merge /
  build / restart / 子代理 / 全局记忆写。
