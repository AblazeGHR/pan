# Action 链第1阶段候选交付

工作树：`D:/project/pan-worktrees/job-chain-phase1-20261025`。分支：`feature/job-chain-phase1-20261025`。实现基线：`550ff4b193e5c9db07408170be23561733464e08`。实现、矩阵和证据同一候选提交；该提交的完整 SHA 在交付消息中给出，也可用 `git rev-parse HEAD` 核验。本报告不宣称已获 MA 验收或已集成。

## 行为与边界

Job/Trigger 支持显式有序 steps（稳定 stepId、actionId、literal/input/event/前序输出/system 映射），接纳时冻结整链 Action/Template 配置和版本。串行等待完成契约，全成功才成功；默认失败、取消、unknown/uncertain 阻断后续。Run 保存每步 execution 与 Attempt 的状态、结果、错误、版本及 checkpoint；已完成步骤在恢复中跳过；安全持久边界恢复与副作用重投区分。原有无 steps 的入口与执行/通知路径保留，语义兼容单步骤，不改写历史。

矩阵：[IMPLEMENTATION_MATRIX_20261025.md](../../docs/design/job-unification/IMPLEMENTATION_MATRIX_20261025.md)。它逐项包含实际 MCP/HTTP/GUI、执行符号、Action/Job 表达、复用及缺口；§9 每项均列默认/备选/影响。首次暂停前曾先写实现草稿、未先落盘矩阵，未满足要求；恢复后撤回该实现草稿，先补齐矩阵，再重新实施。此顺序偏差已在矩阵中保留。

未实施 Scheduler→Event、Event Register/Trigger/Automation UI、旧 MCP wrapper 或真实数据迁移。前端未改，不执行 tsc/build/92资产。未操作 main/practical、push、远端 CI、部署/产品服务或真实数据。验证时观察 main=`399b86a92c4abd616fcb9671da432e22921284b4`、practical=`4c0d6428811a3239daae1241bfc5052cbd1f7050`；候选提交只写当前 feature 分支。

## 文件

- `packages/jobs/chains.py`
- `packages/jobs/chain_runtime.py`
- `packages/jobs/action_runtime.py`
- `packages/jobs/action_templates.py`
- `packages/jobs/action_api.py`
- `packages/jobs/system_actions.py`
- `packages/automation/chain_engine.py`
- `packages/automation/engine.py`
- `packages/automation/store.py`
- `packages/automation/api.py`
- `packages/core/background_jobs.py`
- `tests/test_job_action_chain.py`
- `tests/test_automation_action_chain.py`
- `docs/design/job-unification/IMPLEMENTATION_MATRIX_20261025.md`
- `artifacts/job-chain-phase1/REPORT.md`、`manifest.json` 和全部 JUnit XML（失败实验原字节以 .xml.gz 保存）。

## 最终验证

PowerShell，cwd 为上述工作树：

```powershell
$env:PYTHONPATH='.'
python -m pytest -q tests/test_job_action_chain.py tests/test_automation_action_chain.py tests/test_automation_core.py tests/test_automation_concurrency.py tests/test_automation_retry_parallel.py tests/test_job_action_templates.py tests/test_job_completion_recovery.py tests/test_job_system_actions.py tests/test_job_overlap.py tests/test_job_unified_contract.py tests/test_job_mcp_config.py tests/test_jobs_api.py tests/test_background_job_api.py tests/test_session_message_jobs.py tests/test_job_delete_safety.py tests/test_job_cleanup_failures.py tests/test_job_inbox_boundaries.py tests/test_background_jobs.py tests/test_main_lifecycle_jobs.py tests/test_job_delete_main_combination.py tests/test_jobs_completed_retention.py tests/test_scheduler_api.py tests/test_scheduler_dispatch.py tests/test_scheduler_engine.py tests/test_scheduler_cron.py tests/test_scheduler_mcp.py tests/test_scheduler_store.py tests/test_action_ids.py tests/test_action_groups.py tests/test_action_persistence.py tests/test_system_fields.py --junitxml=artifacts/job-chain-phase1/final.xml
git diff --check
python -m compileall -q packages/jobs/chains.py packages/jobs/chain_runtime.py packages/automation/chain_engine.py packages/jobs/action_runtime.py packages/jobs/action_templates.py packages/jobs/action_api.py packages/automation/store.py packages/automation/engine.py packages/automation/api.py packages/core/background_jobs.py packages/jobs/system_actions.py
```

最终 pytest exit=0：**698 passed，0 failures/errors/skipped**，其中新链专项 41 项，现有回归 657 项。JUnit：[final.xml](final.xml)；机器可读命令/计数：[manifest.json](manifest.json)。diff --check exit=0，compileall exit=0。存在环境既有告警：未知 pytest timeout 配置项、Pydantic lifespan 前向引用、Starlette/httpx 弃用；没有新增 skip/xfail 或弱化旧断言。

