# Legal Offline / Illegal Lock 实施交付（待 MA 独立验收）

日期：2026-10-18。工作树 `D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`，分支 `review/legal-offline-illegal-lock-20261018`。

用户在只读审阅结束后明确扩大授权：在同树继续技术修订、最小实现、迁移、测试并提交，交 MA 独立验收和集成准备。本次未合 main/practical、未 push/release、未重启现有 Pan、未使用 8767/8768 测试。

## 提交与集成准备

- 基线：`5d20e72bf44f8cdb46bf8629f4772fb3b393863a`。
- 初始审阅：`1e50493441202e7db0e17794eaca1b46c12a7753`，完整初审结果见同目录 `legal-offline-illegal-lock-review-20261018.md`。
- 实现、迁移及验证证据提交：`2d9c95fa2372308b40ba9f05ae1cdd8475c32acd`。
- 本报告所在的后续交付提交仅整理文档/证据：将技术修订版改为独立文件名，更新 manifest 中对应路径，规范 build 文本日志尾部空白，记录最终结论；不改变已验证的产品或测试源码。
- 技术修订版：`docs/design/PAN_LEGAL_OFFLINE_ILLEGAL_LOCK_AGENT_TASK_REVISED_20261018.md`。Pan-main 原任务是未跟踪用户文件；保留原稿路径，分支采用独立修订文件名，避免未来 fast-forward 覆盖或被该未跟踪文件阻挡。
- 核验时 canonical `main` 和 `practical` 均仍为上述基线，当前拓扑可在 MA 验收后整批集成并准备 practical 快进；本次没有执行这些动作。若 MA 验收时 main 已变化，应重新检查差异与必要回归。
- 源码/测试及修订文档逐文件 SHA256：`audit/legal-offline-20261018/source-manifest.json`。产品/测试文件字节与最终验证一致；修订文档改名不改变内容。

## 实现后的行为

1. **安全执行终结。** 新增独立持久化 `legal_offline`、`offline_run`、`illegal_batches`。每次受保护执行在 provider 交接前写 False + run UUID；正常完成、正常捕获 Error/429 的安全终态同 history/result 提交 True。合法 Idle 允许常驻 CLI 存活；随后空闲 CLI 死亡不据此产生 Illegal。
2. **稳定身份。** run UUID 跨 Pan 重启保留；Worker owner token/generation 防止可复用 workerId、同对象 Restart 与迟到回调误更新。实际 writer ticket 内再次验证身份。终态去重仍不重发 result，但可安全结束当前幂等执行，避免 Idle 留下 False。
3. **异常快照。** 在所属 Session 的 queue exclusion 下先完成失主交接对账，再按现有 Lock All 的 queued 范围，仅给尚未有效锁定的消息加 Illegal。失败 run、批次、精确新增成员、pending/可恢复 ledger、queue revision 同次 Session JSON 原子替换。重复检测、重启、空批次均不重新形成快照，不锁新 D，不重锁手动释放项。
4. **一个可撤销锁。** `queueLockSource` 是 manual/illegal 的互斥来源，`queueLockIllegalBatch` 仅控制自动解除资格。手动 Lock/Lock All 可以取得 Manual 归属；Unlock/Unlock All 清除任一来源。保留旧 API 字段和范围；增加来源元信息，未把来源做成双层 OR 锁。
5. **保留既有安全条件。** Stop reports 的锁是带报告策略归属的 Manual；用户显式 Lock 后，Resume reports 不清除用户锁。Steer/self-consume unknown、force hold、编辑 lease、Pause、native ownership 不因消息 Unlock 而被绕过。
6. **继任恢复。** 成功建立的新 Worker Idle 在 consumer 开始前恢复本 Session 仍未恢复的前序批次，仅条件解除仍属于原 batch 的 Illegal。Manual、删除或已手动解锁的项跳过。允许为 Illegal-only 积压建立继任；Manual-only 保持原恢复约束。Pause 仍禁止交接；无历史 Illegal 对未来消息的持续门禁。
7. **正常收尾。** Stop/Restart/takeover 在物理退休与必要保存后结束执行。watchdog 异常中止不因复用 kill helper 而变合法。EOF 后先退休 tracked runtime，再允许继任。安全 Idle 的旧 CLI 若跨服务仍活或 owner 身份未知，保持合法，同时拒绝第二 writer。
8. **迁移。** 缺失/损坏的旧合法性和无稳定 run 身份的 False 不猜成 Illegal，不复制旧 legal-state。旧 manual/auto-report/布尔消息锁归一为 Manual，报告策略归属保留；重复加载不覆盖新 batch 或用户操作。旧 legal ledger 继续承载既有恢复意图/Details 功能，不再作为新 Illegal 判据。
9. **可见状态。** HTTP list/detail、WS Session 摘要增加 `legalOffline`、`illegalOffline`。Illegal 筛选和 WorkerDot 使用服务端事实，不再比较旧 legal-running。合法性随 Session 摘要版本更新，独立于可能缺失的 Worker execution cursor；真实 Chromium 已验证无需刷新即可变红与恢复。

