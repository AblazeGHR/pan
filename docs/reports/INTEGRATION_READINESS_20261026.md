# 集成就绪只读 QA（2026-10-26）

结论：**否，当前树不达到“实现整个设计、可合入 main/practical”的门槛。**

评估对象为 `qa/integration-readiness-20261025` 的产品源码基线 `43d0977f0c783f0bd69da5c4611d6bdfe8236a67`；本提交只包含报告和脱敏证据。对照为本地干净 detached worktree `7f303faa355bfc763f0452ad68d31b920e4dc6e1`。该 SHA 的远端 CI 全绿是用户提供的背景，本次没有查询或重跑远端 CI。

## 放行阻塞

1. 新模型定向契约通过并不等于既有全栈回归通过。后端发现旧授权/Automation 契约回归，并有 `kick()` 与 `close()` 的确定性锁等待；原始全量命令在默认 300 秒超时后退出 1，未完成测试集。
2. [最终设计](../design/job-unification/JOB_FINAL_MODEL_20261025.md) §6–7 要求零旧写入口、零可执行旧 owner 和零 legacy 执行代码。R4/R5/R6 的删除出口仍大量存在，R3 也不能因路径翻转标为关闭。
3. 相对当前 main，两侧各有 37 个独有提交，merge-base 为 `cc5b616ecfa32077e177d9d97699dc7063ccaf4b`。只读 `git merge-tree --write-tree 7f303faa 43d0977f` 退出 1，存在 3 个内容冲突：`packages/web/src/services/api.ts`、`packages/web/src/stores/automationStore.ts`、`packages/web/src/views/AutomationView.tsx`。本次未生成工作区合并、更未验证冲突解决后的组合树。
4. 完整前端 Vitest 有两项既有失败；构建通过不等于测试通过。三平台 CI、真实数据迁移、部署和人工验收没有本轮证据。

## 执行边界与复现

未修改产品代码、测试、配置或锁文件，未 push，未推进 main/practical，未操作用户服务或 8767/8768，未使用真实 profile。HTTP/stdio/浏览器相关测试仅执行仓库自有临时根、随机端口、fakeCLI harness，由测试 fixture 负责启动与清理；这不构成部署或生产服务验收。

Python 使用 `D:/project/Pan-main/.venv/Scripts/python.exe`（3.14.5），后端所有运行均经 `scripts/run_tests_isolated.py` 清除继承的 PAN 设置，并由公共 fixture 隔离存储。前端先只读复用 Pan-main 依赖；该安装缺 `.bin` 及 jsdom 的 `@asamuzakjp/css-color`。随后两棵树一致切换到现有 `D:/project/Pan/packages/web/node_modules`，只读复用同版本依赖，没有安装/升级共享包。Vitest 2.1.9、Vite 6.4.3、TypeScript 5.9.3、React 19.2.8、jsdom 25.0.1；使用 `--no-cache --maxWorkers=2 --minWorkers=1`，不改变测试断言。

完整命令、退出码、逐测试失败身份及 skip 原因见 [脱敏结果](../../evidence/integration-readiness-20261026/results-summary.json) 和执行清单。完整原始日志/JUnit 仅在本机隔离证据目录保留，不提交测试 token、临时 profile 内容或完整 traceback。

## 测试数字

| 检查 | 集成树 `43d0977f` | 干净 main `7f303faa` | 结论 |
|---|---|---|---|
| 后端收集 | 6500，exit 0 | 6056，exit 0 | 无收集错误 |
| 原始完整 `tests/` | 默认 300 秒超时，exit 1；无完整 JUnit | 5901 passed / 44 failed / 111 skipped，exit 1 | 候选原始全量未完成 |
| 候选其余 6492 项 | 6189 passed / 192 failed / 111 skipped，exit 1 | 已含于完整基线 | 排除并发文件后跑完 |
| 并发文件 8 项逐项补跑 | 7 failed + 1 timed out；各 exit 1 | 8 passed；与授权组合共 71 passed，exit 0 | 集成新增回归 |
| 后端完整身份合计 | **6189 passed / 199 failed / 111 skipped / 1 timed out = 6500** | **5901 / 44 / 111 = 6056** | 分批覆盖无遗漏/重复；不代表连续全量完成 |
| 全量 Vitest | **2570 passed / 2 failed / 3 skipped**，215 文件，exit 1 | **2564 passed / 2 failed / 3 skipped**，215 文件，exit 1 | 两项失败同身份、同原因 |
| `tsc -b` | exit 0 | exit 0 | 通过 |
| `vite build` | exit 0 | exit 0 | 通过；保留 chunk 提示 |
| 压缩资产解压比对 | **92** 个，exit 0 | **90** 个，exit 0 | 通过，见 [资产 SHA256](../../evidence/integration-readiness-20261026/asset-sha256.json) |
| 独立 path-flip + 全部 final 契约 | 432 passed，exit 0 | main 无新增 final 测试 | 独立通过 |
| 同一 432 项在完整其余批次中 | **424 passed / 8 failed** | 无对应 final 测试 | admission 上下文敏感失败仍阻塞 |

