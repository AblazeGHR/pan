# Locker runtime 独立复审：阻断

审查日期：2026-10-23。供 MA 独立验收。不是合并、部署或真实 provider 验收。

原候选树 `D:/project/pan-worktrees/locker-runtime-wiring-20261023` 全程只读，HEAD `e863eb16a5f80d637842e7de2bf0a52a5bf0365a`；其产品源码 `980fae907a077476e193f1aa9dd556b0ea906a24`。两者之间只有 audit 证据文件。复核相对 `ddf146bcc36039e98776e359cbdf700c05d86a84` 的整合 diff、原报告及 JUnit，未把原报告的 184 Python / 25 UI / build / browser 成绩计为本轮实测。

隔离树 `D:/project/pan-worktrees/locker-runtime-independent-review-20261023`，分支 `review/locker-runtime-independent-20261023`。局部修复提交 `747f9b14f5e43961918df8004e5f5d1994405fad`；最终源码与独立测试提交 **`939ea97c00abed1c0f43700b90c221247028a4da`**。本树仍是阻断候选，不能因局部修复直接整合。最终证据提交 SHA 由交付消息给出。

## 结论与阻断

**F2 在本轮覆盖范围内通过；F3 不能整体通过。** 不是无 Bearer 即可执行的漏洞：实际 submit 需要有效用户 Bearer；问题在于该 submit 没有绑定用户所见选项与实际 Action 的含义，也没有形成可审查的委托权限。

### B1：固定选择可执行不同的 Action/目标（未修复，阻断）

独立确定性回归 `tests/test_locker_independent_review.py::test_fixed_choice_must_not_execute_changed_action` 三个参数全部失败。先创建固定正文和选项 `Pause approved-target`，保持整个 card.definition 不变，实际 Pause 回调却收到 `foreign-target`：

1. `unpinned_trigger`：合法旧 schema 不要求 triggerRef.revision。卡片建立后，Trigger 从 pause 指向 foreign_action；用户选择原固定选项后，新 Action 执行成功。
2. `pinned_action`：即使固定 Trigger revision=1，更新同 ID Action 的 selector 不更新 Trigger revision，仍执行新目标。
3. `after_admission`：使用 asyncio.Event 屏障，等待持久 Event/Run 已接纳并到达 run_attempt，再修改 Action，然后释放屏障。执行器首次读取的是新 Action，仍执行新目标。没有靠 sleep 制造概率窗口。

源码证据：

- `packages/interactions/bridge.py:62-83` 只核对可选 Trigger revision。
- `packages/interactions/runtime.py:55-59` 授权只核对 recipient、eventTypes、dependency，未绑定生产者权限或 Action 含义；`:69-80` admission 只固定 Trigger revision。
- `packages/automation/engine.py:258-264` 到首次实际 attempt 才读取当前 Action 并建立 digest；此前卡片/事件接纳没有该约束。
- `packages/mcp/server.py:1934-1958` 只检查卡片 ownerSession 的 self/managed 权限，嵌套 triggerRef 不作目标权限核验。
- `packages/web/src/views/LockerView.tsx:84-90` 展示生产者提供的 label，没有可信解析得到的 Action/目标/参数含义。

真实 HTTP/MCP 补测还证明：restrictToManaged=true、canClaimUnmanaged=false 的来源 Agent 对非 managed 目标 `agent_notify` 被拒，但能创建引用可操作该目标 Trigger 的卡片；用户 submit 后 Pause 实际运行。该事实不等于无需用户授权；它说明当前实现把任意生产者 label 的 submit 直接当作 Trigger 的执行授权，不能证明用户批准了实际动作。不能以“Trigger 之前被启用”或“卡片不可变”代替这一绑定。

必要后续：将服务器可信解析的 Trigger/Action（包括参数绑定与目标选择含义）绑定至用户实际批准的选项，并让首次执行和恢复保持该约束；明确受限生产者能引用哪些用户授权能力。卡片建立、批准、持久 admission、首次 attempt 之间的变化均需拒绝或保留原获批含义。仅把 revision 改为必填、仅校验 Trigger、或只在 accept 前取一次 Action digest 都不能关闭以上三个屏障。为避免在独立审查里自行规定新的委托权限/UI 语义，本轮未改 Job/Trigger/Action 执行器，也未用临时白名单伪装修复。

