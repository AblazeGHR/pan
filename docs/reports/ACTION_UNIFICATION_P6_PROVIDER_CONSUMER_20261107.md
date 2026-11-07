# P6 provider / QQ / model-CLI isolated consumer slice

Baseline: `68e534a329f6daa4072c1ce05bdf6ae02207ab6e`.
Branch: `implement/action-unification-p6-20261106`.
Worktree: `D:/project/pan-worktrees/action-unification-p6-20261106`.

本片只覆盖隔离环境能证明的真实 HTTP/stdio/CLI/QQ 消费链；已绿的
reconciliation、multi-entry、Chromium 不重复计入。执行仍复用 FinalService、
Run/Attempt/effect、owner fence、operation receipt 和现有 provider/channel
adapter，不连接真实账号、profile、8767/8768，也未部署。

## 具名链路矩阵

| 链路 | 兼容行为与证据 |
|---|---|
| canonical provider / run-now | owned HTTP/stdio provider fixture 实际消费冻结 Step，返回 provider receipt；同步 run-now 返回 accepted/dispatched/completed 与原 dispatchKey，历史保留 runId/status/error/result | `provider-consumer-green.xml`、P6 delivery/families/client tests |
| provider unknown / lost response | provider 返回 unknown/timeout 后 Run 保持 uncertain，operation/requestKey 原身份可查询，冷恢复不重复调用 | `test_P6_DELIVERY_API_01`、families unknown tests、CLI unknown receipt tests |
| provider cancel | 已接纳 Run 通过现有 control_run/cancel receipt，取消竞态不重放，冷查询保留 cancellation/effect | delivery cancel tests、job_final_clients lifecycle tests |
| Session/legacy queue | real standalone stdio → trusted IPC → owned Session provider，旧 flat 参数/返回仍保留 queueItemId、runId、status；accepted 与 completed 分开 | `test_P6_DELIVERY_SESSION_03`、job_final_clients stdio suite |
| QQ gateway/native | owned local HTTP upstream 证明 `/api/qq/send`, send_file, history, contacts, inbox, subscribe/unsubscribe 的 legacy wire shape，以及 gateway/stdio projection | `test_plugin_qq_integration.py` 与 delivery QQ tests中的可用项 |
| QQ unknown/no-replay | channel provider 的 lost/unknown receipt 保持 uncertain，冷恢复查询原 outbox，不重发 | delivery/families channel tests；nonebot-only native fixture见阻塞 |
| notification | WS/Session outbox 复用原 Run/effect；一次投递、unknown receipt、冷恢复/no-replay和历史字段均保留 | P6 delivery history/notification tests |
| CLI | real TCP isolated service，CLI list/create/update/run/cancel/pause/resume/history 与 401/unknown receipt 均保持旧返回；CLI 不绕过 owner/receipt | `test_job_final_clients.py` CLI suite、JSON shell operator tests |
| permission | stdio/HTTP/CLI caller grant revoked、missing token、gateway stale contract 和 disabled projection 都拒绝且无副作用 | provider-consumer-green.xml client/projection tests |

## Red-to-green / external boundary


补跑修复：仓库已有 `packages/qq/requirements.txt` 声明 `nonebot2[fastapi,websockets]>=2.4.0` 与 `nonebot-adapter-onebot>=2.4.0,<2.5.0`；owned `.tmp/p6-env` 已包含 `nonebot`。之前 setup error 来自 isolated runner 选用全局 Python 3.14，而不是该 owned env。改用：

```
python scripts/run_tests_isolated.py -- .tmp/p6-env/Scripts/python.exe -m pytest   tests/test_action_unification_p6_delivery.py   tests/test_plugin_qq_integration.py tests/test_job_qq_native_provider.py -q
```

两个 OneBot ack 节点及相邻 QQ HTTP/native/gateway/stdio 用例 **6 passed**；完整 provider/QQ/CLI/stdio 组合在 owned env 中 **106 passed**，0 failure/error/skip，见 `provider-qq-owned-env-green.xml` 与 `provider-consumer-owned-env-final.xml`。原全局环境红证据仍保留，作为环境选择错误的对照，不再作为当前验收结果。

完整首次组合 `provider-consumer-original-red.xml` 共 106 项，其中只有 2 项 setup
error，0 个业务 failure：

```
ModuleNotFoundError: No module named 'nonebot'
tests/test_job_qq_native_provider.py::test_P6_DELIVERY_CHANNEL_01_original_qq_http_plugin_synthetic_onebot_ack_and_no_send[False/True]
```

这两个 setup error 已由仓库现有 owned env 路径修复；没有安装新依赖、没有伪造 QQ native 消费。原红保留为环境选择对照，当前 owned env 组合全部通过。QQ HTTP/gateway/stdio 的可用链路和 legacy wire shape 均已验证。

unknown/no-replay 路径保持红证据和现有语义：任何没有可信 provider receipt、原始
Run/Attempt/effect 或 durable outbox receipt 的结果都不会被转成成功，不会自动重发。

## 验证边界

- 使用 isolated runner、临时 profile 和 owned local HTTP/stdio/CLI fixtures。
- 未接触真实 provider、QQ 账号、model CLI、真实 profile/data、8767/8768。
- 远端 CI、Linux/macOS、部署和人工验收未运行。
- 之前候选遗留的 `bulk-stdio-rerun-20261107.xml` 与 `reconcile-original-red.xml`
  仍为未跟踪保留证据，未纳入本片计数。

本片不改变已批准的产品能力边界，只封存 provider/QQ/CLI 的真实隔离验收与明确的
nonebot 外部阻塞。正常 hook 提交候选，不 push。
