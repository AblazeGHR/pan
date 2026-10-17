# Codex 受控原生 turn 事前持久准入：交付与剩余边界

日期：2026-10-18。工作树 `D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`，分支 `review/legal-offline-illegal-lock-20261018`。交付给 MA 独立复核，不合并 main/practical、不 push/release、不重启现有 Pan。

## 提交与结论

- 冻结实现基线：`e681cb5a4c574ac552afc106576186408d1d2289`。
- 本轮实现/测试/设计及调查证据：`21e3b3bf4f4a54712dd1d35bdbf8cc434e791cee`。
- 本报告为后续纯文档提交。main/practical 最后只读核验仍为 `5d20e72bf44f8cdb46bf8629f4772fb3b393863a`，未移动。

**已完成受控 ResidentTui 代理 turn/start 的事前持久握手，并扩展到空闲时的手动 /compact。** 真正的本地 Codex CLI + 原生 TUI + 本地合成 provider 已验证：ACK 前 provider 未接收，ACK 后才执行；磁盘 False/run/nativeAdmissionId 已先提交；started/result 关联同 run，正常安全 Idle 后杀 wrapper 不生成 Illegal。

**不能据此认定所有原生入口均闭合。** 活动 turn 中的 compaction、未知 passthrough、独立 command/exec、detached review 和外部 takeover 保留既有功能，其剩余执行归属风险见后文。本轮不把未批准的缺口判完成。

本轮从冻结基线重新编写，未应用归档 WIP。先前只调查报告和 9 用例证据已提交保留；WIP 为 ZIP 内的原始 patch，manifest 保存原 patch SHA256。ZIP 内补丁仅供追溯，不是可应用实现，包含未验收/超出本轮范围的内容。

## 行为与关键边界

1. ResidentTui 在转发受控 turn/start、空闲 thread/compact/start 前生成 admission UUID，捕获 thread/control generation，短时间持 owner 锁预留 pending。stdout 发请求，等待时不持 owner/policy 锁。ACK 之后再在实际 owner 写边界核验相同 thread/generation/对象归属，才尝试 peer.send。
2. Pan 复用 queue_lock、有序 writer、Session JSON 原子替换。同一候选提交 False、唯一 run UUID、nativeAdmissionId、owner、task_seq、executionSeq、native operation/timing；writer 内检查捕获的 runtime 身份。提交后才发布 Worker 执行身份。ACK 携带 requestId/runId，且仅向捕获的原 stdin 发回。
3. reservation busy 与 admitted execution 分开；busy=false 不能清已准入执行。所有原有 native handoff/delayed restart gate 同时考虑两者。原生准入不认领 Pan 队列项，不为既有 Pan queue/Steer/审批续写额外建立 run。
4. stdin parser 直接分发 ACK。可能阻塞于 owner/policy 锁的控制回调交给单独有序 executor；普通任务、控制和 EOF 保持原顺序，只有 ACK 可以绕过等待。没有依靠 sleep 推断 provider 就绪。
5. 一次性 admission 状态区分 waiting/granted/sending/unknown/started/cancelled/rejected。迟到 ACK 不能复活已取消请求；ACK 与 timeout 的竞争由状态锁决定；发送调用一旦尝试，失败保留 unknown/False/owner，不自动重投。
6. 确定未发送的取消、provider 明确拒绝且尚无 started 冲突，发送 correlated not-forwarded。Pan 只按 nativeAdmissionId/run/owner 条件安全退休，旧取消不能结束新 run，已经 Illegal 的执行也不能被迟到取消洗成合法。若取消回执丢失，保留未完成事实，等待显式退休/对账。
7. provider started 将 turn ID 绑定原 admission；不再首次写 False或分配第二 run。旧 thread/turn 的 completed 不影响当前 accumulator，结果须匹配 admission/run/turn。wrapper 自己在发送后崩溃不再伪造普通安全 provider result，避免未知执行被提前写 True。
8. 正常 provider 终态仍沿用既有安全 history/result 提交；正常捕获 Error/429 的产品决定保持。安全 Idle 可保留常驻 CLI；本轮没有把 CLI 存活当 Illegal。

