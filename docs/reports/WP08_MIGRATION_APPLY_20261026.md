# R4 WP08：离线迁移 apply 候选

- 分支：`r1/wp08-migration-apply-20261025`。
- 基线：`da4551dcb085246097aae1bcab3f0d0d38438a74`；交付 SHA 以包含本报告的候选提交为准。
- 范围：`scripts/job_final_migration/`、新增 fixture 测试、脱敏验证工件和本报告。
- 未 push，未推进 main/practical，未启停服务，未访问 8767/8768，未接触真实 profile。

## 交付行为

1. 在 lite 工具上增加显式 `--target/--context/--apply/--expected-digest/--mark-read-only/--rollback`。
   不传 `--apply` 时仅规划，target 和其父目录不创建；root 与报告目标仍必须显式给出。
2. 只映射六类 simple 记录；生成通过 WP01 校验的 schemaVersion 2 Job/Trigger/Creator/Template 图。
   目标 ID 基于来源身份和 mapping version 稳定分配，整张分配表持久化，重跑不换 ID、不重复写。
3. 完整包保存来源/hash、record/schema fingerprint、cursor、原审计字段、历史证据和源 bytes 备份。
   complex 不生成 targetIds、不改原文件；unknown/坏 JSON/重复 key/scan error 拒绝整次 apply。
4. dry-run 的 planDigest 同时绑定盘点和上下文。提交前重验所有已扫描源 bytes、集合与 ledger；
   Action 引用必须已发布/启用并满足固定 revision/digest/completion/input schema，已有授权主体不得替换。
5. 单个私有 JSON 包同目录临时写、fsync、原子 replace，持有独占 lock；已有包变更前备份前一完整包到 `.bak`。
   rollback 撤销离线 canonical 发布/只读标记，保留来源证据与原 ID 分配，不恢复旧执行、无源数据删除。
6. 迁移完成可持久化旧记录 read-only 意图和 ownerEpoch；普通重跑保持标记。
   成功发布后报告才置 `plan.executable=true`。没有 server import、服务控制或旧 store/pass 删除。

**边界：这是离线 canonical 包 apply。** 所有新执行定义 disabled，Template 为 draft。
只读/owner 切换元数据在迁移包内，未修改源文件权限，也没有跨进程 fencing authority。
`runtimeActivated=false,runtimeFenceVerified=false,ownerSwitch.runtimeEnforced=false` 明确保留。
这符合本工作包不改运行时/不启停服务的限制，但不能据此关闭设计 §5.2 的真实 owner 切换或 R4 生产迁移门禁。
授权/fence/provider 的上下文声明也不冒充独立验证过的生产证据。

## CLI 与资料

完整用法、context 合同、六类映射、rollback/lock 恢复和平台验证矩阵见
[迁移包 README](../../scripts/job_final_migration/README.md)。所有示例只允许在自有临时 fixture 中运行。

```text
# 默认 dry-run，取得报告中的 planDigest
python -m scripts.job_final_migration --root <fixture-data> --target <outside-root-canonical.json> --context <context.json> --out <outside-root-report.json>

# 显式提交
python -m scripts.job_final_migration --root <fixture-data> --target <outside-root-canonical.json> --context <context.json> --expected-digest <planDigest> --apply --mark-read-only --out -

# 显式回滚离线发布
python -m scripts.job_final_migration --root <fixture-data> --target <outside-root-canonical.json> --rollback --expected-digest <current-bundleDigest> --out -
```

## 实测证据

| 层次 | 结果 |
|---|---|
| focused 组合单测 | **176 passed / 0 failed / 0 errors / 0 skipped**；exit 0 |
| 新 apply 测试 | 31 项，涵盖六类、schedule once/interval/cron 与四种 business action、broadcast/chain、幂等、来源 hash/root 快照、complex/unknown/corrupt、坏引用、源与 context 变更、部分 write/fsync/replace 失败、只读/rollback、路径冲突与独立进程无 server import |
| lite 回归 | 原 16 项全部通过 |
| WP01 契约 | 129 项全部通过 |
| 提交边界 crash | 实际子进程在 target replace 前/后 `os._exit(77)`；分别读回旧完整包/新完整包；root 不变；锁不自动清除；确认子进程退出后可复用原 operation 恢复 |
| 独立 CLI 演练 | `python -m` 默认 dry-run→apply→幂等重跑→rollback；6 类、11 个 canonical objects；root 快照不变；exit 0 |
| 静态 | compileall、`git diff --check` 通过；常规 Git pre-commit hook 无前端路径命中 |

组合命令：

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_final_migration_apply.py tests/test_job_final_migration.py tests/test_job_final_contracts.py -q -o addopts= --tb=short --junitxml=artifacts/wp08-apply-tests.xml
```

工件：[统计](../../artifacts/wp08-apply-test-summary.json)、
[JUnit（已去主机名）](../../artifacts/wp08-apply-tests.xml)、
[CLI 脱敏结果](../../artifacts/wp08-apply-cli-evidence.json)。
本机原始测试日志为 `artifacts/wp08-apply-tests.log`（ignored，不提交）。
保留 1 条已有环境 warning：pytest 不识别 `timeout` 配置；没有通过屏蔽 warning、放宽断言或 skip 使结果变绿。

开发过程曾出现通知状态词映射、链 fixture 的 referenceGeneration、Windows 默认文本解码错误；
均在源码/fixture 中修正后重跑。上述计数只指最终完整组合运行。

## 尚未验证

- 全量后端测试、前端构建（本次未改前端）。
- 真实 HTTP/浏览器/MCP、真实 provider/capability/目标存在性与 profile grant。
- Linux/macOS 运行、远端三平台导入/收集/CI。
- 旧/新时间计算器下一 100 个发生点、完整 DST/pause/resume/misfire 行为对照；本轮只验证规范映射及来源 cursor 保留。
- 真实 profile 的只读盘点、复制数据演练、生产迁移、源 owner 排空/lease fencing/激活或零旧写。
- 真实断电/网络文件系统原子性、部署与人工验收。

源 store/pass、复杂原数据和运行时均保留；R6 删除不属于本候选。
