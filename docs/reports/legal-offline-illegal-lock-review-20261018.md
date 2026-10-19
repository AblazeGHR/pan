# Legal Offline / Illegal Lock 原任务文档审阅

> 本文是最初只读审阅的历史快照。用户随后扩大授权，实施、技术修订与最终验证另见 `legal-offline-illegal-lock-delivery-20261018.md`；原文中的“本次未实施/未提交”等描述仅指最初审阅阶段。

日期：2026-10-18。结论：方向可实现，但当前文档尚不适合无澄清直接实施。发现 6 项 P1、5 项 P2；主要障碍是生命周期定义和现有契约边界未闭合，并非要推翻用户已决定的单锁模型。

## 审阅对象、环境与证据边界

- 原始文档：`D:/project/Pan-main/docs/design/PAN_LEGAL_OFFLINE_ILLEGAL_LOCK_AGENT_TASK.md`，完整读取 1–157 行；SHA256：`C4055322C9162446C833BBDAFE351CEFA6F8FDA88C7E69D94FE6978538C2D6A9`。以下 `原文 Lx–y` 均指此文件。
- 审阅 root：`D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`；分支：`review/legal-offline-illegal-lock-20261018`；HEAD：`5d20e72bf44f8cdb46bf8629f4772fb3b393863a`。开始时工作树干净。
- 只读核对 Pan-main HEAD 也为该基线。原始任务文档在 Pan-main 中是未跟踪文件，本审阅树没有该文档；因此直接审阅用户指定的原件，未用其他副本代替。
- 已读本树 `CODEBUDDY.md`、Pan skill、`docs/TESTING.md`；检查工作树祖先、原文目录祖先及报告目录，未发现适用的 AGENTS.md。
- 未执行原文 L3、153 的实施指令。未修改产品、测试、配置、原始设计，未安装依赖、build、启动真实服务、访问或操作 protected 8767/8768、合并、push、restart。本树唯一交付文件是本报告；未创建提交。
- **实测**仅包含 Git/文件核验及下述 5 个纯函数探针。**代码推断**表示沿基线源码及已有测试阅读得到的结论；文内“复现情景”是待实现后执行的故障序列，除明确标注探针外没有实际运行。**假设/待澄清**表示现有证据不能替用户选择的产品语义。

## 必须保留的决定

保留独立 `legal_offline`；False 正常运行不等于 Illegal；正常 Stop、安全 Restart、正常完成 Idle、当前被正常处理的 Error→Idle 合法；只锁快照中的已有未锁消息；manual/illegal 是同一个有效锁的互斥来源；手动 Lock 可转 Manual、手动 Unlock 可清除任一来源；自动恢复只清除对应批次仍为 Illegal 的锁；不重锁手动释放的项、不扩大同一批次、不新增持续锁未来消息的消费门禁。

下文建议只补充这些决定的边界与可执行条件。涉及现有 Stop reports、native ownership、服务恢复偏好的冲突，明确列为需确认的映射，不擅自删除已有功能。

## P1-01：受保护的“运行”未定义，Idle 合法与进程停止条件直接冲突

**原文证据：**L7、12–13、30–38、119、126–127。L36 要求合法 Idle 写 True，并要求写 True 前“运行已停止”；L38 又笼统要求 CLI 被强杀/崩溃可识别 Illegal。

**源码证据：**`packages/core/worker.py:6720–6745` 区分 oneshot 与常驻 stream，并在已启动进程上创建 Idle Worker；`:1351–1385` 在任务终态提交时记录 Idle；`:1431–1440` 发布 Idle；`:5964–5999` 下一轮复用同一 stdin 进程；`:1465–1470` 的 `_runtime_stopped` 要求 tracked process returncode 已确定。

**代码推断：**Idle 是任务安全边界，不等于 CLI 进程死亡。若“运行停止”解释为 `_runtime_stopped`，常驻 stream 正常完成不能写 True，随后 Pan 重启会误判原本 Idle；若 Idle 写 True，之后空闲 CLI 被外部杀死，恢复时单靠该 bool 又不能证明进程遭遇非法退出。这不是多加几个状态回调可解决的问题。

**复现情景：**stream 任务完成→CLI 仍活→按 L36 写 True→空闲时外部 kill 或 Pan 崩溃→再次启动；两种实现分别产生误报或漏报，且都能声称符合当前文字。