后端 200 个未通过身份（199 failed + 1 timed out）分类如下。全部失败身份、首行原因、基线状态和分类见 [失败清单](../../evidence/integration-readiness-20261026/FAILURES.md)，机器可读明细见 results-summary.json。

| 分类 | 数量 | 归因与处理 |
|---|---:|---|
| 集成新增红项 | **156** | **128** 个同身份在本机完整 main 通过，**28** 个为候选新增测试、基线未收集；包含 1 项锁等待超时。覆盖 43 个模块，包括授权、Automation/Trigger、Event Store、Job chain/template、MCP/Locker/live HTTP、两个 Codex conversion security gate 断言。必须修复或以等价行为证明更新过期测试，保持负控。 |
| 环境缺依赖 | **37** | Terminal sidecar 缺 `@xterm/headless` / `@xterm/addon-serialize`，另缺 Python `winpty`；包含错误 skip 原因与 MCP 包装启动错误。两树均有环境缺口，不据此断言产品退化，也不能视为通过。 |
| 基线已有 | **7** | 6 项 Terminal 子进程/门禁断言在两树同身份同首行原因失败，也受缺 sidecar 前置影响，保守归为基线已有。另 1 项 Web 根路由在完整套件 teardown 因跨 event-loop 的 Worker/queue 清理失败，基线单独复跑通过，属于顺序/资源污染。 |

128 个基线通过的新增红项证明相对本机 `7f303faa` 的观察差异；两树还相差其它 main 提交，不能将每项直接归因到某一个 Job 提交。28 个新增红项中，8 个 admission 用例独立通过而完整上下文失败，必须排查状态/资源隔离及接线。未降低断言或增加 skip。

111 项 skip 的逐项原因保留于 JSON，包括已退役 oneshot、sidecar 缺依赖、POSIX 环境专属、真实 CLI 显式 opt-in、本机 Kimi 测试目录及 gitignored 同步副本等。环境与 opt-in skip 均为未覆盖层，不计成功。

前端两项基线已有失败为 `demo/mockBackend.test.ts` 的 handoff 错误文案不一致，以及 `hooks/reaudit.orderedEvents.test.tsx` 的 Codex delta/final tool envelope 消息结构多字段。完整前端未发现新增失败。初始 jsdom 缺依赖的尝试两树均为 347 passed / 1 skipped / 182 unhandled errors，exit 1；最终用同版本健康依赖重跑获得上述完整数字。

工具目录另核对：36 个 canonical 工具内部无重名，与 92 个 legacy 工具名交集为空，见 [目录检查](../../evidence/integration-readiness-20261026/catalog-audit.json)。结构无冲突不等于完整行为兼容，后者仍受全量红项阻塞。

## 契约、路由与工具

`test_job_path_flip.py` 与全部 11 个 `test_job_final_*.py` 独立执行：**432 passed / 0 failed / 0 skipped，exit 0**。包含 contracts/runtime/admission/creators/executors/independent execution/HTTP/transports/clients/migration/apply。

生产 route snapshot 与固定快照一致：108 条相关路由，method + 归一化参数路径冲突为 0；31 对 canonical/final alias 使用同一 endpoint；具体旧静态子路径优先于 canonical 动态 ID。HTTP/TCP、Cookie Origin/无效 Bearer、操作幂等与原 requestKey 查询、真实 stdio MCP core/native、CLI transport 均在上述测试覆盖范围。