### B2：unknown 遇 Trigger 停用无法查原回执（局部已修复）

原候选在 EventStore 已 commit、Locker receipt 未提交的故障后保留 unknown；随后停用 Trigger，dispatch 先跑当前授权而报 forbidden，不能读到原 accepted receipt。删除后的 detached delivery 同样存在该问题。

局部修复只修改 `packages/interactions/store.py`：持久状态 in_flight/unknown 只查其原 eventId 回执，不再依赖当前 Trigger 授权；新 admission 仍需 authorize。当前/已删除 × 回执存在/不存在四种独立测试均通过：存在则 accepted 并停止查询，不存在则继续 unknown；禁止 authorize/accept/append，重复恢复仍只查询同一 ID。没有新增重投或跨文件事务承诺。

## 本轮独立实测

Python：`D:/project/Pan-main/.venv/Scripts/python.exe`，测试始终由 `scripts/run_tests_isolated.py` 清除继承 PAN 配置并选随机端口。所有 storage fixture 指向自有临时路径。精确 argv/exit 与文件散列见 `commands.json`。

| 证据 | 结果 | 含义 |
|---|---|---|
| red.xml / red.log，原 e863 产品源码 | exit 1；4 failed | 初版三项 Action 漂移 + 一项 Trigger 撤销后的回执失败 |
| green.xml / green.log，局部修复后早期子集 | exit 0；32 passed，3 deselected | 四项回执组合和原定向回归；deselected 是单独保留的安全阻断，非 skip |
| final.xml / final.log，939ea 源码 | **exit 1；33 passed，3 failed，0 skipped** | 最终完整定向结果；三项屏障仍阻断，没有放宽断言或 xfail |
| http.xml / http.log / http-evidence.json | exit 0；1 passed | 真实独立 HTTP + stdio MCP + fakeCLI + 冷重启 |
| ui.xml / ui.log，939ea 源码 | exit 0；10 passed | Locker 表单/错误恢复与内存 Bearer 请求定向测试；不是本轮浏览器验收 |