**最小文字修订建议：**“`legal_offline` 保护一次可恢复执行单元。合法 Idle 指当前执行单元已完成安全提交，允许常驻 CLI 存活；进程 Stop/Restart 的合法收尾另须确认相关物理运行实例停止。下次受保护执行开始前重新持久化 False。”同时请明确：正常 Idle 后的空闲 CLI 死亡是否单独算 Illegal；若必须算，需额外独立的进程所有权事实，不能要求同一 bool 同时证明任务结束和进程合法退出。

**验收：**stream 同进程两轮、oneshot 两轮、启动后尚无任务的 Idle、正常完成后的 Idle、Paused 导致的 Idle 分开测；每个阶段崩溃恢复均有明确预期，不能仅测试 worker.status 字符串。

## P1-02：现有 generation/workerId 不能提供跨重启幂等，回调必须捕获旧身份

**原文证据：**L30、62、77–79、83、98、122，仅建议复用身份/代次，未指出当前身份的持久性缺口。

**源码证据：**`packages/core/worker.py:308–348` 的 generation 与 Session 索引都在进程内；`:571–581` 从当前 registry 分配最小可用 `worker-N`；`:7441–7445` Restart 复用对象并修改其 generation；`:9571` shutdown 清空 generation。`_steer_run_id` 在 `:315`、`:1932–1938`、`:6019` 维护，不能当成所有 adapter、执行模式都有的持久运行主键。

**实测：**抽取原函数执行，清空 registry/generation 模拟新运行环境，前后都分配 `worker-1 / generation 0`。

**风险/复现情景：**上一代 False→异常批次已提交→Pan 再次重启→新进程用相同 workerId/generation 重识别或错误关联；同一异常重新锁 D，或新一代状态清除不属于它的批次。进程内还有一种独立风险：旧协程持有可变 `w`，await 后读取的是 Restart 已更新的 generation，表面检查“当前 generation”仍可能通过。

**最小文字修订建议：**“在开始受保护运行前，将不可复用的持久 run token（或持久 epoch+序号）与 False 同次提交；异常事件以失败 run token 为稳定幂等键。回调在发起时捕获 token、generation、process/所有权对象，提交前验证当前所有者和捕获值；不得在回调末尾从可变 Worker 对象补取身份。”这不要求引入庞大事件系统。

**验收：**服务重启后 workerId/generation 相同；同 Worker 对象 Restart；旧终态、旧 stdout、旧定时任务在新一代运行后到达；不同 adapter 没有 runId。均不能重复建批次或误解锁。

## P1-03：异常快照、开始新运行和持久化失败之间缺少可执行的准入顺序

**原文证据：**L36、38、77–79、98、115、118、121。文字要求原子/幂等，但只明确“新增锁+归属”和“解锁+已恢复”两组原子性，没有明确失败 run 的已处理标记与锁快照的同一提交边界。

**源码证据：**`packages/core/session.py:2392–2480` 使用每 Session JSON 元数据、history JSONL 和 `os.replace`，不是现成 SQL 队列事务。`packages/core/worker.py:635–663` 保存失败返回 False；`:5998–6024` 及 `:6160–6162` 的旧调用未检查返回 bool 后再交接。`packages/web/server.py:218–221` 捕获启动恢复初始化错误后继续，`:248` 仍启动 watchdog。`:6669–6792` 的 Worker 建立及 consumer 启动存在多个可调度 await。

**代码推断/复现情景：**①复用旧 helper 返回值模式，False 落盘失败仍写 stdin，随后崩溃没有异常证据。②先保存“此 run 已处理”再保存锁，第二步失败导致永远漏锁；反向顺序则重启重做快照锁到新 D。③启动初始化失败后 watchdog 或用户发送先恢复消费历史消息。④另建 batch DB，而锁留在 Session JSON，不能凭单边事务获得跨文件原子性。

**最小文字修订建议：**“每 Session 按既有 spawn/lifecycle 与 queue 串行边界完成：核验旧 owner→恢复队列投影→同次原子提交失败 run 已判定标记、批次、精确成员与锁→准入新 run 并提交新 token/False→允许 provider 写入。开始写入 False 失败必须拒绝交接；判定/锁提交失败则该 Session 不进入新运行或消费，允许重试并暴露失败。该临时初始化屏障不是持续锁未来消息。默认把关键事实放在同一 Session 元数据提交；跨存储必须说明额外协议。”

