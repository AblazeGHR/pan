# JOB-RETIRE revision2 integration-only closure

工作树 `D:/project/pan-worktrees/job-retirement-closure-20261027`，分支 `r6/retirement-closure-20261027`，基线 `add4bef7773216dd79793733a50a2ec19a6869c3`。
本候选只新增专属测试、离线 inventory/closure 工具和证据；没有修改产品、共享 conftest/support、React、CI 或三个 scheduler host，也没有读取/修改真实 data/profile、51 migrated 或 60 unrecognized 集合。

## 交付物与门禁

- [successors.json](../../evidence/job-retirement-closure/successors.json) 覆盖原 43 红逐 node，并列出 E01–E15、S01–S14、U01–U16 共 45 项的当前观察、证据入口和剩余缺口。当前 15 项有严格后继，1 项为新发现产品缺口，27 项仍无精确后继；P23 三原红是这 43 项子集。
- [inventory.json](../../evidence/job-retirement-closure/inventory.json) 扫描 1075 个生产/入口文本文件，发现 64 条依赖边、33 个动态 import 候选、0 个语法错误。它不把零静态 host import 当退役证明。
- [node-review.md](../../evidence/job-retirement-closure/node-review.md) 将源码、单测和未验证层分开；[validation.json](../../evidence/job-retirement-closure/validation.json) 核对 332 个原报告/原测试/三个 host Git blob。
- [job_retirement_closure.py](../../scripts/job_retirement_closure.py) 是只读门禁：依赖边、缺失严格后继、未完成 inventory 或保留 host 时固定 exit 2，输出 `removalAllowed=false`、`migrationComplete=false`。

## 实测结果

使用 `D:/project/Pan-main/.venv/Scripts/python.exe`，通过 `scripts/run_tests_isolated.py`，所有数据为 pytest 自有临时根、随机 loopback 端口。

- 新增编辑/closure gate：10 个节点，8 passed / 2 failed / 0 error / 0 skip；两条失败正是 whitespace-name 严格红，未吞掉。
- P23 canonical safety：3 passed，exit 0；真实 owned tree/HTTP cleanup、unknown 首次拒绝再明确复查、late terminal 均通过，资源工件显示端口释放且无剩余 owned process。
- 组合 focused：136 passed，0 failed/error/skip，exit 0；覆盖 startup/import、source migration/cold reopen、scheduler surface、P24 history/pin、quota unsupported 与 canonical recovery。
- 两条新增严格红明确显示 `FinalService.write` 接受纯空白名称（`" "`、`"\t\n"`），提交 config revision/operation。这是实际产品缺口，未改断言、未加 skip；缺口范围是 `packages/jobs/final_contracts.py` 的 name 语义校验与其写入路径。[final-gate.xml](../../evidence/job-retirement-closure/final-gate.xml) 保留失败输出。

## 当前结论与下一步

不能宣布旧宿主整体删除、整体迁移或真实 owner 清零。三个 host `packages/scheduler/store.py`、`time_events.py`、`legacy_bridge.py` 保持原样；没有放宽 v1 writer、composition、unknown owner 或 P23/P24 原红。`server.py` 仍有 lifecycle/background_jobs 兼容边界，`independent_service.py` 仍承载 canonical `/executions` 与安全 owner 证明，MCP manifest 仍保留旧 Runner 只读/取消控制。

MA 下一步应先处理两个 whitespace-name 红，然后按 [node-review.md](../../evidence/job-retirement-closure/node-review.md) 补 G2 owner 身份/资源结算、G3 真实迁移分类与历史索引、G4 完整通知/管理能力和三平台/部署/人工验收。任何 unknown owner、未 ack outbox、活进程或缺失动态入口证据都继续阻止宿主删除；不得自动重投 unknown。

没有 push、merge、main/practical 操作、保护端口操作、WSL 启动、依赖安装或真实服务重启。

## revision3 名称语义修复

MA 已复现原后继 8 pass/2 fail 后，本候选仅修改 `packages/jobs/final_contracts.py`：
`_validate_name_semantics`、`normalize_definition` 和 `_validate_document`。纯 whitespace
名称在写入/文档验证前返回 `ContractError(invalid_schema, details.reason=blank_name)`；合法
中英文名称（包括前后空白）保持原文，不自动 strip 或改写。递归检查覆盖五个 canonical kind
及 template 的嵌套 job/creator/trigger 定义。JSON Schema 结构没有需要重新生成的变化，
因此没有伪造或修改生成工件。

revision3 focused 命令见 [revision3-focused.xml](../../evidence/job-retirement-closure/revision3-focused.xml)：
**145 passed，0 failed/error/skip，exit 0**，包含两条原 whitespace 红后继、五 kind 一致性、
完整 FC01–FC04 schema/contract、CAS/idempotency 与 invalid-definition no-write。原始
`final-gate.xml` 的 8 pass/2 fail 仍保留，未重写旧证据。

## revision3 严格后继补批

本批只登记已有精确 canonical 语义测试，不把泛化 suite 当后继。原 43 节点当前为：
**30 strict-successor、1 product-gap、12 gap**。新增映射均写入
[successors.json](../../evidence/job-retirement-closure/successors.json)，覆盖结构化身份、生命周期/CAS、
legacy fallback 拒绝、session effect/retry identity、动态 target 冻结、partial/outbox、multi-entry
composition、owned shell、QQ typed error、schedule composition、target consistency 和 source identity。

严格后继矩阵为 **28 passed / 0 failed/error/skip / exit 0**，命令和计数见
[revision3-strict-command.json](../../evidence/job-retirement-closure/revision3-strict-command.json)
与 [revision3-strict.xml](../../evidence/job-retirement-closure/revision3-strict.xml)。

仍未关闭的 12 个原节点是：三个 resume/empty-scan/scheduled-fire 分支，QQ target/edit、QQ
targetless run-now、QQ backlog retry、existing-session 业务校验、bad schedule spec、automatic
recovery revocation，以及原 quota real-HTTP fixture。它们分别需要 native provider/real HTTP/
owner-policy 的精确后继；本批没有用现有泛化测试冒充，也没有改共享产品代码。三个 scheduler host、
unknown owner、真实迁移和 51/60 集合边界继续保持阻塞。
