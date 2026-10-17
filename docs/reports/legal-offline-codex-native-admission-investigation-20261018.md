# Codex 原生 turn 事前持久准入：冻结版本调查

> 本文保留调查时的结论和冻结版本行号；后续已获 MA 授权实施受控 turn/start 与空闲 compaction，实际交付另见 `legal-offline-codex-native-admission-delivery-20261018.md`。调查时的“未提交”只描述该阶段。归档 WIP 未应用，当前实现从冻结基线重新编写。

日期：2026-10-18。基准 `e681cb5a4c574ac552afc106576186408d1d2289`。工作树 `D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`，分支 `review/legal-offline-illegal-lock-20261018`。

## 结论及工作边界

**Pan 控制的常驻 Codex 原生 TUI 的 turn/start，可以在现有本地代理转发前等待 Pan 原子保存 False/run 后的 ACK。无需修改 Codex 客户端/供应商协议，也无需通过键盘推测开始执行。** 必须改变的是 Pan↔wrapper 私有控制协议、原生 RPC 代理的转发时机，以及错误/超时/迟到回执处理。

这可作为原任务内的有限实现，但不能直接推出“所有原生执行入口均闭合”：独立 takeover、绕开私有代理的外部客户端、compaction，以及开启 passthrough 后未知的新执行 RPC，需要各自准入或明确阻断；未知方法仅标 settings_uncertain 并不等于持久执行准入。MA 应按下文的范围作决定，当前缺口保持未验收。

用户最新消息将本回合改为只调查。收到前，前条整改授权下已生成 16 文件未提交 WIP；收到后已归档到 `audit/legal-offline-native-admission-investigation-20261018/wip-before-freeze.zip`（内部为 `wip-before-freeze.patch`） 并恢复这些文件到冻结 HEAD，未提交/应用该补丁。它包含未验收原型和与本调查无关的模式修正，不能整包集成，也不代表正式建议已实现。HEAD 未前进，main/practical 未移动，无 push/restart/依赖安装/真实供应商执行。报告和调查证据保留为未跟踪文件。

## 冻结源码的事实链

| 位置（均为冻结版本行号） | 事实及含义 |
| --- | --- |
| `packages/core/worker.py:8188–8224` | native_input 经过 queue_lock、generation 核验后写 wrapper stdin；成功回执只证明 PTY 写入，不证明独立 turn 已持久准入。 |
| `packages/core/adapters/codex/app_server_wrapper.py:1394–1414` | native_handler 将输入送 ResidentTui.input。 |
| `packages/core/adapters/codex/resident_tui.py:282–285` | input 写原生 PTY。不可将字符、Enter 或写入回执当作执行事实，审批回答、草稿和快捷键也走这里。 |
| `resident_tui.py:60–85` | TUI 使用 --remote 指向 Pan 私有、鉴权、动态端口代理；不是直接连 provider endpoint。原生按键所触发的 RPC 会回到这个代理。 |
| `resident_tui.py:189–239` | _handle 解析原生 JSON-RPC。thread/resume 在这里被改为 thread/read，不启动新 kernel。只允许当前 thread。 |
| `resident_tui.py:240–261` | 转发 turn/start 前已有可拦截点：设置 _native_turn_pending、发 native_busy，再 peer.send。当前没有跨 Pan 进程持久 ACK。 |
| `app_server_wrapper.py:1423–1447` | provider turn/started 后才发 pan.native_turn_started；执行开始早于此观察。 |
| `packages/core/worker.py:2262–2266` | Codex 在此事后调用 _admit_native_turn，所以不能证明事前 False。 |
| `worker.py:2139–2176`、`:734–750` | 已有分配 task/execution seq、捕获 runtime、保存 run 的函数，可复用但须修正 busy 与原子身份。 |
| `worker.py:603–624`、`packages/core/legal_offline.py:103–114` | 现有有序 writer + offline_override 原子保存可同次提交 False/run UUID/owner，无需新数据库。 |
| `worker.py:2248–2260`、`packages/core/adapters/cbc/native_bridge.py:197–208` | CBC 已有 request/ACK 形状，可复用消息格式；CBC 现有实现本身不等于 Codex 全路径已验证。 |