还应明确线性化点：锁定快照在取得 queue exclusion 后形成并提交；提交失败前不能对外宣告 Illegal 锁已完成。进程崩溃后只能承诺恢复至最后已提交的快照，不能承诺恢复未落盘的内存 ID 集合。

**验收：**在 False 写入、批次提交前后、已处理标记、consumer 启动、writer cancellation 处注入失败；证实写 stdin/spawn 次数、落盘内容、重试批次 ID 和成员集合。初始化失败时其他无关 Session 可继续，失败 Session 不抢先消费。

## P1-04：当前锁模型超出 manual/illegal，直接套用会改变 Stop reports 或重放未知交接

**原文证据：**L15–19、49–68、104–109、117、144–145，假设可消费性由单锁决定，迁移主要描述旧 `locked: bool`。

**源码证据：**`packages/core/worker.py:3259–3279` 的有效 locked 是 `queueLockManual OR queueLockAutoReport OR forceSendHeld OR queueSteerPending OR selfConsumeUncertain`，另有 Pause/native ownership/edit lease。`packages/web/server.py:8411–8435` 的 Stop reports 可加/去当前自动报告锁；`worker.py:4741–4747` 对未来报告自动加锁。手动 Unlock 在 `server.py:8449`、`:8477` 只清 manual/auto-report，不清 unknown/force hold；`:7996–8003` 对外分别投影 locked、lockManual、lockAutoReport。

**实测：**四个非 manual 字段分别都能使当前 `_queue_item_locked` 返回 True；edit lease 单独阻止交接。

**复现情景：**①把旧 auto-report 迁成 Manual，恢复 reports 时沿用旧逻辑已无法解除，或误清用户 Manual。②把 Steer unknown 当普通来源锁，Unlock/自动恢复清除它，已被 CLI 接收的同 ID 消息可能重投。③把所有阻塞压为单 bool，解锁后绕过编辑 lease/native ownership。

**最小文字修订建议：**“manual/illegal 的单锁只约束本功能的可撤销消息锁；现有交接结果未知、force hold、编辑 lease、Pause 和 native ownership 的安全条件保留，解除消息锁不等于可交接。禁止把 unknown 转 Manual 或随 Unlock 清除。”另必须给出 Stop reports 的明确状态转换表：如何映射当前 auto-report、如何在单锁下表达用户 Manual 覆盖、恢复 reports 时释放哪些项。若坚持全系统仅两种来源，又要求 Stop reports 的现有释放行为完全不变，需要澄清允许保留的策略归属元信息；不能悄悄删功能或改为双层 manual/illegal 锁。

**验收：**当前 report 锁、未来 report 锁、报告锁+手动 Lock、恢复 reports、Unlock 后又收到新报告、Steer/self-consume unknown、force hold、编辑 lease、Pause/native ownership 的交叉矩阵；原 ID 不重复投递。

## P1-05：Lock All 的准确范围与 crash 队列归一化顺序未写明

**原文证据：**L14、24、67、75、118、120、146。既要求复用现有范围，又用“发生时已有消息”描述快照，未定义物理死亡时刻、检测时刻还是持久提交时刻。

**源码证据：**`packages/web/server.py:8447–8453` 当前 Lock All 只处理该 Session 的 `queue_pending`、dict、deliveryState=queued；不是全库、全 Workspace 或 ledger。`packages/core/worker.py:4118–4140` 归一化失主 reserved/writing/in-flight；`:4163–4188` 可从 ledger 恢复 pending。`server.py:8152–8179` 的 queueAllLocked 只统计公开投影、queued、按 ID 去重，且空队列为 False。它是摘要，不是永久全锁开关。

**代码推断/复现情景：**A 在 reserved 时崩溃，B 在 pending queued，C 只在 ledger。先 Illegal lock 再恢复，只锁 B；A/C 恢复后逃出保护。反之，如果把所有 ledger 条目都锁，会错误处理 sent/deleted。CLI 死亡到 EOF 检测前并发加入 D，当前持久记录不足以还原精确死亡时的队列快照。

**最小文字修订建议：**“本事件范围为所属 Session 在异常判定的 queue 串行边界、完成既有失主交接对账后，按现有 Lock All 选择器得到的 queued 集合；sent/deleted 不复活，有效在途 owner 不撤销。后续新入队以该线性化边界为准。queueAllLocked 仍是非空公开 queued 投影每项有效 locked 的派生摘要，不由 Illegal/False/批次存在设置。”若用户要求物理死亡瞬间快照，需先确认额外持久序号/时间证据范围，不能靠 wall-clock 猜测。