| 测试文件（tests/ 下） | 最终数量 |
|---|---:|
| test_job_action_chain | 31 |
| test_automation_action_chain | 10 |
| test_automation_core | 20 |
| test_automation_concurrency | 8 |
| test_automation_retry_parallel | 50 |
| test_job_action_templates | 33 |
| test_job_completion_recovery | 11 |
| test_job_system_actions | 11 |
| test_job_overlap | 4 |
| test_job_unified_contract | 13 |
| test_job_mcp_config | 2 |
| test_jobs_api | 54 |
| test_background_job_api | 25 |
| test_session_message_jobs | 14 |
| test_job_delete_safety | 29 |
| test_job_cleanup_failures | 14 |
| test_job_inbox_boundaries | 12 |
| test_background_jobs | 51 |
| test_main_lifecycle_jobs | 19 |
| test_job_delete_main_combination | 2 |
| test_jobs_completed_retention | 42 |
| test_scheduler_api | 19 |
| test_scheduler_dispatch | 4 |
| test_scheduler_engine | 29 |
| test_scheduler_cron | 43 |
| test_scheduler_mcp | 22 |
| test_scheduler_store | 37 |
| test_action_ids | 10 |
| test_action_groups | 64 |
| test_action_persistence | 2 |
| test_system_fields | 13 |

专项实测包含：单步结果/通知兼容、多步顺序、输入/Event/前序输出映射、映射缺失/类型错误、失败阻断、取消、unknown三种状态不重投、接纳后编辑链和 Action 不影响 Run、固定 Template 版本冻结、并行 Run 审计隔离、已知失败显式重试只重试当前步骤、monitoring 当前步恢复、恢复期间编辑下一版链、运行中撤权、落盘故障前/后不推进、进程退出后边界恢复不重跑、运行中步骤重启转 uncertain、可信 shell 逐步骤 admission、ASGI schema/权限/脱敏。真实临时子进程在完成步骤落盘后 os._exit(23)，新 Runtime 按同 Run 恢复；另有两步真实自有命令验证结构化输出与文件副作用。

旧 Action 回归 `test_action_groups.py` 中两项测试会创建 `127.0.0.1:0` 临时 HTTP fixture（重定向限制、响应容量）；测试 finally 自行 shutdown/server_close/join。没有启停 Pan/独立 Job 服务，也未使用8767/8768；新 API 验证使用无网络 ASGITransport。不能将这些测试等同用户现场或供应商消费验证。

## 失败实验与修正

保留所有失败 JUnit，不覆盖为绿；失败XML原字节以无损 `.xml.gz` 保存（避免原 traceback 尾部空格影响 diff --check），解压后保持原始失败状态与内容：core-initial（81中3失败，早期新链 fixture）、chain-first（26中8失败：Trigger fixture参数重复与Job安全边界被公开视图脱敏）、chain-second（26中1失败：测试未等 retry_wait 消失）、chain-fourth（35中1失败：fixture 调用错误方法）、chain-fifth（39中1失败：命令 fixture argv 内JSON花括号被既有占位符校验拒绝）、template-freeze（1中1失败：fixture缺用户授权）、chain-final（85中1失败：下一版配置覆盖旧Run restarting状态）。最终均由对应修正后698项汇总覆盖。

其他阶段绿证据：legacy-initial 55/55、chain-third 26/26、regression-first 300/300、regression-second 357/357、owned-command/template-freeze-second/recovery-edit 各1/1。它们与最终测试重叠，不能累加为独立通过总数。

## 未决契约与未测边界

- 本阶段显式 steps 的技术默认：schema v1、调用方稳定ID、每字段绑定、字典点路径（无数组/脚本）、structuredContent优先、运行时输入schema校验；仅新配置生效。无新静态输出类型系统、补偿、分支或并行步骤。
- 时间 Event 名称/schema/身份/授权，多个时间触发的关联/命名/编辑/删除，misfire/游标/nextFireAt，新组合存储事务与共享Scheduler影响UI，均留待后续冻结。本阶段继续旧时间路径，不选择新时间行为。
- 恢复材料：Job 的已知失败为保持冻结显式重试，私下保留chainRecovery直到下一admission/删除；Trigger最终终态删除snapshot。正在执行且收据不明的step阻断，confirm-recovery亦拒绝，不实现新对账解锁UI。
- 带 triggerRef 的绑定 interaction响应仅保留原单Action verifier；显式新链匹配此类Event时失败，不扩大绑定授权。后续整链绑定/验签契约未实现。
- 交互卡：**0张，IDs=[]**。没有发现必须改变旧用户可见行为才能完成本阶段的阻塞产品决策；默认与替代均在矩阵说明，没有以默认改动旧入口。
- 未运行产品服务/网络HTTP/MCP e2e、真实Session/Worker/QQ/供应商消费、GUI、真实shell树取消、部署重启、多机/多进程共享恢复、全部磁盘故障/极限容量组合。自有命令和退出进程仅操作临时目录；shell admission测试使用spawn替身。
- 候选只供MA后续在integration/job-chain-20261025验收/合并，等待用户命令；本TA不自行合并或推进受保护分支。