## 推荐的最小私有握手

正常路径的严格顺序：

1. **代理预留。** 在 ResidentTui._handle 识别当前 thread 的 turn/start，生成不可复用 admission UUID，记录 JSON-RPC id→admission 映射。短时间持 control_owner 锁核验 native mode/control generation，原子预留 _native_turn_pending；不是写 False，不消费队列。
2. **请求 Pan。** wrapper stdout 发 `pan.native_turn_admission`，至少带 `requestId`、`threadId`、captured control generation 和 operation=turn/start；不得在请求/日志里带 prompt 或 capability。wrapper 在此等待，尚不调用 peer.send。
3. **原子准入。** Pan _read_stdout 对捕获的 Worker/token/generation/process/stdin、当前 Session/thread、控制权和执行冲突复检；在 queue_lock 和实际 writer ticket 内，再次核验相同身份。一次保存 False、run UUID、owner、execution/task seq 和 nativeAdmissionId。ACK 内容带 requestId、ok、runId，且只写回捕获的旧 stdin，不能换成 successor pipe。
4. **消费 ACK。** wrapper 只接受当前 pending UUID 的 ACK，记录 runId。迟到/重复/未知 ACK 不得授予其他请求权限。ACK 必须由专用 stdin 分发器直接路由到 pending future/event，不能排在 native_input、mode_handler 或 provider 完成循环后面。
5. **再验 owner 再转发。** 结束等待后重新取得 owner/policy 锁，核对原 mode/control generation、Session/thread 及 pending request；设置 sending/attempted，之后才调用 peer.send。此处调用前磁盘必须已有 False。
6. **绑定 provider turn。** 将响应的 turn.id、后续 turn/started、turn/completed 绑定同 admission/runId；started 不再分配第二执行、不再首次写 False。终态按原安全 history/result 提交结束该 run。旧 turn 迟到终态不能结束后继执行。

可复用现有消息形状：

```json
{"type":"pan.native_turn_admission","requestId":"uuid","threadId":"thread","controlGeneration":3,"operation":"turn/start"}
{"type":"native_admission","requestId":"uuid","ok":true,"runId":"durable-run-uuid"}
```

第二行是 Pan→wrapper 私有 stdin 控制，不是用户输入/业务任务。拒绝响应只给固定原因码，不能泄漏 prompt 或私有 endpoint。wrapper 运行必须 fail closed：缺少新协议处理器/没有 Pan ACK 时不能回退到旧的先发请求模式。

### 不能省略的并发修正

- **busy ≠ 已准入。** resident_tui.py:248–250 在请求前发 busy；worker.py:2268–2269 将其写成 _native_external_turn_active，worker.py:2150 又因此拒绝 admission。应分开表示 reservation/busy 与 durable admitted execution；busy 仍阻止队列，不能据此猜开始/结束。
- **ACK 泵不能被锁挡住。** ControlOwner.write（control_owner.py:37–43）持 RLock 调完整 operation；resident_tui.py:242 又持 policy lock。不能在这些锁里等待 Pan。_read_pan_stdin（app_server_wrapper.py:1298–1304）还同步调用 mode_handler/native_handler；即使等待线程释放了锁，若后续 native_input 在 stdin 线程阻塞于另一个锁，ACK 仍会堵在管道后面。建议 stdin 只做解析，ACK 直接分发，其他可能阻塞的操作进入保持原顺序的单独 control executor。不可为了修死锁把 owner 核验和实际发送拆成无保护竞态。
- **writer 内身份。** _admit_native_turn 当前在 loop 中先改变 task_seq/Worker 当前任务字段（worker.py:2153–2167），再等待有序 writer。建议将 durable seq/nativeAdmissionId/run 在同一候选保存，提交成功后发布 Worker 投影；失败不得留下伪执行身份。请求在等待期间被 Restart 取代时，writer 拒绝、ACK 不可发给 successor。
- **pending-ACK 期间保持 gate。** 模式可切换，但预留尚未通过“确定未发送”取消前不能释放 queue handoff。简单收到 busy=false 不得清除仍 admitted/sending/unknown 的执行。