**验收：**queued/reserved/writing/in-flight/ledger-only/sent/deleted/unknown 混合、重复 ID、无效 shape、不同消息来源、同 Session 与其他 Session；快照期间并发入队/删除/编辑；D 入队后 queueAllLocked 立即变 False，空队列始终 False。

## P1-06：native 外部执行和脱离进程的生存性不能由当前回调/registry证明

**原文证据：**L24、26、36–38、83、98。要求真实开始前 False、恢复时识别仍有效的实例，但没有 native/takeover 的实施边界。

**源码证据：**`packages/core/adapters/codex/app_server_wrapper.py:1432–1437` 收到 provider `turn/started` 后才发布 `pan.native_turn_started`；`packages/core/worker.py:1981–2006` 收到通知后才保存 running。`packages/core/adapters/cbc/native_bridge.py:180–184` 也从 native submit 通知执行。`worker.py:5581–5588` 明确保留 native 用户持有/输入不确定的 kernel；`:6933–6972` takeover 另启 TUI、Pan Worker 转 held。`:319–330` registry 是内存对象，服务重启后空 registry 不证明旧原生进程已退出。

**代码推断/复现情景：**用户从 native 直接开始 turn→provider 已执行→Pan 尚未收到通知即崩溃，此时上一轮 True 没有变 False；恢复可能漏判。另一方向：Pan 失联但 native CLI 仍有效→只因 registry 无 Worker 判 Illegal、再 spawn 同 thread，可能产生第二写者。这里未实测真实 native 脱离后的 OS 行为，属于必须验证的边界，不断言必然存活。

**最小文字修订建议：**“native/takeover 必须明确受保护执行开始前的持久准入位置；仅收到事后 started 通知不足以证明事前 False。如最小方案是在交出 native 执行权前标记风险运行，则写明它与正常 Idle 合法的关系、终结条件。服务恢复用可验证的 owner 身份/存活证明区分有效、已死、未知，未知不得自动创建第二 writer；浏览器 detach 或控制权切换本身不是进程结束或恢复事件。”无法覆盖时必须列为不支持边界，不能宣称全模式满足 L36–38。

**验收：**native 输入前后、started 通知前后杀 Pan；浏览器关闭/重连；held takeover；provider 仍活/已死/身份不确定/PID 复用；保留原 thread、原队列身份，无双 writer、无未知输入自动重发。

## P2-07：全锁后现有自动恢复不启动 Worker；“有效 Idle”可能又过早解锁

**原文证据：**L7、17、83、98、100、129、147。指定 Worker 回到 Running/Idle 才解锁，但没有定义谁启动它、何种 Idle 为有效。

**源码证据：**`packages/core/worker.py:3282–3301` locked-only 队列阻止恢复；`:5652–5656` 即使 force 恢复也退出；`:6741`、`:6761–6763` 新 Worker 一开始就是 Idle，`:6755` 启动 stdout task，不等同于 provider 已经验证 resume/thread 可用。`:2055–2062` init 仅提取元数据。

**实测：**全锁 A 时 `_queue_recovery_blocked=True`；追加无锁 D 后为 False，选择器直接选中 D。

**复现情景：**Illegal 锁完所有项→自动恢复被旧 gate 阻止→没有 Running/Idle→锁一直存在。另一个极端是把构造 Idle 当恢复，spawn 成功但 provider 随即 resume 失败，消息已提前解锁。

**最小文字修订建议：**明确选择：“维持现有全锁不自动唤醒，用户显式 Start/Restart 或后续未锁入队才建立新实例，届时恢复批次”；或“允许一次只为建立有效实例的恢复启动，仍不交接锁项”。两者均可保持不锁未来消息，需用户确认预期。定义 adapter/mode 的有效恢复证据，排除构造值、旧 replay、失败 spawn；不能只依赖 UI 状态。

**验收：**只有 Illegal 锁、只有 Manual 锁、混合锁、空队列、有新 D；spawn 返回 handle 后立刻失败、oneshot 无常驻 CLI、Paused/native gate。确认恢复触发和自动解锁时点可观察且幂等。

## P2-08：正常 Error 合法应有原因优先级，不能被底层 kill/EOF 或早期 True 覆盖

