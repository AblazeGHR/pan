# Practical 数据门禁：unknown Queue 兼容修复

结论：本轮 **main-only 数据兼容技术门禁已关闭**，待 MA 独立复审。原阻断的原生 `unknown_after_crash → queued` 已修复；实际 practical 副本 gate **7 passed / 0 failed，exit 0**。这不等于已合入 main、部署、现场迁移或完整产品人工验收。

## 基线、提交与实现范围

- 新隔离树：`D:/project/pan-worktrees/queue-unknown-migration-fix-20261023`。
- 分支：`fix/queue-unknown-migration-20261023`，从已接受可靠性候选 `259030968d7bbf231fa587265609a916f1cd137d` 创建，初始 clean。
- 产品修复提交：`775c6b83b91e147c77b724bf82a1b2eb80cb698a`。
- 精确冻结受测源码：`cfd5c71ace2e33ffe898328be699e2dec672e04d`；此提交仅补 private-root 文件换行兼容。其后的交付提交只加入文档/审计证据，产品代码和测试不再改变。
- 产品文件仅 `packages/core/worker.py`；回归文件 `tests/test_queue_unknown_migration.py`；测试说明 `docs/TESTING.md`；实际副本 harness 复用已交付的 `audit/practical-data-gate/run_gate.py`，增加真正的 Queue migration / repeat 断言，没有删除原断言。

修前直接审阅原审计树的 `unknown-queue-probe.json` 和迁移函数，确认原生 unknown 被 backoff 成 queued。原审计树与证据未改动。新增测试先在原产品源码执行：**16 failed / 3 passed，exit 1**；失败覆盖 pending、detached ledger、queued/unknown ledger 冲突、cold reload、重复输入与 operator migration。红证据 `red-junit.xml` / `red.log` 保留。

最小修复在既有状态机内完成：

1. 识别原生 `unknown_after_crash` 及显式 `unknown` / `uncertain`，不将这些已知状态当成缺省 queued。
2. pending 或 ledger 任一副本保留 unresolved 证据时，queued 副本不能覆盖它；保留原 Queue ID、正文/来源等既有数据和扩展字段，校正冲突的 dispatchState，重复迁移稳定。已有可信 sent_to_cli 回执仍按原终态去重契约处理，不重发。
3. ledger-only recovery 可以恢复可见的 unresolved pending 行，但没有 claim 权限；普通 GET migration 不凭 detached ledger 新增 pending 行。自动恢复判定不再为 unknown 启动 Worker。
4. 原 `retry_pending_item` 与重复 clientMessageId 返回原 unresolved 状态/ID，不发恢复信号。未知结果不能通过普通 retry 或重复发送变成 queued；本轮没有另造确认/恢复协议。
5. 普通 pending、queued、sent 去重与旧 unfinished reservation 的兼容处理保留；Pause/Illegal/lease/owner 等既有 gate 不变。没有新 registry、调度器或全局 I/O 锁。

状态仍由既有 receipt / Queue API 暴露；pending-only UI 不因此改成历史账本页面。本轮未改前端，也不宣称新增了 unknown 人工解决 UI。

## 冻结源码验证命令

全部工作目录为新隔离树，Python 使用 canonical `E:/software/miniforge/python.exe`；未设置 PYTHONUTF8。所有证据文件显式 UTF-8，日志重定向为 PowerShell UTF-8。

```powershell
# 修前；产品仍为 259030968d
python scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_queue_unknown_migration.py -o addopts= -q --junitxml=audit/queue-unknown-migration/red-junit.xml
# exit 1；16 failed / 3 passed

# 固定 cfd5c71ace2e33ffe898328be699e2dec672e04d 后
python scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_queue_unknown_migration.py tests/test_queue_migration.py tests/test_unified_server_queue.py tests/test_delivery_semantics.py tests/test_task_id_inheritance.py tests/test_job_inbox_migration_drill.py tests/test_premerge_legacy_data.py tests/test_report_action_coexistence.py -o addopts= -q --junitxml=audit/queue-unknown-migration/frozen-junit.xml
# exit 0；96 passed / 3 existing skipped / 1 dependency warning

python scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_agent_inbox.py -k "unknown_never_replays or legacy_migration_preserves_original_and_never_resurrects or queue_commit_then_crash_cold_recovery or legacy_subscription_maps_full_without_old_backlog" -o addopts= -q --junitxml=audit/queue-unknown-migration/inbox-junit.xml
# exit 0；4 passed / 24 deselected / 0 skip

E:/software/miniforge/python.exe audit/practical-data-gate/run_gate.py
# exit 0；7 passed / 0 failed

E:/software/miniforge/python.exe audit/queue-unknown-migration/check_source.py
# exit 0；源只读 hash/refs/status 后检

git diff --check
# exit 0
```