## 崩溃、拒绝和超时

| 窗口/事实 | 必须行为 |
| --- | --- |
| 请求到达前、原子保存失败 | 不发 provider；返回拒绝；不得凭失败将旧异常 run 写合法。 |
| 已提交 False，ACK 丢失或 Pan 随后崩溃 | 不自动重发；wrapper 无 ACK 不发送。恢复时保留已准入未完成事实，不能靠超时推定合法。 |
| 等待超时且 wrapper 原子撤销 pending、能证明尚未调用 peer.send | 发 correlated notForwarded 取消事实；Pan 只按 nativeAdmissionId/run/owner 条件安全退休。若 Pan 已死或取消回执丢失，保留 False，待对账，不能隐式解锁。 |
| ACK 与超时同时发生 | pending 状态转换须单次决定。取消胜出后迟到 ACK 不再发送；发送胜出则不能再发 notForwarded。需要 event/状态锁，单独 event.wait(bool) 不足以证明发送边界。 |
| ACK 后 mode generation 变化、尚未发送 | 不发送；按相同 admission 条件取消；绝不能收到旧 ACK 后用新 generation 继续发送。 |
| peer.send 尝试后抛错、断线、wrapper 死亡 | 发送结果 unknown；保留 False 与 owner，不自动重投，不发 notForwarded。发送函数异常不证明 provider 未接收。 |
| JSON-RPC start 明确拒绝且可证明无 turn 建立 | 在 _from_server 的 id 映射（resident_tui.py:144–148）上关联 admission，提交安全拒绝终态，不能只清 busy。若同时收到 turn/started/状态证据冲突，则保持 unknown。 |
| 正常 turn/complete、正常捕获 Error/429 | 复用已有安全结果链；先安全提交后 True，保持用户决定。 |

Pan 与 provider 不存在原子分布式事务；方案保证的是“任何 provider 转发尝试之前有持久 False”，不是保证每个写入 False 都一定实际执行。pre-admit 后但未发送的崩溃会保守留下未完成记录，这需要 correlated 取消或显式 Stop 对账，不能通过快照 UI Idle 自动合法化。

## 覆盖矩阵与仍可绕过的路径

| 路径 | turn/start 握手的覆盖及下一步 |
| --- | --- |
| Pan 正常队列→_consumer_stream→AppServer._run_turn | 已有交接前 False；_run_turn 在 app_server_wrapper.py:1213 发 turn/start。保留原机制，不再加第二 run。 |
| 原生 TUI 键盘→私有代理→同 thread turn/start | **能闭合。** 拦截点是 peer.send 前，不是键盘输入；包括该 TUI 的普通新提示、同一 RPC 形状的重试和附件提示。要确认真实客户端所有新 turn 都走此 RPC。 |
| turn/steer、审批/用户输入响应、command/exec/write | 不是当然的新 turn。只有可关联的 admitted active run 才可作为续写复用；现有代理允许部分 THREAD_WRITES 在 Idle 送出，必须在执行入口校验当前 run，而不能凭方法名就假设已有 False。 |
| thread/compact/start | 当前是允许的 THREAD_WRITES（resident_tui.py:30），但没有独立 admission 或清晰完成映射。若 compaction 构成受保护执行，需独立 operation admission + completion；不能因为名称不是 turn/start 就放行。 |
| native_passthrough=true 的未知/新增方法 | resident_tui.py:243–254 允许未知 RPC，只写 settings_uncertain。潜在新执行（例如新增 review/exec/后台操作）可绕过仅针对 turn/start 的握手；具体方法语义须按安装客户端 schema 核验，不能从方法名断言。要宣称全闭合，需显式分类可证明的设置/只读/续写操作，其他执行请求准入；未知语义必须拒绝或保持未验收。禁用/限制现有 passthrough 会改变既有交互，须 MA 决定，不能在只调查回合擅改。 |
| thread/resume（私有 TUI bootstrap） | resident_tui.py:217–226 改为 thread/read；不因此分配执行。不要破坏现有“不第二次 resume/kernel”的边界。 |
| 独立 takeover 终端 | server.py:3450–3466 打开外部 CLI；worker.py:7132–7149 的 unknown owner 只阻第二 writer，不提供 per-turn False。该 CLI 不经过 ResidentTui 代理。全覆盖需要把 takeover 接到同一代理/等价同步 hook，或阻断其执行；这是交互/生命周期改动，不能由本方案自动代表已经覆盖。 |
| 另一个自行启动的 CLI/直接认证连接 provider、其他 thread | NativeTransport（native_transport.py:23–46）是私有 capability；正常使用不公开，但本地外部进程绕过代理仍没有此握手。thread 校验仅覆盖代理自身，不能声称拦截所有机器上的 writer。外部 owner未知时现有拒第二 writer仍应保持。 |