主要实现入口：`packages/core/legal_offline.py`、`packages/core/queue_locks.py`、`packages/core/worker.py` 的 `_commit_offline/_prepare_offline_recovery/_begin_offline_run/_activate_offline_worker`，以及 `packages/core/session.py` 的 `offline_override` 保存/发布边界。

## 最终验证与证据

下表均为**实测**。没有以静态检查代替浏览器或进程行为。

| 检查 | 结果 | exit / skip | 证据 |
| --- | --- | --- | --- |
| 30 文件后端回归 | **509 passed**，47.86s，3 条现有库警告 | 0 / 0 | `audit/legal-offline-20261018/backend.txt`、`backend.xml` |
| 其中新执行合法性/故障/并发用例 | **32 passed**，包含于上述 509 | 0 / 0 | `tests/test_legal_offline.py`、backend JUnit |
| 真实隔离 HTTP/进程/Chromium 生命周期 | **10 passed**，31.92s | 0 / 0 | `real-lifecycle.txt`、`real-lifecycle.xml` |
| 前端 4 文件回归 | **65 passed**，4 files passed | 0 / 0 | `frontend.txt`、`frontend.xml` |
| `pnpm build` | TypeScript + Vite 成功，校验 **70** 个预压缩资产 | 0 / 不适用 | `build.txt` |
| 实现提交 pre-commit | `React frontend OK`，TypeScript 通过 | 0 / 不适用 | 提交命令输出 |

合计上述互不重叠的测试集合 **584 passed，0 failed，0 error，0 skipped**。`results.json` 记录机器可读汇总；JUnit 用例时间之和与命令墙钟耗时不是同一指标。

后端覆盖：安全 stream Idle/429、False 失败零 stdin 写入、原子 replace 失败回滚、提交中的 API 不可见性、已提交/未提交取消、前后 writer 顺序、旧 owner/generation、PID/create-time 复用、活的合法 Idle owner、重复/空异常事件、多批次继任、ledger 恢复、手动转换与 Unlock、并发入队+解锁+Manual claim、编辑 lease、旧锁/合法性迁移、终态幂等去重、启动偏好、正常服务退出/Restart、现有 native/Steer/queue/报告回归。

真实隔离服务覆盖：

- Running 中外部杀掉自有 provider，持久异常快照仅含 B/C；手动释放 B、把 C 转 Manual、加入 D 后重启，批次不扩大；新一代 Idle 恢复后 B/D 按原队列交接，C 保持 Manual。
- 正常任务和模拟 429 捕获错误安全完成后，CLI 仍存活；再杀空闲 CLI、重启服务，不产生 Illegal 或异常锁。
- 强杀自有 Pan 服务，不执行安全 lifespan；旧执行 False 被保留。旧 provider 退出后启动，先建立一次稳定异常批次；再次重启仍不扩大，显式新 Worker Idle 恢复，Pause 继续禁止交接。
- delayed restart 与锁队列、插件配置的五种既有组合，保持终态先于替换、无提前消费与重复 spawn。
- Chromium **153.0.8010.12** 连接真实隔离服务，收到真实 WS 后显示红点，再在继任 Idle 后变绿，**未刷新页面**。截图 `browser-illegal.png`、`browser-recovered.png`；实例信息见 `browser-evidence.json`。

