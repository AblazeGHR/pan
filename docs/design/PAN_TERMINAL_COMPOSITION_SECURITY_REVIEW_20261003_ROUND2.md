# 组合 r2 独立窄验报告（固定 3a579da6，含 MA 静态注意裁定）

- 日期：2026-10-03。审查者：独立审查 TA（专属树
  `D:/project/pan-worktrees/terminal-composition-r2-review-20261003`，
  branch `review/terminal-composition-r2-20261003`，HEAD==`3a579da6`，clean 预检）。
- 被审对象：`3a579da64c249af35fb20fbfcab6340263d10fa6`（父 `7e94f13a`）——
  "修正后真实组合验收（ROUND2）"：seed `26b7086b`（含已接受 emulator r4（63e 链）与
  Runner F5 最终 `29d535bc`）；首轮 `7e6b30cb` 以 cherry-pick -x 导入为历史（原证据不刷新）。
- 审查输入（只读）：CODEBUDDY、`PAN_TERMINAL_COMPOSITION_BOUNDARIES_20261003.md` 全文（含
  2026-10-03 后续验收状态）、`PAN_TERMINAL_RUNNER_F5_ACCEPTANCE_20261003.md`、ROUND2 报告全文、
  tests 差量（+210/-36，13 项）、r2 收集器/README/source_blobs/pins/日志/summary、
  launcher 收尾段源码、close-budget 用例源码。cabacc7f 首轮结论仅作参考。
- 唯写：`audit/terminal/implementation/composition-review/round2/**` 与本文件；被审
  生产/tests/doc/旧证据只读；旧 runner-review / composition-review / observability-review
  及实现树未动（终态核验）。
- 纪律：唯一真机运行 = 13×2 复跑 + v3 自有会话；sidecar 专属目录 `npm ci`（exact pin、未改 lock、
  git 零变化）；自建资源同 handle raw FILETIME+Wait 核验清理；硬看门狗；未跑旧 50/47/48/core/
  全库/浏览器/provider/账号/真实 durable/长稳；无广杀/stash/reset/子代理/全局记忆写/主线操作。

## 0. 结论：**接受**（基础组合可接受子集维持；无返工项；与生产 launcher/P2 门分开）

**passed（实测）**：

| 组 | 断言 | 结果 |
| --- | --- | --- |
| 复跑 | 13 直连 + 13 uv，均 rc 0 | ✓（59.47s / 59.69s；`13 passed in 58.84s / 58.63s`） |
| F1（v1，真实 node） | spawn 前缺失→owner=None；ready 前 **EOF/fatal/timeout** 三态→**必须抛** + 可重试 owner + retry_cleanup 收敛 + 无残留（timeout 态为本审查补测） | ✓ 4/4 |
| F2（v2，真实引擎探针） | **单一 monotonic**（首次提交→applied==total，不扣 probe）：304,000B/2,252 ops → **0.078s**（r3 旧口径 35.1s 的 ≈450×）；producer ops vs 引擎帧分列（**5 batches**/batched 2,252/`max_batch=65,475 ≤64KiB`）；干净流 full；**OSC → partial 且 note 携带 `reasons=WINDOW_TITLE_OSC_UNVERIFIED`、`diagnostics.reasons=[]`（原因分层实证）** | ✓ 6/6 |
| F5（真机，复跑证据） | 真实会话字段 bool-or-null；reset 未确认窗口短超时快照 `reset_unconfirmed=true`/`cursors_valid=false` → 迟到 ack **一次回填**（reset_count=1）后 `false/true`；node reasons 仅 note 而 python reasons 空不得升级（partial 维持、不放宽白名单） | ✓（我的两份复跑 JSON 与 3a 一致） |
| 驱逐边界（v3，自有会话） | total=378,404 / applied=246 / first_retained=116,357 窗口捕获 → `read(applied)` **显式 gap=[246,116357]**（gap[0]==applied、携带窗口数据）；**fresh-view 不自动 reset（reset_count=0）、PTY 同 PID 存活**；追平后 cursor==total、`read(cursor)` 无 gap | ✓ |
| 心跳/尺寸（v3） | 引擎停滞期间另一连接 5×心跳 **0.0s**；resize(30,100) pty/engine 分列、`resize_wait` 仅引擎确认 | ✓ |
| 收尾/硬死（v3） | 正常结束：exited + `emulator_close{closed,job_verified,graceful,process_exited,guard_closed,reader/stderr/applier_joined,streams_closed}=true`；shell 与 sidecar 均消亡；**硬死布局**：同 handle 终止 runner → shell+sidecar 消亡、launcher 退出码非 0；自建清理**零强杀** | ✓ |
| 证据锚定（v4） | source_blobs **3/3** == 提交内 blob（tests `aa7639cd`、ROUND2 报告 `9db86e13`、r2 README `b50a4c40`）；pins 复算一致（`f811b02…/9c88ee…`、6.0.0/0.14.0、node v24.15.0）；r2 日志入库 13/13；**首轮旧证据零覆盖**（M/D 扫描空）；本 diff 生产零改动；我的 13×2 全绿 | ✓ |

