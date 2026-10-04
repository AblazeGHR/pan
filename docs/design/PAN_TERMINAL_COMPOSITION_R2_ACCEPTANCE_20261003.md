# Terminal 修正组合 ROUND2 接受记录

2026-10-03，MA 接受基础组合子集，不授予完整终端或 P2 接入许可。

## 固定对象与证据

- 实现：`3a579da64c249af35fb20fbfcab6340263d10fa6`；独立审查：`326a646e7adecbc0673de07428a52eaa7f8fce70`。
- 独立复跑：直连 13 passed（58.84s）、uv 隔离 13 passed（58.63s），均 rc 0；日志位于 `audit/terminal/implementation/composition-review/round2/logs/`。
- 独立 v1–v4：真实 Node 启动前/EOF/fatal/timeout 的 owner 与重试、真实引擎合并供料、真实会话 gap/心跳/尺寸/正常收尾/已测硬死布局、源码 blob 与 pins 核对通过。残留扫描为 0。
- MA 本轮核对审查树身份/clean、报告全文、入库双环境日志、summary、v4 源码锚定和范围；没有重跑组合或旧模块套件，不冒称亲跑。
- 隔离集成从 `26b7086b` 安全 fast-forward 到 `326a646e`，包含首轮历史、修正组合测试与独立审查产物；生产 `packages/` 零差量，与被审测试源码逐字节一致，diff-check 干净。旧证据未覆盖。

## 接受的能力与限制

接受真实 ConPTY + Runner + IPC + headless 引擎的基础装配：协议 A 单遍续流、applied cursor 驱逐时显式 gap、fresh-view 不自动 reset/不结束 PTY、独立连接心跳、尺寸分列、确认字段保守透出与 partial 不升级。

F1 启动失败 owner 与 F2 feed 合并修复在真实组合中闭环。吞吐只引用本机单一 monotonic 区间；实现组合 0.234s/2314 producer ops/11 engine batches 与独立引擎探针 0.078s/2252 ops/5 batches 是不同输入、不同场景，不混为跨机 SLA。真实 VT 未验证序列继续 partial，不放宽白名单。

## 三层收尾事实（不得互相替代）

1. **引擎对象可重试：已验证。** 单独 HeadlessEmulator 用例证明 close(0) 保留对象后，有界重试可收敛。
2. **测试宿主正常收尾：已验证；失败 owner 消费：未闭环。** 内联 launcher 只调用一次 emulator.close(8.0)，记录报告/异常后退出；没有消费失败 owner 的重试链。单独对象用例不代表该链已实现。
3. **生产 launcher：未实现、未批准。** 必须另行实现明确的引擎归属、启动失败清理、运行结束后的有界收尾、失败 owner 保留/重试与非零退出记录，不能用测试宿主或 Job 硬死兜底替代。

独立审查对基础组合判定接受、无返工项；上述失败收尾是后续生产宿主门，不倒写旧报告，也不将已接受子集撤回。

## 下一门与未验证

P2 服务/Web/MCP/registry 接入仍未批准。下一步需要明确授权生产 launcher 的独立实现与验收范围，再决定服务层接入。

Ctrl-C、真实 durable detach、浏览器渲染/fit、跨用户/主机、长稳/慢客户端背压、POSIX、跨 sidecar 重启恢复仍未验收。write budget 是发起取消的截止，不是硬 SLA；确认字段没有快照历史世代的原子绑定。硬死 Job 清理仅限已测布局，不泛化。

未合 main/practical/PR，未 push/build/restart，未运行 provider、账号或既有服务测试。
