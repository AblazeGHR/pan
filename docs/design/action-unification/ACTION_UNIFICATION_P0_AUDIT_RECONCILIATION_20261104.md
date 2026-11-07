# P0：原目录任务只读审计接收与新决策对照

来源：Session `ses_237eb38a86e4af09`，任务 `ACTION-CATALOG-UI-20261104-1` 的终态报告。MA 已读取原报告并核对工作树 HEAD 为 `c9dc621a01ccb568878c8a37297c034cbaec18de`，dirty 候选存在，未提交。

原报告：`D:/project/pan-worktrees/action-catalog-ui-20261104/docs/reports/ACTION_CATALOG_UI_MIGRATION_20261104.md`。
原实验入口：该树 `evidence/action-catalog-ui/audit_fresh_profile.py`、`red.json`。本次未独立重跑实验；下面的 HTTP/Chromium 数值是 TA 证据，不是 MA 新运行结果。

## 可作为 P0 输入的事实

| 观察或缺口 | 新方案承接 |
|---|---|
| fresh profile 旧 `/api/actions` 为空，canonical `/api/jobs/actions` 有15项，原页面不显示 | P1唯一目录与P5页面；不能靠双tab宣告完成 |
| canonical Action 目前只有产品发布与只读API，没有用户CRUD/publish | P1补真实用户草稿/发布/版本入口 |
| 自定义api/command没有canonical provider装配 | P2抽取复用既有执行原语，direct与Job上下文走同一命令实现 |
| session_operation pause 无canonical Action | P0逐字段核对既有pause/权限/目标选择，P2保留为受控命令能力，不删原功能 |
| job_template只有现有instantiate API，未装配为可调用Action | P4以参数化Job创建命令复用既有instantiate/composition |
| Group release/pin/plugin已有宿主，provider能直接执行命令 | P3保留发布/绑定；P2统一调用、授权及回执。直接效果本身不再因为无Run而判违规 |
| 旧后台/Action Job写入口已退休，产品typed执行已由Job承接 | 保持已退休入口，不恢复旧dispatcher；新增直接调用走新统一入口 |

## 已过时的建议，不作为实施依据

原报告§3及部分映射说明提出“所有副作用都必须经Job”“Group写能力必须Job化或只读化”“Trigger只准入Job”。这些沿用旧D2，已被可信用户确认的U01/U02/U11取代。

当前依据是 `ACTION_UNIFICATION_DETAILED_PLAN_20261104.md`：Action命令直接返回输出；Group固定版本发布工具；Action可创建Job；Trigger可准入Action或Job，直接Action不建立独立自动重试/恢复编排。

原报告所列“待用户决定”的Group终局、模板形式及执行边界已有上述决策，不再次作为阻塞问题询问。具体provider白名单、pause调用链和执行上下文抽取仍是P0代码设计工作。

## 不构成的验收

- 原只读UI候选尚缺Vitest与修后生产浏览器，不能签收或合并。
- Group命令效果、canonical自定义写入、真实403与数据迁移尚未验证。
- 只读报告接收不等于P0完成：仍需逐能力映射、真实receipt/owner抽取设计和原模型文档修订。
- 原任务保持superseded，原Session/dirty/证据保留；没有派发、续作或清理授权变化。