全部服务使用 `tmp_path` 数据根、动态 loopback 端口，本轮端口均非 8767/8768；`real-lifecycle.txt` 记录各实例及清理后的端口释放。provider 是本地确定性 fake CLI，不调用真实 CBC/Codex 模型或外部 API。测试 harness 额外把 startup_recovery 记录放入其临时数据根。

前端 build 保留已有 router 动静态 import 和 chunk size 警告；后端保留 Pydantic 未解析 annotation、Starlette/httpx、anyio 弃用警告。没有新增 skip、xfail、禁用断言或关闭类型检查。修复了 native 创建测试绕过 lifespan 却依赖全局 manifest 的 fixture：显式空 catalog，原能力与参数断言保留。队列 API 的精确期望只增加新的来源元信息。

复跑：使用本机验证解释器 `D:/project/Pan-main/.venv/Scripts/python.exe` 和 `scripts/run_tests_isolated.py`；后端文件清单见 `audit/legal-offline-20261018/backend-files.txt`。真实集成入口为 `tests/test_legal_offline_browser.py`、`tests/test_legal_offline_http.py`、`tests/test_delayed_restart_locked_queue_http.py`。前端入口为 WorkerDot、sessionFilters.workspaceSearch、WorkspaceRail.searchFilter、sessionStore.restartIndicator 四个测试，命令使用 `pnpm exec vitest run --no-cache`。

## 依赖与操作边界

- 已按要求先运行 `prepare_validation_env.ps1`。默认 Pan-main 共享依赖缺 xterm，第一次 build 因此失败；三方 package.json/pnpm-lock.yaml/vite.config.ts SHA256 一致后，经脚本撤销本树自有 junction，再只读复用 `D:/project/Pan/packages/web/node_modules` 完整依赖。未 install，未改依赖包或 lockfile；最终 build 成功。准备日志见 `dependency-env.txt`。
- **操作偏差如实记录：**早期 Vitest 默认更新了 Pan-main 共享目录的 `.vite/vitest/results.json` 测试缓存，因此不能声称整个共享目录从未写入。之后所有最终 Vitest 验证使用 `--no-cache`；未删除或猜测还原别人的旧缓存。包内容和依赖声明未更改。
- 单条验证命令预估均小于 3 分钟，实际后端约 48 秒、服务组约 32 秒，未启动额外实现 Session 或真实服务后台 Job。
- 8768 监听仍归 PID **31556**，进程启动于 **2026-10-18 02:28:57**，早于本任务；8767 未启动测试监听。未调用现有服务的管理或消息接口。
- 原任务文件 SHA256 仍为 `C4055322C9162446C833BBDAFE351CEFA6F8FDA88C7E69D94FE6978538C2D6A9`，main 原稿未变。

## 剩余边界与 MA 验收重点

1. **原生客户端事前 admission 尚未改造。** Pan 队列路径在交接前持久 False；原生 CLI 独立发起 turn 则依赖 adapter 的权威 started 通知，在通知到达并落盘前存在窄崩溃窗口。本次不把该事后通知宣称为原生客户端的原子事前准入。要覆盖此窗口，需要后续 adapter/client 握手；已在技术修订版中明确。这是剩余实现边界，不是已验证通过项。
2. 活的/未知旧 owner 会拒绝第二 writer，但没有实现跨 Pan 进程自动接管原生 kernel。真实 native OS 脱离、物理键盘输入及所有后生子进程的完整保活/收容，并未作为本次 fake CLI 服务验证的等价证明。
3. 迁移对无可靠证据的历史数据保持原可消费性，不承诺追溯识别所有历史异常；未迁移实用数据。本次没有全量 pytest、全量前端 suite、远端 CI 或真实模型验收。
4. 快照边界是持有 queue exclusion 的检测/提交边界，不是无法持久还原的物理死亡瞬间。异常批次墓碑目前保留于 Session 元数据，未增加垃圾回收策略；大规模异常历史压缩可以后续单独设计，不能破坏幂等。
5. MA 应独立核对最终分支差异、来源转换、事务失败/取消、继任解锁及上述 native 缺口，再决定是否集成。当前交付状态为**实现已提交，待 MA 验收；未合 main/practical，未发布**。