当前源码定位：

| 文件 | 主要职责 |
| --- | --- |
| `packages/core/adapters/codex/native_admission.py` | 单请求状态转换、ACK、timeout/cancel、发送未知与 started/rejected 互斥 |
| `packages/core/adapters/codex/resident_tui.py` | 代理事前拦截、owner/thread/generation 复检、RPC response→admission 关联 |
| `packages/core/adapters/codex/app_server_wrapper.py` | ACK 路由、控制 executor、provider notification 关联、wrapper 故障不冒充安全结果 |
| `packages/core/worker.py` | 原子持久准入/条件退休、busy 与 active 分离、绑定 started、拦截迟到外部 result |
| `packages/core/legal_offline.py` | task_seq 纳入现有原子候选 snapshot/override，保证准入序号与 run 同次保存 |

## 最终实测

| 本轮集合 | 通过 | 失败 / 错误 / skip | exit | JUnit 时间 |
| --- | ---: | --- | ---: | ---: |
| 13 文件定向回归（含 32 个新准入用例） | 334 | 0 / 0 / 0 | 0 | 11.585s |
| 真实本地 CLI / 原生 TUI / 本地 provider | 3 | 0 / 0 / 0 | 0 | 7.820s |
| 不重叠合计 | **337** | **0 / 0 / 0** | **0** | — |

证据目录 `audit/legal-offline-native-admission-20261018/`：完整命令及计数在 results.json，日志/JUnit 为 targeted 与 real-native，源码逐文件 SHA256 在 source-manifest.json。本轮测试没有新增 skip/xfail 或放松既有断言；旧原生 completion 用例改用真实 admission/ACK 形状；原本未登记 Session/Worker 的 metadata 测试补上实际注册身份。

新增用例覆盖：turn/start 和空闲 compact 两种操作的磁盘 False 先于 peer.send、replace 失败不发布序号、ACK 丢失/迟到不发送、thread/control owner 切换、发送异常保留未知、RPC 明确拒绝/started 冲突、同 thread 第二预留被拒、writer 等待期间 Restart、旧取消不能结束后继/Illegal 执行、晚到 completed 不结束当前 turn、busy 预留不拒首个有效准入、ACK 绕过阻塞回调且任务不越序。旁路观察使用 after-start 标记，不计作事前覆盖。

真实测试使用本机 Codex **0.162.1**、临时 CODEX_HOME/工作目录、自有进程 Job guard、本地动态 HTTP provider，未消耗远端模型：

- 真实 TUI 键盘输入新提示，等待 admission。测试通过 Pan 的真实有序保存 helper 创建 run，读取 Session JSON 确认 False/nativeAdmissionId；此时本地 provider received 仍为 false。发 ACK 后才看到 provider 请求和关联 started/result。
- 同一真实 TUI 输入 /compact，确认 operation=thread/compact/start，同样先持久准入后 ACK，正常收到 correlated result。
- 结束安全结果提交后，外部结束自有 wrapper，确认合法性保持且无 Illegal batch。
- 保留原有真实 mode round-trip/kernel identity、启动失败退出用例。测试是“真实 CLI/TUI + Pan 持久 helper”的进程路径；没有启动完整 HTTP Pan 服务，也不等同于浏览器端到端验收。

本轮无 React 源码变化、未安装依赖，未重复 build。MA 对冻结版的 601 测试和 build 属独立既有证据，不累计进本轮 337。每个定向命令预计和实际均远小于三分钟，没有启动额外 Session/长时 Job。

## 其他原生入口调查与兼容处理

本机 CLI 执行 `app-server generate-json-schema --out <临时目录>`，exit 0；仅导出协议定义，无 provider 请求。最小摘录和命令保存在 local-schema-findings.json，版本 0.162.1。以下区分代码/协议推断和本轮实测：

### 已同架构补齐：空闲手动 compaction

协议定义 thread/compact/start，真实 TUI /compact 创建活动 turn，并沿同类 started/completed 通知结束。空闲时它能复用同一 admission/run/result 模型，本轮已补并实测。没有依靠空 response 或 settings_uncertain 把 compaction 伪装成“设置已修改”。