正式资源使用 `/api/jobs`、`/api/triggers`、`/api/event-creators`、`/api/job-templates`、`/api/job-runs`、`/api/operations`，Action Ref 目录为 `/api/jobs/actions`；`/api/final/*` 继续作为 deprecated alias。旧 facade 重挂到 `/api/legacy/jobs`，部分旧具体路径和写方法仍保留。旧 MCP 工具仍在目录，独立测试验证其目录与部分兼容调用；完整旧行为兼容仍受全量红项阻塞。因此，“当前双轨路由无匹配冲突”成立，“旧工具、旧入口已退场”不成立。旧授权测试对 `/api/jobs` 的原有假设已失效，必须修复契约/fixture 或行为后恢复负控，不能删除安全断言掩盖失败。

## R4/R5/R6 与整个设计

| 阶段 | 设计出口 | 当前源码证据与影响 |
|---|---|---|
| R4 | 简单迁移、复杂原状保留且排除执行；旧 owner/可执行注册清零；删除旧 pass/dispatcher/第二账本 | `background_jobs.py` 仍定义 `run_due_message_jobs`、`run_due_scheduled_tasks`、`_fire_scheduled_entry`、`_run_job_action`、`_execute_job_action`、`_redeliver_undelivered`、`create_service_job`、`transition_service_job`。离线 migration/apply 合成测试通过不能证明所有 profile 数量守恒、active legacy=0、oldOwner=0、unknownOwnership=0。当前无真实副本 crash/rollback 或 owner 切换验收，R4 未完成。 |
| R5 | 删除旧读控路由、工具、兼容层与旧 UI；发布代码零旧执行入口 | `packages/jobs/legacy_compat.py`、`packages/scheduler/legacy_bridge.py` 仍存在并被业务使用；`final_api.install()` 有意保留旧 facade 写入口；MCP 仍导出 `agent_background_start`、`agent_message_job_create`、`scheduler_create/run_now`；React `/jobs` 仍是 `JobsView`，含 legacy tab 与 `NewJobForm`，新页面另挂 `/automation/canonical`。属于双轨实现，R5 未完成。 |
| R6 | 删除 owner-switch、迁移 runtime、loader/flags，发布包只含新模型；CI/迁移/部署/人工各自签收 | `scheduler/api.py` 仍有 `/tasks/{task_id}/owner-switch`；`scheduler/store.py` 仍有 `migrate_legacy_tasks`、`_job_from_legacy_task`、`_merge_legacy_runs`。没有最终发布包 denylist/运行时零旧写证明，也没有三平台当前组合树 CI、迁移、部署 readback 和人工签收，R6 未完成。 |

R4–R6 是最终设计的强制完成条件，并非可延后的美化工作。D6 允许复杂旧数据原状保留，但要求不再执行、不自动导入/清理，并有 ID/位置/hash/原因与手动重建告知；不能将“保留复杂数据”解释为继续保留旧 owner。该设计还要求 R3 删除旧新建/编辑/新效果入口，而本树及 [WP06c 交付说明](../design/job-unification/WP06C_PATH_FLIP_20261026.md) 明确保留旧写方法和工具，故不能宣称 R1/R2/R3 已全部关闭。

## 下一步顺序

1. 先修集成新增的后端契约回归与并发关闭挂起，保留安全负控和 unknown/uncertain 语义；旧测试需要按新模型接线时，证明等价行为后更新，不直接降低断言。
2. 在独立集成分支吸收当前 main，解决上述 3 个冲突；重跑组合树的完整后端、前端及定向契约。当前 main 的 CI 不能替代组合树 CI。
3. 按设计完成 R3 新准入退场，再完成 R4 单 owner/fence 与离线迁移处置证据。真实副本和生产迁移另按授权推进；本次未进行任何真实数据操作。
4. R4 达标后实施 R5 旧入口/工具/UI/compat 删除，再做 R6 迁移 runtime 与发布包退场扫描；保留复杂原数据的非执行审计。
5. 恢复全量绿后验证三平台 CI、隔离真实 HTTP/浏览器/MCP 慢层及平台 executor 契约，分别完成真实迁移、部署 readback 和人工验收；之后再评估推进 main/practical。

可接受的已披露项是既有前端失败的归因、现有构建 chunk 提示、明确说明原因的 opt-in/platform skips 及过渡阶段的 alias。它们都不能用来宣称全量绿或整个设计完成。新增回归/挂起、未解决合并冲突及 R4–R6 缺口必须修复；环境缺依赖可以作为本机失败原因，但必须在完整环境重验后才计通过。