最终 Python 命令：

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_locker_independent_review.py tests/test_interaction_runtime.py tests/test_interaction_cards.py tests/test_user_locker_lifecycle.py -o addopts= -q -s --junitxml=audit/locker-independent/final.xml
```

HTTP 命令：相同 wrapper，pytest 参数为 `tests/test_locker_independent_http.py -o addopts= -q --junitxml=audit/locker-independent/http.xml`。HTTP 在源提交前的相同最终产品/HTTP 测试文件上运行；其后只新增了旧用户迁移测试，没有修改 HTTP 或产品代码。

UI 命令（cwd=packages/web）：`D:/node_npm/node_global/pnpm.cmd exec vitest run src/views/lockerModel.auth.test.ts src/views/LockerView.test.tsx --reporter=default --reporter=junit --outputFile=D:/project/pan-worktrees/locker-runtime-independent-review-20261023/audit/locker-independent/ui.xml`。仅复用既有 node_modules 的本地 junction，未安装共享依赖。

`git diff --cached --check` exit 0。`raw-logs.zip` 保留原始 log/JUnit 字节，供核对；可读副本只清理行尾空白，通过/失败与断言均未修改。`finalize_evidence.py` 生成命令、JUnit 属性和 SHA256 清单，不执行产品或测试。

HTTP fixture 的三次失败实验完整保留：fixture-failure 是最初未将来源设为 restrictToManaged，projection-failure 是误认为 Queue HTTP 投影含 envelope，batch-failure 是误认为两条通知必为两个 CLI turn。修正测试以实际已存在的权限、队列投影、批量交接契约断言；最终仍严格断言两个原 Queue ID、每种通知各出现一次、正文/answer 不泄漏和无冷启动重放。它们不是产品缺陷的修前证据。

## 通知持久接纳与真实消费

最终 HTTP 使用随机端口 **51362**，data root、Session/config/Inbox/gateway manifest、fakeCLI 路径与 PID 均先核验，见 http-evidence.json。QQ manifest 为空，CBC argv 被本地 fake_stream_cli 替换。

- 来源 Queue 暂停时：提交/拒收分别持久入队，共两个稳定原 Queue ID；fakeCLI received=0。这只证明通知持久接纳。
- 用户提交两次同一 requestId：一个 directed selected Trigger Run，实际 Pause 目标 Queue；unselected 没有 Run。Reject 带 choice 定义也不产生 Run；delete 不增加通知。
- 解除来源 Pause 后：fakeCLI 确实从 stdin 读取两种通知，各一次；Queue 合并成一次 turn。Chat History 保留两个原 queueItemIds，queue_delivery_ledger 保留两个投递回执，pending 清空。这里可以声明本地 fakeCLI 已消费通知，不能声明真实来源模型已理解或完成业务。
- 通知 summaryBody 为 200 字符，未携带完整 body、answer、附件。按既有明确文档，200 是通知投影上限；固定 card.definition.summaryBody 仍可长于 200（schema 上限32000）。如果 MA 将“summaryBody<=200”解释为卡片存储字段也必须限200，则该更严格要求尚未满足；本报告不隐去这一区别。
- 冷启动后旧内存 Bearer 返回401，原 key 重新登录后仍对应 profile `user`，Run=1、原通知不重投。进程已清理，随机监听已释放。
- 当前 CLI 的 legacy notice 文本格式含 `unknown` / `sessionId:null`，cardId 等结构在持久 envelope/History 原项中，但不全投影到 CLI 文本。未在本轮扩展格式；不能宣称来源 Agent 已通过该通知完成按 cardId 检索/继续 Job 的业务闭环。

## 迁移、恢复、权限与未验收矩阵

| 项目 | 状态/边界 |
|---|---|
| UserAuth 内存 Bearer → 本 profile user；无/错凭据401 | 实测通过；复用原 key/login/logout，不扩展认证体系 |
| 其他 recipient GET/read/submit/delete 返回404 | 真实 HTTP 实测；不推测映射未知身份 |
| 旧 user 开放/完成记录、未知 recipient | 独立临时 SQLite 副本迁移实测；原文件 SHA256 不变、逐行内容不变，旧完成消息不追补通知 |
| 一次选择、定向真实 Pause、重启无再投 | 本地 fakeCLI HTTP/MCP 实测通过 |
| unknown 回执；删除 detached 回执 | 原候选失败；局部修复四组合通过；缺回执保持 unknown |
| 固定选项与 Action 含义/受限 Agent 委托权限 | **阻断，3 个确定性红例；没有整体修复** |
| reject 不执行 choice，delete 不通知，通知200字 | 本轮定向及 HTTP 实测通过，截断定义见上文 |
| User Inbox 与 Chat 独立、页面 Locker、聚合纯视图、read/unread | diff 静态复核 +10 UI单测；本轮没有重做真实浏览器验收 |
| Agent Inbox 无独立 GUI，QQ/Locker/Queue 原语义 | 本轮产品修改仅两处 receipt reconciliation；原 QQ/Agent 路由未改。本轮未重跑全套 QQ、附件、容量、多订阅恢复 |
| Illegal/lease/owner/generation/native Steer 全 gate 矩阵 | 未重测，不以 Pause 单项代替全部；Queue 原路径未更改 |
| OAuth/private exporter/真实账号、模型、QQ网关 | 未访问，未验收 |
| Job/Template、定时任务与真实执行器组合 | 原 diff 和证据静态审查；本轮未扩展其业务、未重新跑全套；不存在新 Job wait/resume 承诺 |
| build/真人 GUI/真实 provider 结果消费 | 本轮未运行；原候选证据属于原作者，不转记独立通过 |

未移动 main/practical，未 push/release，未连接8767/8768或真实 profile/服务；仅停止自有进程。原候选最后核验仍为 e863、clean。局部修复候选交付并不关闭 B1，MA 应以本报告的 **阻断** 结论处理。
