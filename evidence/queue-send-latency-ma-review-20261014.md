**T-QUEUE-SEND-LATENCY-20261014/1：MA 初审补项完成，可供 MA 验收 FF。**

在原工作树 `D:/project/pan-worktrees/queue-send-latency-20261014`、原分支
`fix/queue-send-latency-20261014` 无损合入 main
`e0b78f35c6d7cac5c014d0d1155f4f6cd6229b5a`。正常 `git merge --no-edit main`
生成 merge commit `522135b02123829177cd91699d1e2f2500bd3a39`，无冲突，保留
`f03395d16f5dcb045de3b2c9544868bb3ef75736` 的历史，没有 reset/rebase/squash，
没有移动 main/practical 或 push。最终组合 SHA 是包含本报告的分支 HEAD；
提交后另向 MA 给出该完整 SHA。通知 Workspace 改动沿用 MA 已验收的 main，
本轮没有再改前端或扩大其验收范围。

**sender 补审结论与最小修复**

旧 sender 在创建后、等待 queue_lock 时按 worker ID 重新查 Worker；同 ID 的
Worker 替换、process/stdin 替换或 generation 改变会让旧请求写到另一个 runtime。
六种确定性红测在 merge commit 上全部失败：返回了接受而不是写前拒绝。
现在在创建 sender 前捕获 `(Worker, generation, process, stdin)`，内部参数
`_expected_runtime` 只传递给控制发送函数，不进入 provider wire。调用开始、拿锁
后以及编码后/实际 write 前校验该绑定；失配明确返回
`Worker changed before control handoff`，两个 stdin 都没有 write/drain，历史不追加。
已写入的请求继续 drain 当次捕获的 stdin，不重新读取 w.process 指向的新 pipe。
这是原发送路径的绑定校验，没有新增队列、owner 架构、超时或重投规则。

f03395d1 的 finally 已对 sender 执行 cancel + await gather，本轮对真实
asyncio.Lock 等待验证了取消效果：在 queue_lock 仍由测试持有时，返回 unknown
或请求协程结束前 sender 已 done/cancelled；随后释放锁也不会迟到 write。
30 秒测试使用生产常量，实测 30,015.0 ms，断言仍为 `30 <= elapsed < 32`。
没有缩短截止时间或用 clock mock 替代这一观察。

已 write 且 ACK 先到时，回收 sender 只结束 drain 等待，不 close pipe、杀进程
或撤销已接受数据；历史仍只记录一次。HTTP/调用方取消后的未来回执保留在原
requestId 的 future 中：明确接受才记录一次并解除阻止；迟到 unknown 不解除
阻止，也不自动重发。排队 Steer 的原 item ID/generation 门禁保持原实现。

**精确 node 与证据**

下列 node 均位于 `tests/test_queue_send_latency.py`，可以用完整
`tests/test_queue_send_latency.py::<node>` 直接执行：

