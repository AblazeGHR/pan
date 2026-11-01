# R6-E scheduler 历史只读替代

- 基线：`28ed7ccd7908bf30b1dc33b48fdf4dbf21cc2d6d`；分支：`r5/p17-r6-history-read-20261025`。
- 本候选仅解决 task list/get/runs 的历史替代与 store 读取依赖；未删除宿主。
- 没有 push、启停服务、8767/8768 操作、真实 profile 读取/迁移或发布包 build。

## 实现与兼容

`packages/jobs/migration_history.py::Archive` 读取已安装的 `migration_history`、
`migration_unrecognized`、`migration_receipt`，不构造 FinalService/CreatorStore/owner。
数据库只用 URI `mode=ro`，缺库返回空；存在 WAL 明确返回 `history_unavailable`，
避免只读连接创建/更新 shared memory，亦不以 immutable=1 忽略 WAL 的已提交事实。

`packages/scheduler/history.py` 校验 Base64 原始字节与 SHA-256，投影原始状态并标记
`executable=false`；opaque unrecognized 保留原 bytes/hash/reasons/targetIds=[]。
原 ID、taskId/jobId 别名可寻址，重复 identity 明确失败；迁移快照优先于同 ID
残留源文件。无迁移行时直接读取 `background_jobs/jobs/*.json` 中的 scheduled-task，
不调用 background_jobs.list_jobs/store，亦不做迁移、补字段落盘、创建目录或执行。

HTTP GET `/api/scheduler/tasks`、`/tasks/{id}`、`/tasks/{id}/runs` 保留名称、
`ok/tasks/task/runs` 与 camelCase 包络。get 增加 `history` 原 bytes/hash/source 与
`log`（Base64 原字节+hash）、`receipt`（原键/原历史声明）。没有引用/receipt 时返回
显式 null，不推断 delivery 成功；已有 log 引用缺文件、hash 不符、越界则明确失败。
任务缺失返回 `not_found`，损坏/歧义/读取错误返回 `history_unavailable`。

list 默认仍返回全部；可选 limit 1–500、offset >=0，按创建时间/原 ID 升序，
缺创建时间排序为空字符串。runs 默认 HTTP 100/MCP 50、上限 500；按原 JSONL
行序反转（保持旧“最新在前”契约），先按原归属/别名过滤再分页；未知状态不归并成功。
两者增加 total；不存在 runs.jsonl 时保持旧空历史包络，非空坏行明确失败。

MCP `scheduler_list/get/runs` 经现有 HTTP 读取相同实现；list/runs 增量支持分页。
list 的 target_session_id 过滤沿用 MCP 层行为，发生在 HTTP 分页之后。
保留原身份检查与 Action 原归档的 job.read 授权；旧写仍认证后 410，owner-switch 404。

为避免 task router 的间接 store 导入，event-source 的 Sources/schema 延迟到其路由
调用时加载，根路径解析改用 history.data_root；未替换其 Sources 实现。
engine.preview_next 使用相同只读 task 投影，不再导入 store；status 原语义不变。
没有修改 canonical Job/Trigger/Creator 的定义、执行或迁移安装语义。

## 验收证据及边界

- 新增 `tests/test_scheduler_history_read.py`：131 条（含 111 原 ID 参数化）。
  通过真实 rows_for 格式生成隔离合成的 51 migrated + 60 unrecognized，不是实际用户数据。
  逐 ID 检查 raw bytes/hash/status/已有 log/receipt、不可执行标记与目录/文件 bytes 不变；
  分页排序、原别名、未知/缺失、log/hash/database 故障、opaque bytes、WAL 拒绝、
  迁移快照优先、无归属 run 隔离、Action 权限与 MCP→ASGI 查询。
- 独立 Python 子进程拦截 `packages.scheduler.store` import，成功导入 HTTP router
  并读取缺目录；测试也禁止 store 读取/写入及 FinalService/CreatorStore/OwnerFence 初始化。
- 首轮新增用例 126 passed/2 failed；红证据 `focused.xml` 保留且脱敏。
  失败分别是测试错误预期 missing-createdAt 排序、event_api 间接导入 time_events/store；
  修正契约预期和运行时导入路径后通过，不删断言或 skip。
- 中间兼容/迁移回归 336 passed；随后含 Creator/owner 的一轮 382 passed。
- 授权定向一轮 182 passed；最终组合 **396 passed、0 skipped、0 failures/errors，exit 0**（两条既有依赖 warning），见 `final.xml`。
- 全仓 collection：7110 collected，4 errors，exit 1；四个浏览器/real 测试模块缺
  Python `playwright`。详见 `collection.xml` / `collection.log.gz`。没有伪称全仓绿。
- 隔离 import-only 生产 route/tool 快照：15 条 scheduler routes、118 MCP tools，
  与 R6-D 相同；未执行应用 lifespan、未开监听。
- 全仓反向引用详见 `references-after.json`，包括 Python 显式/相对/懒加载 imports、
  动态 import 候选与 JS/TS 动态调用。静态扫描不能证明外部 plugin/部署代码零引用。

最终命令：

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_history_read.py tests/test_scheduler_api.py tests/test_scheduler_mcp.py tests/test_scheduler_surface_retirement.py tests/test_scheduler_host_retirement.py tests/test_scheduler_canonical_migration.py tests/test_job_final_migration_user_authorization.py tests/test_job_final_retirement.py tests/test_job_final_creators.py tests/test_job_runtime_owner.py tests/test_authorization_mcp.py -q -o addopts= --junitxml=evidence/r6e-history-read/final.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/ --collect-only -q -o addopts= --junitxml=evidence/r6e-history-read/collection.xml
python scripts/run_tests_isolated.py -- python scripts/audit_scheduler_surface.py evidence/r6e-history-read/routes-tools.json --snapshot
python scripts/audit_scheduler_surface.py evidence/r6e-history-read/references-after.json
git diff --check
```

未验证：全仓测试执行、真实 HTTP socket/浏览器、完整启动/停机、三平台/远端 CI、
真实数据迁移、部署与人工验收。以上成功层只表示本候选的隔离源码/ASGI/MCP 单测证据。

## 尚未解除的宿主引用

R6-D 的 57 条静态 import 目前为 56 条：store 31、engine 7、time_events 10、
legacy_bridge 3、cron 5；其中 store 移除 api/engine/event_api 三处，增加本次测试负控一处；
time_events 从单一顶层 import 拆为两个路由内 lazy imports，故数量 +1。

仍保留的生产 store 依赖包括 `final_service.bind` 根解析、final_runtime/coordinator
handoff 根解析、jobs/api 历史/retention、quota_policy builder、action_runtime calculator、
time_events 与 legacy_bridge 内部；另有 harness/测试依赖。engine 的 server shutdown、
Sources 历史/运行时、cron 与桥的历史门禁也未删除。

因此本候选解除 **scheduler task 历史 HTTP/MCP 缺替代** 这一阻塞；不等于 R6-D
宿主整体可删。仍需迁移上述依赖、验证真实 owner/cursor/receipt 条件并另行集成验收。