### 保留未验收：活动执行中的 compaction

原生代理以前直接转发活动 turn 中的 compaction；本轮保留该行为，不因单 run 门禁一律拒绝。当前正在执行时已有 False，但请求若在 provider 排队并于该 turn 结束后才开始，第一 run 的安全提交与后续 operation 之间仍可能有窗口。可靠统一需持久记录并发/排队的 operation 集合、相应完成/取消关联，并防止任一单独终态过早将整体合法化。仅在已有 run 上“再写一次 False”或只做本地 pending 标志不能证明跨服务恢复，故未声称覆盖。

### 保留未验收：独立 command/exec 与未知 passthrough

本机 schema 明确 command/exec 能在不创建 thread/turn 的情况下运行独立命令；响应在进程结束后返回，支持 processId 和后续 stdin/write/resize/terminate。它可以与 Agent turn 或其他 processId 并行，不能可靠套用当前“一个活动 turn 的 nativeAdmissionId + turn/completed”。

单独空闲 command 可以仿照本方案做请求前准入和 response 后结束，但要兼容用户已有并发独立命令功能，必须有按 processId 的多个 operation ledger/owner/完成计数，而不能通过串行化或拒绝并发冒充完成。本轮保持 passthrough 功能，不改变其默认 opt-in，不一律禁用未知方法。执行可能在服务仍认为合法 Idle 时开始；若随后崩溃，现有 owner gate 能阻止未知/存活 owner 的第二 writer，但不能据此产生准确的 Illegal batch。

未知 passthrough 若确实发出一个新 turn，仍保留既有事后观察/报告路径，明确写 nativeAdmissionTiming=after-start。受控代理的新提示/空闲 compact 则记 before-forward。after-start 不具有 ACK 前零 provider 写保证；未来新 RPC 不能仅因为能转发就算已准入。

### 保留未验收：detached review

本机 schema 的 review/start delivery 支持 inline/detached。detached 可引入另一个 review thread/执行归属，当前代理及 Session/run 都按原 thread 单 owner 建模。可靠支持需把返回的 review thread、执行和 completion 持久映射到原 Session 或独立受管对象；不能只给原 thread 准入后忽略另一个执行。未修改或关闭既有 passthrough review 功能，本轮没有真实触发 detached review。

### 保留未验收：独立 takeover / 外部客户端

codex adapter 的 takeover_command 仍为独立 `codex resume <thread_id>`，不走 ResidentTui 私有代理。既有事前 unknown owner 预留和 Windows Job retirement 继续保留，能防恢复时第二 writer；它们不等价于每次原生 turn 的 False/精确异常批次。

要闭合该路径，应将交互终端接到受控 app-server 代理并保持原输入/权限/会话功能，或让该 CLI 提供等价同步执行 hook。两者会改变外部终端的执行归属/生命周期和断线恢复，超出本次有限代理补丁；不应在无验收下把任意本机外部 CLI 纳入覆盖声明。当前操作风险仍是：takeover 可以执行任务，但异常中断缺少 per-turn 合法性记录，只有 owner 不确定保护。

## 集成准备与限制

本分支提交供 MA 在其独立 integration 工作树审查、定向复验；本次没有操作该工作树或分支。main/practical 未移动，未 push/release、未重启任何既有 Pan，未向 8767/8768 发测试请求。最后只读核验 8768 仍为 PID 31556（开始时间 2026-10-18 02:28:57），8767 无监听。自有真实 CLI/本地 provider 由测试 finally 回收。

原用户设计文件仍未改变，SHA256 `C4055322C9162446C833BBDAFE351CEFA6F8FDA88C7E69D94FE6978538C2D6A9`。技术补充只写独立修订文档。

本轮可验收范围是受控 turn/start 与空闲 /compact 的同步持久准入。上述并发 operation、未知执行和独立外部入口仍待 MA 决定下一步生命周期扩展，原任务整体不能仅凭本轮通过就宣称所有原生路径已完全闭合。
