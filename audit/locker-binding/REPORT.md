# Locker B1 修复候选

2026-10-23，TA 实施交付，等待 MA 独立验收。基于 clean `1b81fc66068b23018085c9aa0c09472e8dbbb0c8`，隔离树 `D:/project/pan-worktrees/locker-trusted-binding-20261023`，分支 `fix/locker-trusted-binding-20261023`。

**源码 SHA：`55dfb2006e574eee9d311a406fcd8be7a161b05b`。本轮验证通过，没有剩余已复现阻断；不等同于 MA 验收或部署通过。** 最终证据提交仅增加本 audit 目录，其 SHA 见交付消息。

## 行为与可信边界

- 创建选择卡片时，服务端在现有 Trigger/Action writer 下解析并保存 Trigger revision/digest、Action ID/digest、参数映射、固定 selector、targetIds 和来源授权依据。生产者提交不能提供 trustedChoices/bindings；MCP 将 caller 身份作为 producerSessionId 注入。来源与 owner 不混为用户身份。
- 卡片独立 bindings 列保存已解析内容。幂等创建保留原定义和绑定，不随新定义更新。前端显示服务端可信 operation、Session IDs、参数映射、Action digest、来源与授权依据；生产者 label 只是原选项名。
- 用户批准前复核；变化返回409 stale，未绑定旧 choice 返回409 incomplete，不写 response。批准后的持久 admission 再核对，在 Trigger/Action writer 下提交唯一 directed Event。明确无接纳的绑定错误显示 rejected/dispatchError，未知结果仍 unknown。
- 首次与后续 attempt 验证绑定后执行卡片保存的 Action 副本；每个 Session effect 前再次复核。变化明确失败，不读取新定义执行新目标，不自动修改卡片或扩大批准含义。仅此类 Interaction Event 使用该路径，一般 Job/Action 动态引用和 Group pin 不改。
- 可证明并接线的能力为 `session_operation` + explicit selector。来源受 restrictToManaged 限制时，只接受 self/managed 内的 owner 与全部目标；不隐式 claim。event/query selector 不能证明创建时范围，返回 incomplete。其他 Action 类型尚无此契约的完整效果范围证明，也返回 incomplete，不用全局白名单放行。
- B2 原回执修复保留：in_flight/unknown，包括用户删除后的 detached，只查原 eventId；当前 Trigger 停用或修改不阻止核对。缺回执保持 unknown，不重新 admit。
- 新通知的实际 CLI 文本包含 JSON：cardId、responseKind=submit/reject、summaryBody 前200个 Unicode 字符，以及实际用户 answer/choiceId（若提交）。不带完整 body/附件。reject 不执行 choice；delete 不建立通知。用户 response 已记录与 Trigger 接纳、Action完成仍分别报告。

源码入口：`packages/interactions/trusted.py`（解析/范围/复核）、`store.py`（绑定迁移/事件/通知）、`api.py`（创建与批准检查）、`runtime.py`（原子 admission）、`automation/engine.py` 与 `actions/executor.py`（卡片专属 attempt/effect gate）、`LockerView.tsx`（可信展示）。规范更新于 `docs/design/User-Inbox-Interaction-Card-Contract.md` 与 `docs/skills/pan/references/http-api.md`，稳定 schema 位于 `packages/interactions/schema.json`。

## 红绿、屏障及恢复证据

修前三个 B1 红例及 B2 红例保留在基线 `audit/locker-independent/red.log`、`red.xml`，原最终阻断证据保留在同目录 final.xml；没有删除或 xfail。当前 `tests/test_locker_independent_review.py` 保持三个场景和“不执行 foreign target”的安全断言，并增加 stale/无 Event/无 effect 或 failed Run 的更严格断言。由于正确行为允许拒绝，已将原测试中强制“必须 succeeded”这一脆弱前提改为要求显式拒绝/失败，未放宽目标安全断言。

`tests/test_locker_trusted_binding.py` 的10项定向用例覆盖：

1. 创建时到 Card 持久化之间同时持有现有 Trigger/Action writer，竞争写者不能改变解析内容。
2. 批准前定义变化：409 stale，response 仍空；批准后到 admission 屏障变化：明确 rejected，无 Event。
3. 首次 executor 进入之后、首次 effect 之前修改 Action 或撤销来源权限：无 Pause effect、Run failed。
4. 来源范围外拒绝、managed 范围放行、动态 selector 不猜范围。
5. 旧 SQLite 副本增量 bindings={}，原列逐项保持、源字节保持，无推断绑定，可拒收。
6. 已持久 Event 丢回执、Run queued 后关闭；重开服务后仅对账一次原 receipt。未改定义正常完成一个 Run；改定义只失败，不执行新目标。
7. 第二次 attempt：原绑定 Action 在首次失败后被修改，后续 attempt 明确 failed、不读取新目标，也不继续自动重试该绑定错误。

B2 四组合（活卡/删除 detached × 原回执有/无）继续通过，禁止新的 authorize/accept/append，只能查询同一个原 eventId。缺回执保持 unknown。

## 最终命令、退出码、JUnit