**原文证据：**L13、36、38、40、127。明确 Error→Idle 合法，同时笼统写 CLI 强杀/崩溃非法；未给 shared kill helper、watchdog、执行前错误的优先级。

**源码证据：**`packages/core/worker.py:1777–1793` 执行前失败也汇聚统一终态；`:6228–6237` spawn 失败走该路径；`:6417–6444` oneshot timeout、非零码等被捕获后进入统一 error 终态。`:5600–5615` watchdog 调用 `kill_worker(recover=True)`，`:7044–7058` 共享 kill 在 history flush 之前记录 offline；`:7470–7471` Restart stop 也记录 offline。`:1351–1383` 终态则已有提交失败回滚及 DurableOutcome 区分。

**代码推断/复现情景：**通用 EOF/非零码先触发 Illegal，随后正常 error 终态又 True，制造不必要锁；反向若在共享 kill 一律写 True，会把 watchdog 中止尚未完成执行当合法。Stop 提前 True 后 history flush 失败/崩溃也违反 L36、121。

**最小文字修订建议：**“集中策略接收显式 operation/reason、受保护 run、终态是否安全提交和停止证明；已捕获且安全落盘的 Error→Idle 按用户决定合法，不能被其底层退出码追溯改判。正常 Stop/Restart 在全部必要安全收尾成功后提交 True；watchdog 的异常中止、未处理 EOF、收尾中断不得仅因复用 kill helper 而变合法。进程已被终止与受保护运行已合法结束分别核验。”

**验收：**当前所有捕获→Idle 的 Error 保持合法；Error 持久化中崩溃非法；Stop/Restart/timeout/idle reclaim/外部 kill 分开测，终态、报告、history flush 的失败点不能提前 True。

## P2-09：迁移中的“保守安全策略”没有可验收含义，历史证据不足时无法两全

**原文证据：**L34、42、104–111、137。缺失字段不能当 False；不能简单从旧状态复制；又要求可靠证据初始化与不误判，没有给不确定数据处理结果。

**源码证据：**`packages/core/session.py:1358`、`:1544–1547`、`:1881` 只有旧 legal-state 元数据；`packages/core/worker.py:598–603` 本来刻意不在 EOF 更新旧 ledger；`tests/test_session_legal_worker_state.py:70–101` 明确验证空闲 Worker EOF 后旧字段仍 idle。`session.py:1593–1612` 还有旧 Pause reports 的一次性迁移；`:1836` 用版本缺省控制该迁移。

**代码推断：**历史 idle 不能证明物理 CLI 正常退出，历史 running 也可能来自用户显式设置或正常服务退出选择。没有额外证据时，“历史异常零漏判”和“正常 Idle 零误判”不能同时保证。

**最小文字修订建议：**给出 missing/null/非法类型、可信终态、明确未结束 run、证据不足的迁移表；未知不得臆造失败 run 或批次。推荐对未知保留迁移不确定事实并保持原可消费性，之后首次新执行前建立正式 token/False；若用户选择未知一律锁定，需明确这与“不改变已有可消费性”的例外关系。迁移版本与新字段同次持久化，只补缺，不覆盖已写入的新批次或手动解锁。对旧 manual/auto-report/unknown 的迁移采用 P1-04 的映射。

**验收：**新 Session、历史 idle/running/无字段、手动 set-running、旧 Pause reports、损坏类型；迁移两次、迁移中崩溃、迁移后手动解锁再重启，均不覆盖新数据或扩大集合。

## P2-10：旧字段仍驱动 UI、启动策略和定时恢复，不能只迁移 Worker 内部

**原文证据：**L11、24、26、111、142、149。允许旧字段保留其他用途，但未明确“其他用途”名单及与新 Illegal 的关系。

**源码证据：**`packages/web/src/utils/sessionFilters.ts:103–106` 的 Illegal 筛选和 `components/worker/WorkerDot.tsx:26–38` 的红色状态仍比较旧 legal running 与实时状态。`packages/web/server.py:5486–5555` 启动候选/恢复策略依据旧字段；`:621–634` 主服务操作先快照旧 running；`:3717–3727`、`:3825–3833` 正常 Restart/Exit 可选择 preserve-running。`packages/core/worker.py:9561–9567` 会在物理停止后恢复旧 running 值。`packages/core/background_jobs.py:2155–2163` 定时 resume 同样用旧字段。Details 显式 set-running 在 `worker.py:666–684` 不要求真实 Worker 运行。

