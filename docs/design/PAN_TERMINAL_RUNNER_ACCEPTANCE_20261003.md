# Terminal P1 Runner 隔离集成验收

2026-10-03：接受固定 `a48e729d` 的 Runner 生命周期安全子集。独立审查 `ed5ab75b` 全文与树状态已核对，不代表服务接入或完整终端交付。

## 来源与验证

- 实现 `d48f967e` → `a704abbb` → `a48e729d` 无冲突进入集成分支，对应 `0a1493c2` → `e15b2bb0` → `373ca8fd`。
- 集成模块/client/tests/interface 相对源 a48 差量为空（文档澄清前核对）；生产代码未由 MA 修改。
- 独立复跑：38 直连与 38 uv 均通过；k1–k5 门控通过，source_blobs 5/5 与源提交一致，新日志已入库，旧缺失日志未补造。
- MA 集成树 uv 定向：7 项 N1–N4 新测试与核心 adapter 字面量守护，**8 passed / 45 deselected，1.88s，exit 0**。未重复 38 全量、129+8 或全库。
- 先失败证据：旧 a704 的 n1a/n2/n3/n4 四项独立复现；n1b 本来正确，defect=false 如实保留，不当作新增修复。

## 接受范围

心跳与 expiry 关闭权锁内线性化，取得关闭权后不得假 ok 续约；同一 close worker 消费与失败重试不叠加。非法 terminal_id 含启动失败路径不得派生状态文件。状态含真实自身 pid 与 raw FILETIME 字符串，unknown 不造身份，prior mismatch 仅标注、状态不是存活权威。唯一自有 tmp 与写串行，失败不清他人文件。finalize 锁等待及 close worker 等待纳入本次预算，失败保 owner、非零与可重试。

## 文档校准与接线约束

- W1 已在集成接口文档澄清：`run` 收尾的连接 join 3s、watchdog join 3s、finalize 12s 是分段预算。finalize 自身无 join；状态写位于其后，不能承诺整段收尾硬 12s。
- W2：0.05s 是实现边界，浮点可能影响分支；两侧都不得静默延长。生产 12s 不靠这个边界作为 SLA。
- 阻塞 write/close 必须用可追踪 worker，不能占服务事件循环；write budget 是发起取消截止，不是硬返回上界。backend 必须装配 retained-handle `backend.probe`。
- I-1..I-4 仍内部协议提案，需在组合验证后由 MA 冻结，不能直接当作批准的 P2 API。浏览器不得直连或持 runner token。
- 无引擎快照 unavailable 的边界仍有效；真实 HeadlessEmulator 与 Runner 尚未组合验证。resize_wait 只确认引擎，三方尺寸不得假一致。

## 未验收

Ctrl-C、ambient Job 下真实 durable detach、浏览器恢复、P2/Web/MCP/registry 接线、跨用户/跨主机及长稳均未验收。注入 detach 不替代真实宿主寿命证明，内核退出清理证据不泛化。

未合 main/practical/PR#6，未 push、build 或重启服务。下一步为隔离组合验证与接口收敛，不是服务发布。
