# Legal Offline 合入 main / practical 验收

用户已确认真人验收通过，并授权合入 main、practical；发现回归/兼容问题先补丁复验。本次在独立集成树 `D:/project/pan-worktrees/legal-offline-final-integration-20261019` 完成组合验证，再以 fast-forward 推进目标分支，不做 reset、不 push、不重启服务。

## 集成身份与修订

- 接收基线：main = practical = `9943cb6d90069a699c03f11af6d530f60d5afe96`。
- 原交付分支：`review/legal-offline-illegal-lock-20261018`，HEAD `68771ba63274dd54fa4e2ba3ce9db177f19f0917`，保留不删除。
- 集成分支：`integration/legal-offline-final-20261019`。
- 已验证源码 merge commit：`fe8f933c753d46cb3964e5fa6872f8e0260c917d`，包含全部交付和下面的兼容修订。随后的本报告/证据提交不改产品源码。

唯一源码合并冲突在 sessionStore 的 SUMMARY_PROJECTION_FIELDS：保留 main 的 historyEpoch/historyRevision 和交付的 legalOffline/illegalOffline/legalityDiagnostics，未任选一边覆盖。

扩大组合回归初轮发现 13 个失败：旧 Session Details API 测试仍期待 lastLegalWorkerState 改写及旧 save_async 路径；旧 Exit/Restart 测试仍用 lastLegalWorkerState 的假对象构造执行快照。保留原测试身份，更新成明确的新 operation ledger fixtures、真实原子 replace 故障注入、落盘先于广播和冻结快照检查，不添加 skip/xfail、不提高超时、不恢复旧字段业务判断。同时补回缺失 Session 的原有 `Session {id} not found` 错误格式，减少旧客户端兼容差异。34 项相关测试先单独通过，再重跑完整组合。

## 最终验证结果

证据目录：`audit/legal-offline-final-integration-20261019/`。

| 范围 | 最终结果 | 退出码 | 证据 |
| --- | --- | --- | --- |
| 38 个后端文件：生命周期、队列/lease/Steer、自消费、报告、持久化、原生准入/辅助 operation、watchdog、旧 API 兼容等 | 690 passed，0 errors/failures/skips | 0 | backend.xml / backend.log / backend-files.txt |
| React 18 个相关测试文件：冲突相关摘要/history、WS、TopBar、Illegal filter、Details、Job、队列图标等 | 393 passed，0 skipped | 0 | frontend.log |
| 真隔离 HTTP + Chromium：服务信号、safe/preserve API、q、迁移重启、自动/手动锁、即时发送及图标点击 | 22 passed，0 skipped | 0 | live.xml / live.log |
| 真本地 Codex CLI + 合成本地 Responses provider | 3 passed，0 skipped | 0 | real-native.xml / real-native.log |
| React tsc + Vite + 压缩产物校验 | 通过，72 assets | 0 | build.log |
| practical 真实数据副本迁移及浅保存 round-trip | 91/91 metadata + 91/91 history 通过 | 0 | practical-data-compat.json |
| 冲突修复/兼容补丁定向复验 | 34 passed，已包含在 690 内不重复计数 | 0 | compat-api-patches.xml |
| prepare_validation_env / merge commit React hook / 源码 diff check | 通过 | 0 | 命令输出 |

去重共 **1108 个测试通过，0 failures/errors/skips**。不代表整个仓库所有测试或远端 CI 均运行。构建仍有原有动态/静态 import 与 chunk 大小警告；测试仍有现有 FastAPI/Starlette deprecation warnings，不影响退出码。

## practical 数据兼容性

只读扫描 practical 真实 `data/sessions`：91 份 JSON 全部可解析，全部缺新 legality/operation 字段，当前 queue_pending 总项数为 0。1 份旧 lastLegalWorkerState=running 无稳定 run 身份；按已批准的未知历史规则，不凭旧值伪造事故或消息锁。

新增可重复验证工具 `tools/validate_legal_offline_data_copy.py`：读取实际 metadata 与 JSONL 到系统临时目录，强制 Session store 指向该副本，再执行新版本迁移准备、浅保存和重新加载。逐项验证 name/adapter/config/权限/workdir/队列/ledger/幂等索引/Pause/报告策略/managed 关系/未读计数/旧只读字段及新合法性账本保持一致；验证旧恢复意图键不会输出、新数据不制造事故。

结果：91 个 metadata round-trip 全通过，91 个历史 JSONL 哈希全部保持（合计约 245 MB），inventedIncidents=0，sourceChangedDuringRead=0。实际 practical 数据未写入，临时副本自动清理。报告只包含数量和元数据清单摘要哈希，不包含 prompt、正文或附件。

实际队列为空，因此 Manual/Illegal 转换、报告锁、未知交接、lease、崩溃恢复等有状态迁移由组合回归的合成 fixtures 和真实隔离服务覆盖，不把空队列副本扫描冒充这些路径实测。practical 的 57 份 background_jobs JSON、2 份 terminal JSON 均可解析；原持久 Job action 标识保持兼容，未写注册表。

## 推进与运行边界

目标工作树合入前没有 tracked 改动，但有用户既有 untracked 文档和 node_modules 备份目录；全部保留，不 add/stash/delete。完成后应 main/practical 指向同一集成结果，且原 review 分支保留。

共享依赖只读使用 `D:/project/Pan/packages/web/node_modules`，没有安装或改写 canonical 依赖。前端产物已在独立集成树构建验证；不热替换当前 practical 服务正在使用的 dist，避免旧运行中后端配新 API 前端。main/practical 本次为源码集成，没有执行应用部署、重启或 release；后续切换运行版本时使用该版本构建产物并正常重启。

没有对 8767/8768 发测试请求；只读检查 existing listener 身份。所有真实测试服务都使用自有临时数据根和动态 loopback 端口，无远端模型消耗。最终检查自有测试进程/CLI 无残留；其他任务的进程/未跟踪文件不操作。

原人工验收已经通过。本轮补充的是最新 main 组合回归和数据副本兼容证据，仍不宣称真实生产运行版本已经升级、Windows SCM/实体键盘全覆盖或外部未观测客户端的逐 turn 完整追踪。