**复现情景：**新机制正确记录合法 Idle/True，但旧字段仍 running，UI 继续显示 Illegal。正常退出 preserve-running 后，如迁移把它转换成 False，下次正常启动会误锁。定时恢复仍按旧候选启动，在新判定事务之前交接队列。

**最小文字修订建议：**列出必须本次迁移的异常展示/筛选消费者及服务端 Illegal 投影；运行时展示不再自行比较旧状态。旧“恢复意图”可保留独立语义，但不参与新合法性判定；正常安全退出写 True 与 preserve-running 的“下次继续”意图并存。启动偏好和定时 resume 必须经过新 Session 初始化边界；Details 操作不得伪造真实执行或触发恢复解锁。无需为了消除旧字段重构无关功能。

**验收：**HTTP list/detail/WS、WorkerDot、Illegal workspace filter 一致；ask/wake-running/preserve-running、markRunningSessionsOffline 两种选择、定时 resume、Details set-running。正常服务操作不产生新 Illegal，真实异常不被旧字段刷新掩盖。

## P2-11：多批次归属与手动操作的持久顺序需补定义，尤其不能清掉旧批次后又从 ledger复活

**原文证据：**L7、17、60、75、83、96–98、116、133–136。“对应 Worker”“该次 Illegal”“不同批次不交叉清除”均成立，但未定义旧 Worker 已结束后由哪个新 Worker 恢复哪些未恢复批次。

**源码证据：**`packages/web/server.py:8192–8232` 手动锁在 queue exclusion 下同时维护 pending、ledger、queue_revision，并失败回滚；`:8820–8847` 编辑保存合并当前目标与 ledger，编辑 lease 独立。`packages/core/worker.py:4163–4188` 可从 ledger 重新建立 pending；`:4289–4334` 保存存在已提交但调用被取消的情况。当前不能照 L85–93 的语义循环分别写消息和 mark recovered。

**复现情景：**E1 锁 B；E2 发生时 B 已锁而跳过，E2 只锁 C。新有效实例出现：若只恢复最新 E2，B 永久留在 E1；若无 owner 约束全清，则可清掉当前运行之后新产生的 E3。另若自动解锁只改 pending、不改 ledger，后续恢复可把旧 Illegal 元数据带回；手动 Unlock→Lock/删除→旧回调可能覆盖新操作。

**最小文字修订建议：**“批次锚定失败 run，恢复实例属于同 Session 的已验证继任关系；明确一次有效恢复是解除所有先于该恢复边界、已确认终结的未恢复批次，还是逐一关联，二者选定一种。新异常不转移已有锁归属。手动转换/解锁与批次恢复共享 queue exclusion，条件更新当前来源+batch+owner，统一提交 pending、可恢复 ledger、批次状态、queue_revision；失败回滚全部候选，已提交后的取消不回滚或重做。编辑保存只改正文/parts/revision，保留最新锁归属和 lease；解锁不等于释放 lease。”

**验收：**E1(B)→E2(C)→有效继任；在恢复前/中/后插入 E3；重复 E1、手动 Unlock B→Lock B、Manual Lock All、删除、编辑保存/lease过期、取消且提交成功/失败。磁盘重载并从 ledger恢复后锁仍正确；旧 queue.snapshot 不能覆盖较新 revision。

## 补充验收清单及既有测试边界