统一从本树执行：

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe audit/locker-binding/verify.py
```

脚本总退出码0；每条完整 argv、cwd、exitCode 和源码 SHA 记录于 `commands.json`。该脚本只启动自有隔离测试实例，不访问部署服务。

| 最终检查 | 退出码 | 结果与证据 |
|---|---:|---|
| Python：三个原 B1 场景、B2、绑定屏障、Interaction、AgentInbox、Job Template/边界、retry，并定向核验一般动态 Action | 0 | **174 passed，0 failed，0 skipped**；python.xml、final-python.log |
| UI：LockerView 与 Bearer 请求 | 0 | **14 passed**；ui-final.xml、final-ui.log |
| `pnpm build`（tsc -b + Vite + compressed assets verification） | 0 | 92 compressed production assets；final-build.log |
| 随机端口真实 HTTP + stdio MCP + 生产 Chromium + fakeCLI + 冷重启 | 0 | **1 passed**；http-final.xml、final-http.log、browser.json、http-evidence.json |
| git diff --check | 0 | final-diff.log；提交前另检查 staged diff |

原有三条 Python 工具库 deprecation/incomplete-type warning 保留，不安装依赖或以 skip 屏蔽。没有机械重跑全仓测试。

迭代失败证据保留：initial 为旧 fixture 直接建无绑定卡片，产品已按新规则拒绝；barriers 初版错误调用 Session.save 而非 session.save；cold-barriers 初版在故意丢 admission receipt 后未触发测试 dispatcher wake，未能到达 queued 屏障。修正 fixture 的 API/调度控制后保留严格断言，冷恢复最终通过。中间通过计数不与最终计数叠加。原始日志/JUnit 字节保存在 raw-evidence.zip；可读副本仅去行尾空白。

## 生产 GUI 与实际通知消费

最终 HTTP 使用随机端口 **61751**。先核验自有 PID、data/session/config/AgentInbox/gateway manifest/fakeCLI 路径；测试空 QQ manifest，CLI argv 固定到本地 fake_stream_cli。`http-evidence.json` 保存身份、生产 index SHA256、原 Queue IDs、实际 CLI received、History 和 queue_delivery_ledger。

- restrictToManaged 来源对外部目标 agent_notify 被拒；其引用同一目标的卡片创建也被拒。由用户路径显式 claim 后再创建成功，未让 create 隐式取得 managed 权限。
- Chromium **153.0.8010.12** 访问生产 `/react/locker`，使用既有本地 key 登录。在 label 故意为 `Keep target running` 时仍展示真实 `pause_everything`、目标 Session、来源 self_or_managed 和 digest，用户从该生产 panel 选择并提交，表单随后只读。
- **1280/390/320px** 的可信区域均在 viewport 内，document scrollWidth 与宽度一致，无 pageerror。PNG 和 bounding box 见 browser.json；320px 截图已目视核对，文字自动换行、内容可见。320px 主流区较窄，保留既有两列布局，不宣称真人可用性验收。
- 同一 response 重试仍只有 selected 的一个实际 Pause Run，unselected 无 Run；拒收不执行选择，删除不增加通知。
- 来源 Pause 时有两个原 Queue ID 持久接纳，fakeCLI received=0。解除 Pause 后 fakeCLI 从 stdin 实际读取两条通知（可合并一次 turn），JSON 中分别有正确 cardId/responseKind，提交有 `ANSWER-SECRET` 与 choiceId，摘要恰200字符；完整 `FULL-SECRET` body 不在 CLI 通知。两个原 ID 均进入 History 和交接账本，pending 清空。
- 重启后旧内存 Bearer401，重登仍 user；Run仍一个、通知不重投。本地 fakeCLI 确认收到回应，不代表真实模型理解或原 Job 自动继续。

## 迁移与未验收边界

- bindings 只增量加列，旧卡片可读/拒收、未绑定 choice 不能直接执行；旧 user 身份不重写，未知 recipient 不映射。既有 body、signature、response/tombstone 留原值。普通无 trigger 卡片无 Automation 依赖。
- 旧通知已 accepted 或 unknown 的缺字段内容不另造 Queue ID 补发，否则会破坏幂等。新的回应通知携 answer/choice，前一报告“通知不携 answer”的表述已由本次明确需求纠正；历史证据文件原样保留。
- Card binding 为卡片内部可信记录，无通用快照生命周期、任意 URL/shell、全局权限白名单。首次校验后的定义变化仍不会把新 Action 内容带入 effect：实际使用的是绑定副本；权限校验沿用现有 Session 管理数据与逐 effect 复核，不宣称跨文件事务或 OS 级强隔离。
- 本轮覆盖了 AgentInbox/附件契约相关既有定向测试与 Job Template/动态 Action；未运行全仓、全套原生 Steer/Illegal/lease/owner/generation 或真实 QQ/provider。Queue 原门禁未改，不以单项 Pause 冒充完整矩阵。
- 未扩展 OAuth/private exporter，未连接真实账号/模型/网关；不做真实数据迁移或上线。测试根全在自有临时 profile。最终自有进程停止、随机端口释放，恢复时另核验无残留。
- 没有移动 main/practical、push/release 或重启既有服务；原 `1b81fc66` 树未改。后续由 MA 独立复核此源码与证据，不能把 TA 实施完成等同于整合验收。