| 要求 | 精确 node | 验证 |
|---|---|---|
| ACK 先到、drain pending | `test_direct_steer_acceptance_receipt_completes_before_stdin_drain`（已有） | drain 未释放、write 一次、历史一次、明确接受 |
| 写后取消、迟到接受 | `test_cancelled_direct_steer_keeps_original_receipt_owner`（已有） | 原 future/ID 保留，迟到接受只记一次，重复不写 |
| unknown 阻止重发 | `test_unknown_direct_receipt_does_not_clear_resend_block`（已有） | unknown 无历史，第二次 Steer 拒绝、write 仍一次 |
| 持锁等待时取消 | `test_direct_steer_waiting_for_queue_lock_never_writes_after_owner_ends[http_cancel]` | 取消完成后 sender terminal，释放锁不写 |
| 持锁等待满 30 秒 | `test_direct_steer_waiting_for_queue_lock_never_writes_after_owner_ends[30s_unknown]` | 真 30 秒 unknown，sender terminal，释放锁不写/不 drain |
| 实际 HTTP 路由取消 | `test_cancelled_http_steer_waiting_for_queue_lock_has_no_late_write` | ASGI 请求协程取消，Session spawn lock 释放，queue_lock 仍持有，重发 HTTP 拒绝，放锁不写 |
| 替换 Worker | `test_direct_steer_cannot_write_to_replaced_runtime[worker]` | 写前拒绝，旧新 pipe 均零 write/drain |
| 替换 process | `test_direct_steer_cannot_write_to_replaced_runtime[process]` | 写前拒绝，旧新 pipe 均零 write/drain |
| 替换 generation | `test_direct_steer_cannot_write_to_replaced_runtime[generation]` | 写前拒绝，旧新 pipe 均零 write/drain |
| 替换 stdin | `test_direct_steer_cannot_write_to_replaced_runtime[stdin]` | 写前拒绝，旧新 pipe 均零 write/drain |
| sender 调度前替换 | `test_direct_steer_cannot_write_to_replaced_runtime[before_sender]` | 绑定在创建任务之前，调度后不能重新选新 pipe |
| 编码时替换 | `test_direct_steer_cannot_write_to_replaced_runtime[during_encode]` | 编码后重新核验，旧新 pipe 均不写 |
| 已写后 process 替换 | `test_written_direct_steer_drains_captured_stdin_after_process_replacement` | 原 pipe 的 drain pending；原 ACK 接受，不触及新 pipe，历史一次 |
| 取消后的迟到 unknown | `test_cancelled_written_direct_steer_late_unknown_keeps_resend_block` | 阻止仍保持，write 一次，无历史/重投 |

全部真实名称也保存在 JUnit 与 manifest 的 identity 列表。HTTP 证据覆盖
ASGI 请求协程取消及真实
route/Session 锁链，不宣称物理 TCP 断连会自动取消服务端 handler。

- [六项写错 runtime 红测：原始 XML gzip](queue-send-latency-artifacts/queue-latency-ma-runtime-red.xml.gz)
- [sender/历史/分支补审 22 项：JUnit](queue-latency-ma-runtime-green.xml) / [日志](queue-latency-ma-runtime-green.log)
- [整合后 360 项：JUnit](queue-latency-ma-combined-tests.xml) / [日志](queue-latency-ma-combined-tests.log)
- [另补 HTTP 取消 1 项：JUnit](queue-latency-ma-http-cancel.xml) / [日志](queue-latency-ma-http-cancel.log)
- [组合校验清单、源码哈希、node 身份与复跑 argv](queue-latency-ma-evidence-manifest.json)

**组合验收事实**

完整相关回归为原 18 个模块加 `tests/test_worker_branch.py`，360 passed，0 failure、
0 error、0 skipped，51.01 秒；其中包含原 346 个测试身份的全部，逐项集合对比
没有丢失、改名或改参数 ID。HTTP 取消用例在完整回归后作为仅追加的独立函数
补入并单独执行，1 passed；两个批次合计 361 个不同通过身份。追加函数没有
修改前一批 360 项的任何定义或 production source。三处原测试替身只增加接收
内部绑定参数及断言绑定正确，既有测试身份、阈值、失败断言均保留。

`queue-latency-evidence-manifest.json` 与原性能数字属于 f03395d1 的历史快照，
不改写成组合版本的实测；本轮新的 source/hash/结果归属由 MA manifest 保存。
没有重复性能探针或声称在组合 SHA 上重测了原性能数字。

MA 验收：核对最终组合 HEAD 同时包含 f03395d1 与 e0b78f35，确认 main 仍可
FF 到它；核对 MA manifest 当前源码哈希、原 346 identity 全覆盖与两个新批次
零失败/零 skip；复核六个绑定红绿 node、真 30 秒及 HTTP 取消 node。通过后由
MA 正常快进 main，再执行 main→practical；本 TA 不执行这两个 ref 更新。
若失败，引用精确 node/日志和本任务 ID 回填验收记录。

本轮没有新启动后台 Job；单次验证均小于一分钟。没有访问 8767/8768、真实
provider、真实凭据或操作既有服务；所有进程级回归仍采用自有临时根与假 CLI。