## 验收建议及本轮证据

**实测：**仅在冻结源码上跑既有 proxy/控制消息纯测试，9 passed、0 failed/error/skip、exit 0。验证当前 plain turn 经代理转发、passthrough/owner/thread 边界及设置未知标记；它们没有持久 admission 断言，不能作为新方案完成证据。日志/JUnit/source SHA256 在 `audit/legal-offline-native-admission-investigation-20261018/`。没有运行供应商、真实 TUI、真实服务、build 或访问 8767/8768；只读查看 protected listener 8768 仍 PID 31556。

**代码推断：**上述入口可拦截、现有 started 在后、busy/锁/线程风险来自冻结源码。没有声称握手已经在冻结分支实现。

**待实现后必须实测：**

1. 用现有 tests/test_codex_native_passthrough.py 的 fake peer 直接驱动 _handle，把 peer.send 设断言：False/run/nativeAdmissionId 已在 JSON 文件，且旧锁定队列未消费；ACK 前 send 次数必须为 0。
2. 原子 replace 阻塞/失败、ACK 丢失、重复 UUID、迟到 ACK、同对象 generation 变更、writer 等待期间 Restart、取消/发送竞态：不转发或保持 unknown，不能建立第二 run/第二 writer。
3. ACK 等待期间分别插入 native_input、resize、policy disable、mode switch；stdin 仍能路由 ACK，不死锁，generation 改变后不得旧授权转发。
4. native_busy 先于 request；忙预留不拒合法首个准入，双重 turn/start 则只允许一次。失败回执必须按 admission 匹配，不结束后继任务。
5. peer.send 已调用后抛错；provider 收到但响应丢失；turn/start RPC 拒绝；turn/started 与拒绝回执冲突；late turn/completed。分别验证 False 保留或安全退休，不能只断言 UI 状态。
6. 硬杀自有隔离 Pan，分别卡在提交前/提交后 ACK 前/ACK 后发送前/发送后；检查磁盘 owner/run 与恢复快照，且没有真实 provider 再发送。使用 fake local RPC server 即可完成大部分故障矩阵。
7. tests/test_codex_native_kernel_real.py:169–176 已有真实 TUI 键盘到 turn/started 的隔离测试框架，可改成受控本地 provider + Pan admission harness，断言 ACK 前本地服务收不到新 turn、ACK 后才能接收。当前 harness 直接操纵 wrapper stdin，新增协议后必须模拟真正 ACK，不能为了让测试继续而绕过。
8. 保留 UI 原生输入/草稿/审批交互，无 Enter 猜执行。附加 passthrough compaction/未知方法和独立 takeover 测试，明确哪些被阻断、哪些有完成证明、哪些仍未覆盖。

## 给 MA 的决策建议

建议允许下一轮将“同 thread、经过 Pan ResidentTui 私有代理的 turn/start”作为原任务内的有限补丁实施，复用现有 Session JSON writer 和 CBC 私有消息形状，但必须包含身份持久化、ACK 分发、busy 分离、取消/unknown 与 provider turn 关联，不能只加一条 event.wait。

若要求所有原生执行入口都达到同样保证，需要同时决定：compaction/其他执行 operation 的生命周期、未知 passthrough 的准入/拒绝策略，以及独立 takeover 是否接入受控代理。在这些决定或实现/实测缺失时，保持该需求未验收；不能用只记录边界将它判完成。