96 项中的 3 个 skip 均为既有的 “Oneshot mode retired by user; implementation retained”，分别是 one-shot spawn success、spawn failure 和 current-input task-ID 用例。没有新增 skip/xfail；1 个 warning 是已有 NoneBot 依赖的 pydantic forward-reference warning。修复开发中另一次较窄 green 为 61 passed / 2 existing skips，保留但不与冻结结果相加。

pytest 生成的 red log/JUnit 尾部空白与换行已规范化用于 Git 检查；原始 bytes 保留在 owner-only 私有副本的 raw-evidence，原始/提交 hash 见 `evidence-normalization.json`。没有删改失败内容。

新增 22 项回归包含原生/alias 三种 unresolved、三种数据位置、save/cold reload、重复迁移、真实 operator `--apply` 入口的临时副本、重复 enqueue / retry 不 spawn、不触达实际 claim，以及普通 pending/sent 兼容。

首次 canonical harness 在复制前因 private-root.txt 尾部换行导致 WinError 123；修复仅为 `.strip()`，在 `harness-path-failure.log` 保留该失败。没有借此放宽安全 guard 或产品断言。

## 实际 practical 数据副本矩阵

源 checkout `D:/project/Pan`，HEAD / practical 为 `2652ac8e6d0ff312a255666bc5e459890346e6e5`，main 为 `ca464c8d55e9c857aa1fa85652e3dcb3e1f7025e`。只读 before/after refs 相同，practical tracked status 空，根 config hash 不变。没有移动 main/practical，未操作原 ETA/审计树。

| 数据 | 冻结源码实际结果 | 覆盖界限 |
|---|---|---|
| Session / history | 96 Sessions、62,980 history；冷读、hydrate、copy-only save、再加载保存，history bytes 不变 | copy 内 workdir 安全重映射至私有空目录，不访问原 workdir |
| Queue / ledger | 1 queued、1,231 ledger 条目；对全部 Session 实际调用 restore_ledger migration，再调用均 False；无 Queue/ledger 丢失，两次保存精确一致 | 本次真实 pending 中 unknown/steerPending 为 0；三种 unresolved 的 replay 安全由合成原生负控和新增回归证明 |
| unknown gate | 原 Queue ID 保留，unknown_after_crash 仍为 unknown_after_crash；没有 dispatch 或业务效果 | fixture 明确合成，无用户正文 |
| Job / scheduler | 48 Job：32 background-process、10 main-lifecycle、4 scheduled-task、2 session-message；4 scheduled views；旧记录只读 bytes 不变、disabled/no-recovery 投影；846 个额外字段 occurrence 保留 | 32 completed / 5 cancelled / 10 failed / 1 running。未恢复 running Job，未执行计划；独立 scheduler 目录不存在 |
| Action / Group / Template | 2 Actions 保留；copy 安装 5 系统 Actions，再安装稳定；0 Groups / 0 Templates | 没有真实 release/pin/Template 历史样本；合成回归覆盖字段保留与投影 |
| QQ Inbox → Agent Inbox | 11 JSON / 1,349 messages / 11 lockers；实际 import + reopen + repeat 逐行相同，JSON bytes 不变，无消费或 forward | 源 Agent Inbox 不存在；既存消费账本由合成 crash/unknown/tombstone 回归覆盖 |
| QQ history | 15 JSON / 1,534 records，原始 loader 输出与 bytes 不变 | 直接执行产品两个纯读 AST 函数，不导入/启动 NoneBot host，不是 QQ 网络验证 |
| User Locker / EventStore / Trigger ledger | 源不存在；副本实际空初始化两次均稳定 | 同名 automation 目录只有无关旧 quota guard 文件；未将其冒充新 automation 记录 |

