# ses_be105a81379e8cb1 持续拒绝 reimport 的只读诊断

2026-10-10；检查时 feature/main/practical 均为
`6f1047127e8e90d7de3704dba2ae884472ae1dd9`。
本次不修改源码、真实 Session、native rollout 或运行服务。

## 实测

- Pan 完整 `.history.jsonl` 为 4861 行；Codex rollout 解析为 4843 行。
- 7 条带 queueEnvelopes 的 Pan origin，6 条可匹配；最后一条不可匹配。
- 未匹配 durable ID：`pan:03f78bb615c84fd48af9a0145081d67b`。
- queue item：`q_e19358a4e84447c19a4adec2457b4cb7`。
- 记录时间：`2026-10-10T15:30:59.338115`；source=user；parts 只有一个文本块，
  没有附件，nativeItemId 为空。
- 正文请求处理 `Native terminal request failed (403: forbidden-origin)`；
  对完整 rollout 搜索未找到该 origin 正文，并非仅 parser 没返回它。
- durable queue_delivery_ledger 为 sent_to_cli，receiptAt/deliveredAt 在同一时刻；
  当前 queue_pending 不含该条。history envelope 是当时 writing 的快照。
- 该 origin 之后 rollout 仅有 6 条 thread_settings_applied，没有新消息或 turn。
- Pan 的 last_result 在 15:46:35 记 error，result 为
  `Codex app-server exited (returncode=None)`。这是后续错误证据，尚不能证明
  app-server 退出为何发生，或退出本身就是消息未被 native 保存的直接原因。

## 代码推断

`worker._consumer_stream` 经 `_queue_handoff` 完成 stdin write/drain 后调用
`_commit_queue_handoff`，此时记录 sent_to_cli 和 Pan history。因此这个账本状态
证明 wrapper 输入写入边界，不证明 Codex turn/start 接受或 rollout 落盘。
本次 reimport 无法凭这个 receipt 为缺失的 native 消息建立无损对应关系。

这与此前附件 Markdown/真实路径的等价投影缺陷不同。前次修复解决可证明的
附件匹配；没有将真实缺失的 origin 自动删除、强行配给近似正文，或伪造 native
历史。原约束明确要求无法证明时保留旧历史并失败，本 Session 正处于这种情形。

## 私有复现与磁盘重读

只读复制实际 rollout 到系统临时目录，用实际 provider parser 读取该副本；
将完整 Pan 历史写入另一个临时 Session 目录。调用实际 ASGI HTTP handler，
没有监听真实端口，也没有调用真实 Session reimport。

返回 HTTP 200，但业务 `ok=false`，错误精确为原 reported error。
临时 JSON/JSONL 字节完全不变；清 cache 磁盘重读仍等于全部 4861 行旧历史。
真实 Pan JSON/JSONL 和真实 native rollout 的前后 SHA256 完全相同。
无正文证据在 `be105a-diagnosis.json`；私有原始副本未入库。

## 结论与边界

本轮没有使这个 Session reimport 成功：其最后一条 origin 在 native 历史中
确实缺失，当前拒绝保护正确保留了它。不能在原无损要求下通过删除该记录或
放宽匹配修复成功标志。未做自动重发；原消息可能已被 wrapper 收到，重发
需要单独确定任务结果与用户意图，不能当作 reimport 的隐式副作用。

未复现 app-server 当时的运行故障；没有证明进程退出原因，也没有进行原生
数据修补、服务重启、main/practical 推进或发布。若后续调查投递故障，应从
wrapper 的 turn/start 请求/回执和当时日志核对，而非放宽 reimport 无损保护。
