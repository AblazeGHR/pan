# Pan Terminal 修正后真实组合验收（ROUND2）

- 日期：2026-10-03。工作树：`D:/project/pan-worktrees/terminal-composition-r2-20261003`
  （branch `audit/terminal-composition-r2-20261003`，seed `26b7086b`——含 emulator r4 F1/F2 与
  Runner F5 最终 `29d535bc` 的隔离集成）。
- 首轮历史：`7e6b30cb`（本树以 `cherry-pick -x` 导入测试/报告/证据；**原首轮证据不刷新**），
  独立审查 `cabacc7f`；边界权威 `PAN_TERMINAL_COMPOSITION_BOUNDARIES_20261003.md`（含后续
  验收状态）与 `PAN_TERMINAL_RUNNER_F5_ACCEPTANCE_20261003.md`。
- 性质：**修正后的真实验收**（tests/doc/audit 新增与修正）；未接 P2、**未实现生产 launcher**；
  唯一运行面为**测试宿主**（明确标注）。生产/共享契约/服务/Web/MCP/registry/总依赖零改动；
  现有发现如需改生产 → 报告不修。
- 诚实声明：headless↔headless，不声明浏览器渲染；同机探针不外推跨机硬 SLA；Job guard
  内核兜底只按已测布局陈述、不泛化。

## 0. 结论（passed / failed / unverified）

- **passed**：13 项真机组合（直连 13/13 rc 0、uv 隔离 13/13 rc 0；清理 11 会话
  0 存活/0 强杀/残留 0）。
- **failed**：无。
- **unverified**：真实浏览器渲染/fit、provider/账号/网络服务、真实 durable detach、Ctrl-C、
  跨用户/主机、长稳/背压、POSIX、跨 sidecar 重启恢复；生产 launcher 生命周期闭环（未实现）。

## 1. 结果（直连与 uv 分列）

| 环境 | 结果 | 证据 |
| --- | --- | --- |
| 直连 miniforge | **13 passed / rc 0**（59.5 s） | `audit/terminal/implementation/composition/r2/pytest_direct.txt` + `evidence/direct/`（12 用例 JSON + 11 清理轨迹） |
| uv 0.9.14 隔离 | **13 passed / rc 0**（59.7 s） | `r2/pytest_uv.txt` + `evidence/uv/` |
| 资源清理 | 11 会话：**0 存活 / 0 强杀**；残留扫描（python runner + node sidecar）**0** | `r2/summary.json` |

13 = 首轮 11（按 r4/F5 修正期望）+ 2 新增（驱逐 gap/fresh-view、引擎宿主收尾预算）。

## 2. 相对首轮的必要修正（均为真实证据）

| # | 修正 | 实测（本机，80×24+scrollback=1000 默认） |
| --- | --- | --- |
| **F1** | `ready 前 EOF` → **硬断言** `EmulatorStartupError` + owner + `retry_cleanup` 收敛 + 无残留（emulator r4 修复闭环） | `compose_startup_failure_owner.json`：pre-spawn owner=None；**post-spawn EOF** error=`EmulatorStartupError`、owner=true、retry `closed=true`、无残留；fatal 路径 0.093 s 同断言 |
| **F2** | 容量证据=**单一 monotonic**（首次提交 → `applied==total`，不扣 probe 窗口）；producer ops 与引擎帧分列 | `compose_browserless_protocol_a.json`：`catch_up_seconds=0.234`；`producer_feed_ops=2314` → `engine_feed_batches=11`、`feed_batched_ops=2312`、`max_feed_batch_bytes=48,399`（≈64 KiB 批上限内） |
| **F5** | 真实 `engine↔runner↔IPC` 机器字段：bool-or-null；reset 未确认/stale；原因分层 | 真实会话：`cursors_valid=true`、`reset_unconfirmed=false`（bool）；`note` 含 node reasons 而 **`diagnostics.reasons=[]`** → `recovery=partial`（**不升级**）。stub 迟到 ack 真路径：未确认窗口内短超时快照 `reset_unconfirmed=true`、`cursors_valid=false`（引擎属性自身语义）、recovery=degraded；迟到 ack 回填后 `reset_unconfirmed=false`、`cursors_valid=true`（baseline==applied）、recovery=partial |
| **协议 A 驱逐边界** | applied < first_retained 窗口 → `read(applied)` **显式 gap**；fresh-view 不自动 reset、不杀 PTY；追平后重取 | `compose_r2_eviction_gap_fresh_view.json`：`total=359,359 / applied=239 / first_retained=97,264` → gap 返回且起点==applied；`reset_count=0`（无自动 reset）、PTY 同 PID 存活；追平后 `read(cursor)` 无 gap 且空 |
| **宿主收尾** | 测试宿主在 `runner.run()` 返回后**有界 close**；`close(timeout=0)` 不收敛→有界报告→重试收敛 | `compose_r2_engine_close_budget.json`：`close(0)`→`closed=false`；有界重试 `closed=true`（幂等）；正常结束 `emulator_close.closed/job_verified=true`；硬死布局：shell+sidecar 均消亡（**内核兜底不泛化**） |
| **保留** | 独立连接心跳 5×0.0 s 不被慢 snapshot 拖慢；resize PTY/引擎分列且 `resize_wait` 仅引擎；断连 0.6 s 后同 PID 存活；write 3×96 KB 期间心跳 0.0 s；detach 受 ambient 拒绝零变化（不逃脱）；真实会话 partial 为常态（**不放宽白名单**） | 各用例 JSON |

## 3. 装配与宿主口径（不称生产 launcher）

- 测试宿主 launcher（本文件同目录测试代码内联）：同进程组装真实 ConPtyBackend + `TerminalRunner`
  + 真实命名管道/HMAC IPC + 真实 `HeadlessEmulator`（exact pin，`npm ci` 仅 sidecar 目录、
  未改 lock）；`backend.probe` 未注入。控制通道仅测试用（访问引擎确认面）。
- **宿主生命周期**：宿主在 `runner.run()` 返回后显式 `emulator.close()`（有界；失败保 owner
  可重试；本机实测 `closed/job_verified=true`）；硬死由 emulator 自持 Job guard 的内核兜底
  覆盖（仅已测布局，不泛化）。**生产 launcher 未实现/未批准**，本报告不称服务接入完成。

## 4. 未验证（门维持）

真实浏览器渲染/前端 fit、provider/账号/网络服务、真实 durable detach、Ctrl-C 产品能力、
跨用户/主机、长稳/背压（>4 MiB 自然突发、慢客户端 ack）、POSIX 宿主、跨 sidecar 重启恢复、
生产 launcher 归属与失败收尾闭环；本轮未重复 50/47/48/core/全库/浏览器/长稳，未跑 P2。

## 5. 纪律与证据锚定

- 只跑本组合套件（直连/uv 各一次）；首轮证据零覆盖；新证据 UTF-8 `.txt` + JSON +
  `source_blobs.json`（暂存 blob id）与 `sidecar_pins.json`（exact pin）锚定。
- 清理仅自有 retained handle + raw FILETIME + Wait；硬看门狗；无广杀/stash/reset/全局记忆/
  push/build/restart/子代理；未触其它树（a48/29d535bc/7e/各 review 树保持冻结）。
