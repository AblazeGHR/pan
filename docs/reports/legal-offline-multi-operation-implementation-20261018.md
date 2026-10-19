# 多 operation 合法性实施记录

基线 f3e051fc352fad21111cbbefc90ab2b6f63a4205；同一 review 分支，按 MA 授权三个阶段推进。只在本树实施，不合 main/practical、不 push/release、不操作既有服务或 8767/8768。设计依据为 PAN_MULTI_OPERATION_LEGALITY_PROPOSAL_20261018.md。

## 阶段一：ledger 与现有单 run 迁移

Session JSON 新增 legality_schema_version=2、operation_revision、execution_operations。每条记录保留不可复用 UUID、owner/generation、准入 revision、独立安全终态/退休证明；offline_run 保留前台兼容投影，不再独立决定合法性。terminal history/result 与 ledger/聚合仍使用同一 ordered writer 原子 replace；辅助责任不参与前台 seq/result/report。

同 owner 的所有未收尾操作一次分类、一个 carrier 保存消息成员，其余为空墓碑引用同 incident；后到 EOF 不扩大/重锁。继任 ready 仍只恢复捕获的前序批次；安全 Idle 存活 owner 仍拒第二 writer。v1 稳定 run ID 幂等迁移，v2 无 foreground 仍保持辅助责任 False。Stop 在确认 runtime 退休后按 owner 范围退休已登记责任；Illegal 继续等条件恢复。

实测：stage1-regression.xml 为 565 passed、0 failed/error/skip，48.823 秒，退出码 0；覆盖原 30 文件生命周期/队列/持久化及原生准入、完成、新聚合测试。最后两处调整（无前台指针 Stop、终态候选副本）后 stage1-final-focused.xml 为 51 passed、0 skip，退出码 0。两组重叠，不累加。首次定向测试暴露旧 Idle owner 保护缺失，已恢复并通过原用例。日志/JUnit 在 audit/legal-offline-multi-operation-20261018/。

阶段边界：此提交建立聚合基础；command/review/活动 compact/opaque 的真实入口尚未接入，不能将其描述为完整覆盖。阶段二、三继续实施。尚未运行本轮真实 CLI 或前端验证；本阶段未改前端。

## 阶段二：独立 command 与 detached review

新增独立辅助准入及事件候选提交，复用真实 stdin ACK；主 run/task_seq/current result/report 不被辅助操作覆盖。每个请求拥有随机 admission/operation UUID、捕获 owner/generation 和 connection epoch/RPC/process 元数据。多 command 可与前台并发；原 command/exec deferred final exitCode 响应才是安全终态，write/terminate ACK 不结束原命令。发送 unknown 保留责任，明确拒绝只退休该 admission。

review/start detached 原参数不改；返回 reviewThreadId/turn.id 后先持久 bind，再按完全匹配的子资源 completed 收尾。辅助 peer 和主订阅均可观察子 thread；提前完成事件有 128 项有界缓存，溢出/冲突保留 uncertain。主 accumulator 不再误吞外国子 thread，已登记子资源只开放受限只读和准确 turn continuation。未证明完成不写 True。

实测：stage2-regression.xml 为 116 passed、0 skip/failure/error，14.414 秒，退出码 0；包含 command/review 新测试及原受控准入、native completion、passthrough、聚合和真实 native keyboard/compact。随后强化 connection 元数据与 provider 前持久证据断言后 stage2-auxiliary.xml 为 11 passed、0 skip，退出码 0；组间重叠不累计。

真实本地 Codex 0.162.1 + 临时 CODEX_HOME/workspace + 本地 SyntheticResponses：command/exec 真进程写文件并正常收尾；实验能力显式 historyMode=legacy 的 thread detached review 收到准确子 thread 终态且恢复 True，provider 收请求时磁盘已 False；historyMode=paginated 明确拒绝 detached，单项安全退休，未请求模型。产品默认 history mode/experimental capability 均未改；默认新 thread 的 detached 支持受 upstream 限制，不能声称它可成功执行。测试只为验证保留可用 legacy 路径，没有用改写 inline 冒充 detached。

真实子 peer 与 kernel 均使用临时自有端口/Job，finally 关闭；未触碰既有 Pan。此阶段仍不表示 active compact、opaque passthrough、takeover proof 已完成，阶段三继续。

## 阶段三：compact/opaque 与可证明 owner 退休

活动 compact 和未知 passthrough 均在实际 peer.send 前持久独立责任、收到原 runtime ACK 后复检控制代次和 owner；保留转发与参数。空 compact 响应不猜测资源，opaque 普通 reply 不退休，状态为 uncertain，不伪造 Worker Running、不自动 Illegal/锁未来消息。API/WS summary 的 legalityDiagnostics 输出 coverage、未退休 operation ID/kind/state/reason，配置/只读和已绑定续写不误建执行任务。

TakeoverJob 新增 terminate_and_verify/close_retired 分离：真实 Job ActiveProcesses==0 后仍持有句柄，将 proof 与 owner_retirements/责任退休同次提交后才关闭和清引用。保存失败可重试；关闭失败重试核对已持久的相同 jobId receipt。新 takeover 使用唯一 ownerId；没有 Job 全后代覆盖的 legacy unknown owner 保留拒第二 writer。Safe Idle 与 external-owner-only 覆盖独立，不伪造外部执行 run。