实际副本结果与 JUnit：`../practical-data-gate/result.json` / `junit.xml` / `snapshot.json` / `unknown-queue-probe.json` / `gate.log` / `exit-code.txt`。采样 schema/hash 不含原始路径或正文；详细 manifest、copy 和用户内容仅在 owner-only 临时根保留至 MA 验收，路径见 result.json。ACL 仅当前 owner，private-root.txt 被忽略、不入 Git。

## 源 hash 与无副作用证据

270 个 JSON/JSONL 按文件采样，JSON 核对 size/mtime/hash，JSONL 取完整行并校验 prefix；此次没有截掉尾部字节。源目标存储中 SQLite 数量 0；若存在则 harness 使用 mode=ro + query_only + backup，一致 backup 分支此次不冒充已实测。没有复制真实配置正文、凭据、日志、native profile、媒体或附件进入 Git。

实际 loader 阶段 Python audit hook 拒绝源 data/config 的任何访问、拒绝私有副本/audit 外写入、网络与进程效果。源数据仅由采样和后检代码以 rb 打开；所有迁移与 SQLite 初始化发生在私有副本。未运行 Service/Runtime/Engine start、scheduler recovery、用户 Worker 或真实 provider/QQ/OAuth；没有监听 8767/8768 或其他服务端口。

gate 内后检为 **268 unchanged / 2 activeChanged / 0 missing**。后续独立只读 hash 检查同样为 268/2：一个 JSONL 仍匹配采样 prefix 且增长，一个 JSON 元数据被活跃源改写；`source-after.json` 仅保留 hash key、类型、prefix 相等性及 size delta。源未暂停，**不是全局事务快照**；不能仅用 hash 把所有写入归因某个进程。没有本任务源写入的依据是 audit guard、只读 open/backup 路径和无源 loader/进程效果，不把活跃源变化隐去或说全部 bytes 恒定。tracked 源树、refs 和 config hash 均保持不变。

## 迁移、回退和未验证边界

1. 旧 API/kind/普通 pending 与 legacy 字段兼容保留；新增识别的 unknown/uncertain 不会变成新执行授权。未知扩展字段不被拷入可执行策略；旧 Job 不会因迁移自动 enable 或 recovery。
2. 此修复不能恢复此前版本已经把 unknown 改为 queued 且覆盖原证据的数据。仍有 lastDeliveryState 的历史行也不能在本轮猜测判定全部副作用；本次真实 pending 是普通 queued，未对其改写。确需处理历史已投递事实须另行人工核验，不能声称本补丁撤回已发生效果。
3. 不能把修复后的 unresolved 数据交给旧 writer 继续运行：旧版本可能重新自动 queue。回退须停写、备份并保留 unresolved 账本；旧新进程不能共同写同一 profile。技术门禁针对 main-only 合入，不授权 practical 更新或现场迁移。
4. QQ import marker 仍按文件路径，非增量 cursor；旧 QQ writer 在已导入 JSON 文件继续追加不保证会二次导入。部署 cutover 须单 writer 与一致备份；本任务未执行 cutover。不以旧 JSON 回滚覆盖新 Inbox 消费/Queue 事实。
5. 不存在的真实 Agent Inbox/User Locker/Event/Run/Template 数据仅验证首次初始化和合成回归。未测真实 provider/QQ/账号、全服务启动、HTTP/MCP/浏览器、用户现场权限与人工验收；本轮为小范围存储/迁移修复，无前端变化，不机械全仓/build。
6. 自有 harness/pytest 已退出；没有自有 listener、Worker 或 CLI 后代。受保护私有副本保留待验收，未清理其他树/真实数据。没有 push/release、服务启停、main/practical 修改。

交付后停止等待 MA。技术阻断已由红→绿和实际副本 gate 关闭；合入决定与最终独立验收仍属于 MA。