**failed**：无。

**unverified（门维持）**：真实浏览器渲染/fit、provider/账号/网络服务、真实 durable detach、Ctrl-C、
跨用户/主机、长稳/背压、POSIX、跨 sidecar 重启恢复；**生产 launcher 生命周期/失败收尾闭环（未实现
且未批准）**；性能数字仅本机探针（不外推跨机 SLA）；Job guard 内核兜底只按已测布局陈述、不泛化。

## 1. MA 静态注意裁定（先核验后裁定；只报告不改被审文件）

| # | MA 静态注意 | 核验结果 | 裁定 |
| --- | --- | --- | --- |
| 1 | 内联 launcher 在 `runner.run()` try/except 后**只 `emulator.close()` 一次**、随后 `sys.exit(code)`；看不到失败 close 后的有界重试/保留 owner 消费 | **成立**（v4 文本核验：收尾段 `emulator.close(` 出现 **1 次**、无 retry/循环、异常仅记 `{"closed": False, "error": type}`、`sys.exit` 收尾） | 记录为**测试宿主已知边界**；launcher 失败收尾未被消费的缺口属实（与 §4 的"生产 launcher 未实现"一致） |
| 2 | 新增 close(0)→close(15) 用例是**单独 HeadlessEmulator**，不是 launcher 故障路径 | **成立**（v4 核验：该用例自建 emulator、函数体无 runner/client/Session/Popen 引用） | 该用例只证明**引擎对象**的 close 预算与 owner 可重试语义；**不能**代表测试宿主/生产 launcher 消费失败 owner |
| 3 | 报告 §3 若称宿主"失败保 owner 可重试"，需明确"引擎对象可重试 ≠ 测试宿主实际消费失败 owner ≠ 生产 launcher 闭环" | §3 原文：`宿主在 runner.run() 返回后显式 emulator.close()（有界；失败保 owner 可重试；本机实测 closed/job_verified=true）`；同节末句与 §4 已写"生产 launcher 未实现/未批准"及"生产 launcher 归属与失败收尾闭环"未验证 | **表述可接受（不返工）**；建议后续在一句话内显式区分三层（对象语义 / 测试宿主未消费 / 生产 launcher 未闭环）——本轮只报告 |

## 2. 复跑与证据（分列）

| 环境 | 结果 | 证据 |
| --- | --- | --- |
| 直连 | **13 passed / rc 0**（59.47s；`13 passed in 58.84s`） | `round2/logs/direct13.out.txt` |
| uv 隔离 | **13 passed / rc 0**（59.69s；`13 passed in 58.63s`） | `round2/logs/uv13.out.txt` |

13 = 首轮 11（按 r4/F5 修正期望）+ 2 新增（驱逐 gap/fresh-view、引擎宿主收尾预算）；单 `-q` 含汇总行，
与被审双 `-q` 日志互证；未跑 50/47 全量、11/48/core/全库。

## 3. 独立门控（v1–v4；全 rc 0）

- **v1（F1 四态）**：pre-spawn owner=None；EOF/fatal/**timeout** 均"抛错 + owner + retry 收敛 + 无残留"。
- **v2（F2）**：见 §0 表；单 monotonic 0.078s 为本审查独立量化（批合并 5 帧/65,475B 上限内）；
  ⚠ 本审查工具自身两处修正如实记录：v2 初版漏记 note（已补）、v3 初版 `process_dead` UNKNOWN 分支缺
  OpenProcess 兜底导致"已死误判为活"假阴性（已修，修后 shell/sidecar 消亡与组合套件口径一致）——
  均为**审查工具问题**，非产品缺陷。
- **v3（真机会话）**：驱逐 gap/fresh-view/追平重取/resize/心跳/收尾/硬死——见 §0 表。
- **v4（口径与审计）**：见 §1 与 §0 证据行。

## 4. 实测 / 推断 / 未测（失败分列）

- **实测**：13×2；v1（真实 node 进程四态）；v2（真实引擎）；v3（真实 ConPTY+IPC+引擎会话，含硬死）；
  v4（git/文件/文本）。**failures：无**（v3/v4 早前 false 为本审查工具 bug，修正后全绿，见 §3）。
- **推断**：本机探针数字与"批合并使字节吞吐提升"（不外推跨机 SLA）；launcher 正常路径的收尾行为。
- **未测**：§0 unverified 清单；生产 launcher 失败收尾（因未实现）。

## 5. 资源与纪律（实测）

- npm ci：2 packages/0 vulnerabilities；git 零变化（node_modules gitignored）；lock 未改（pins 复算一致）。
- 残留扫描 0；自建临时目录已清理；自建清理仅同 handle raw FILETIME+Wait（零强杀）；
  未触既有服务 8768/账户/凭据/main/practical/.workflow；无广杀/stash/reset/子代理/全局记忆写/
  push/build/restart；不追在途代码。