| 场景组 | 最低必要断言 | 现有可复用测试入口（仅阅读，未运行） |
| --- | --- | --- |
| stream / oneshot / native 生命周期 | False 在实际执行前提交；Idle 不要求常驻 CLI 死亡；下一轮重新 False | `tests/test_session_legal_worker_state.py:28`、`:70`；`tests/test_native_control_api.py` |
| 正常完成 / 捕获 Error / Stop / Restart | 安全收尾成功才 True；正常捕获 Error 不被追溯判 Illegal | `tests/test_session_legal_worker_state.py:217`；`tests/test_main_lifecycle_jobs.py` |
| EOF / watchdog / Pan 崩溃 | 同失败 run 只建一批，先保护再调度；正常 Idle 不误判 | `tests/test_session_legal_worker_state.py:70`；`tests/test_unified_server_queue.py:609` |
| 持久化失败 / 取消 | False 失败零交接；批次和锁无部分提交；已提交取消按 DurableOutcome | `tests/test_self_queue.py:251`、`:277`、`:435` |
| 范围 / 混合 delivery state | 仅目标 Session；失主对账顺序；sent/deleted 不复活；D 不被旧批次锁 | `tests/test_unified_server_queue.py:163`、`:385`、`:609` |
| 单锁 / 手动转换 | illegal→manual、Unlock任一来源、Lock All 转 Manual；旧自动恢复不得覆盖 | 需新增新来源与批次断言，不能仅复用旧布尔锁断言 |
| Stop reports / 未知交接 | 报告策略保留；unknown、force hold 不因 Unlock/恢复而可重投 | `tests/test_self_queue.py:151`、`:307`、`:537`、`:562` |
| edit lease / 并发入队 | 自动锁不取消编辑；保存保留最新来源；过期后按最终锁交接 | `tests/test_unified_server_queue.py:209`、`:279`；`tests/test_queue_send_latency.py:210` |
| 多批次 / 迟到回调 | 跨重启稳定 run；可变 Worker 旧回调拒绝；E1/E2/E3 不交叉误清 | `tests/test_self_queue.py:346`；`tests/test_queue_send_latency.py:244`；另加新批次测试 |
| 全锁与唤醒 | 全锁是否启动按选定语义；新 D 可唤醒；Pause仍优先；无隐含永久 gate | `tests/test_delayed_restart_locked_queue_http.py:54`；需新增 Illegal 恢复测试 |
| 迁移 / API / UI | 缺失未知不猜；重跑不覆盖；queueAllLocked 空=False、D加入=False；list/detail/WS 一致 | `tests/test_session_legal_worker_state_api.py`；`packages/web/src/components/worker/WorkerDot.test.tsx`；`packages/web/src/utils/sessionFilters.workspaceSearch.test.ts` |
| native / surviving process | started前断电窗口、detach、held、PID复用、owner未知，无双 writer | `tests/test_codex_takeover.py:97`、`tests/test_cbc_native_bridge.py:537`；真实 OS 路径待独立隔离验收 |

原文 L124–138 的 13 个场景覆盖了方向，但不应把它们全删重写：在其下补充上述模式、失败点、持久身份和并发顺序即可。用例必须断言原消息 ID、真实 provider handoff 次数、来源/批次、落盘重载结果和通知 revision；仅断言内存 bool 或 WS 状态不足以验收。未来需遵守 `docs/TESTING.md` 的临时数据根和端口隔离；本次不运行真实服务测试。

## 本次实测记录

使用当前 Python，以 `python -B -` 运行内存脚本：标准库 AST 解析 `packages/core/worker.py`，仅编译抽取的原函数，注入轻量 Session 和无 native ownership 的上下文。不导入应用模块，不启动 provider/服务，不写持久化、不生成测试源码或字节码。依次验证：

1. manual 锁唯一 backlog 阻止 `_queue_recovery_blocked`。
2. 追加无锁 D 后允许恢复，`_select_queue_unit` 选择 D，越过锁 A。
3. edit lease 未表现为普通消息锁，但独立阻止选择。
4. auto-report、force hold、Steer pending、self-consume uncertain 分别参与 locked 投影。
5. 清空内存 registry/generation 后，`_next_worker_id/_next_worker_generation` 复现相同 `worker-1 / 0`。

**结果：5/5 通过，退出码 0。**这些探针证明当前函数分支，不证明拟议实现、DB/磁盘事务、竞态调度、UI或真实 provider 行为通过。未运行 pytest、build、真实服务或 native OS 崩溃实验。

## 实施前必须补定的决定

1. 受保护执行单元与空闲 CLI 异常死亡的边界。
2. Stop reports 的单锁映射、与未知交接安全条件的边界。
3. 快照的线性化时刻与失主队列对账顺序。
4. 全锁是否保持不自动唤醒，以及有效 Idle/Running 的恢复证据。
5. native 的事前准入、存活/未知 owner 处置，以及多 Illegal 批次的继任恢复关系。
6. 迁移证据不足时的明确策略，以及旧恢复意图与新合法性字段的分工。

其余事务、revision、取消、身份校验可以按现有架构实现，无需改变用户确定的手动操作规则。补齐上述文字和验收后，可采用 Session 元数据原子提交与现有串行边界实施；当前不能承诺“无需额外提示即可安全直接实施”。