最后复查补充：已退休/Illegal 的准入 UUID 不能重放为 uncertain/running；已发生事故的 owner 不再准入迟到新 UUID，避免下一次 EOF 扩大 cohort。继任 ready 后仅压缩已安全结束且被新 owner fence 的历史记录，保留所有 outstanding/事故墓碑。针对这些变化新增回归，未降低原断言或增加 skip。

### 实测层与证据

- 新的真实本地 legacy CLI 验证：主 turn 正在本地合成 provider 执行期间 compact 原样转发、收到 result；主 turn 安全结束后其独立 uncertain 仍令 False，确认 kernel Job 停止后才恢复 True。
- command/review 真实 local app-server peer 与 provider 验证继续通过；原真实 TUI 键盘 turn/start、idle compact、模式切换、启动失败用例也列入最终组合集。新 detached review 与 active compact 的证明入口是原生私有代理 RPC，不把它描述成实测 TUI `/review` 手势。
- Windows Job 故障注入：proof replace 失败后不释放句柄/owner；落盘成功而 close 失败后只重试相同 Job 退休，均有真实本地进程与句柄断言。辅助 bind/terminal replace 失败、迟到 generation、ACK 丢失/迟到、发送 unknown、主辅并发、通知先于绑定/冲突、事故后迟到准入均有测试。
- `final-source-manifest.json` 在冻结相关 Python 源码/测试后记录 SHA256；最终回归开始后不再修改实现/测试。所有回归经 run_tests_isolated.py，canonical Python 3.14，临时数据/端口/Job，无远端模型支出。精确最终计数见下方冻结验证结果；此前 602 全相关与 123 定向属于中间证据，不累加。

### 仍保留的明确边界

1. compact 协议没有唯一请求→资源关联，未知方法没有通用完成契约。因此它们保持 uncertain，直到可靠 scope 退休；不能声称自然结束自动恢复 True。更精确的自动收尾需 upstream operation ID/方法契约，不靠 FIFO 或 UI Idle。
2. Codex 默认 paginated thread 的 detached 明确拒绝；本轮没有改变默认 thread 模式/启用产品 experimental capability。支持路径通过真实 legacy+实验能力测试建立，UI 原生 `/review` 具体手势/导航未做浏览器或人工验收。
3. 外部 takeover 只有 lease/Job 级覆盖，外部不经受控代理的逐 turn 无事前记录；服务崩溃丢 Job handle 且 proof 未提交时保留 unknown。没有声称全覆盖，也没有自动清 unknown 的新运维 API。普通 PID/后代快照不证明任意外部脱离进程已退休，既有 A–G 该范围限制保持。
4. 未做旧 Pan 降级读取 v2 schema 的兼容承诺；部署应同时使用相应 wrapper/core 版本。未运行远端模型、远端 CI、多 Python 版本矩阵、前端 build/真实浏览器交互（本轮无 React 源码变更）。原供应商 CBC 实际模型/hook 边界保持既有报告，不把本轮 Codex 实测推广为 CBC 实测。

本轮只保留分支，未合 main/practical/MA integration，未 push/release、未重启既有 Pan、未连接 8767/8768 做测试。原审阅报告、f3e051fc 设计和归档 WIP 均保留；没有重放归档 WIP。


### 冻结验证结果与交付定位

最终冻结集：**608 passed，0 failed，0 errors，0 skipped，退出码 0，61.645 秒**；Python 3.14.5，Windows。JUnit：`audit/legal-offline-multi-operation-20261018/final-frozen-regression.xml`，完整日志为同名 `.log`，文件清单和分阶段结果见 `validation-results.json`。共 36 个相关测试文件；最终源码/测试 13 文件 SHA256 核验无漂移。此前的 565/116/602/123 等均为中间或重叠验证，不累计。三条现存第三方 warning（Pydantic incomplete annotation、httpx/Starlette、anyio deprecation）未屏蔽、未安装依赖处理。

阶段一 `0c9dcc8300b2ada4431edbdff7c19e7f60142722`；阶段二 `0e1e3e9d638cff9f85085bdc7b9abcf566232b3a`；阶段三 `b650676c3762aca51b1a4204c022a49d8313df72`；最终证据补录单独提交，不改冻结源码。最终全相关集包含 5 个真实 Codex 用例（既有 3 个真实 wrapper/native TUI 生命周期用例、新增 legacy/paginated 两个原生 peer 用例），以及 Windows Job 真实进程清理/保存故障测试。所有真实本地 helper、peer、provider、owned kernel、Job 的 finally 清理断言通过，无遗留测试服务的报告证据。

源码定位：`legal_offline.py:46/60/79/98/201/237/279`（聚合、迁移、准入、终态、cohort、条件恢复、诊断）；`worker.py:604/755/2143/2175/7340`（原子 writer、scope Stop、辅助准入/事件、Job 退休）；`session.py:1366/1573/1937`（v2 schema/迁移/序列化）；`resident_tui.py:149/173/337`（子资源关联、deferred final、active compact/opaque）；`takeover_job.py:66/84`（verify/close 分离）。行号随本提交实际文件为准，历史报告行号只对应其各自冻结基线。

最终复核 main/practical 仍均为 `5d20e72bf44f8cdb46bf8629f4772fb3b393863a`；既有 Pan PID 31556 的启动时间仍为 2026-10-18 02:28:57，仅做进程身份只读核对，没有服务操作。分支保留供 MA 独立验收和后续集成。
