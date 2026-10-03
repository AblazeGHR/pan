# Terminal 组合子集接受与最小接线约束冻结

2026-10-03，MA 决定。依据固定组合 `7e6b30cb` 与独立审查 `cabacc7f`。本文件冻结内部接线约束，不是 P2 发布许可，不升级 IPC 帧版本。

## 接受与仍阻塞

接受真实 ConPTY / Runner / IPC / headless 装配可用性，协议 A 单遍续流字节守恒、headless 对拍、独立心跳与尺寸分列。独立审查 11 直连及 11 uv 通过，m1–m6 通过，blob/pins/日志锚定与无残留核对。TA、审查、MA 文档核对分列，MA 本事件不冒称亲跑组合套件。

F1 ready 前 EOF 的启动 owner 缺口、F2 每 feed 往返容量瓶颈已独立实证，原仿真器 TA 定向修复在执行；固定组合的全绿不替代这两项出口。吞吐数字仅本机探针，不能外推所有环境。F5 确认字段透出须由 Runner TA 实施、独立复验。组合证据暂保留源树，未在此事件导入其测试。

## I1：生命周期与 reason

- 当前 v1 的 stop.reason 是 ≤64 字符自由串，只有 detach 特判；冻结调用方词表 close / explicit-close / service-shutdown / lease-expired / detach，后续新增词需记录用途。不把文档词表误称现有协议枚举校验。
- 不新增 shutdown op。注入引擎由创建它的 **runtime 宿主（生产应为独立 runner 进程内的 launcher/bootstrap）**负责；TerminalRunner 类不隐式关闭借入对象。宿主在 runner.run 返回后有界关闭引擎、保留失败 owner 并记录重试。Pan 服务只是 IPC 控制者，不得将常驻引擎放在依赖 Pan 服务生存的进程里，否则违背 detach 语义。
- 硬死 Job 清理只引用已测布局及所有权前提，不当作任意部署的通用保证。生产 launcher 归属与失败收尾仍须组合实证。

## I2：快照与恢复边界

- 协议 A 使用 data_b64 + applied cursor；不使用 snapshot 字符串载荷。上限 128 KiB 是 serialized **原始字节**，不是 base64 长度；base64 与元数据合计仍必须满足 256 KiB 帧上限。
- serialized 超界不截断假恢复，显式 degraded；scrollback/尺寸增大可能触发上限，服务/前端须展示原因。
- enqueue 不等于 applied。突发积压不承诺即时 full；快照/barrier 超时要诚实降级，并可在引擎追平后重试。
- applied cursor 若落在 first_retained 之前，read 返回 gap；客户端不得从缺字节的位置继续假完整恢复。可请求新的有效快照重试，或明确降级 fresh-view。这里的 fresh-view 是显示层的显式恢复策略，**不是自动调用引擎 reset_baseline 或结束 PTY**。
- 未验证模式保持 partial；T17 已按能力矩阵校准。headless 对拍不代替真实浏览器对拍。

## I3：确认字段与原因来源

- detail 保持合法有界 JSON 与兼容演进；既有 fidelity / recovery / feed_lag / engine / note / lifecycle.* 不改名。
- 批准在 snapshot detail 增补 cursors_valid 与 reset_unconfirmed：来源已核验才 bool，缺失/异常为 null（unknown），不能默认为有效；unknown/false/reset 未确认不得作为完整续流依据。缩减 detail 时仍保留这些机器判定字段。
- node 未验证序列原因当前来源为 snapshot.note；diagnostics().reasons 是 Python 层原因集合，空不代表无降级。不解析 note 伪造稳定结构化 reasons API，不因 note 缩减而升级 fidelity。
- 不新增顶层白名单字段、不改共享 AppliedSnapshot。确认字段实施与失败路径测试另行验收。

## I4：服务与 attachment

- 浏览器不直连 runner，不持 DPAPI token；浏览器 attachment 与 owner heartbeat 是不同层。
- lease(control) 只由 Pan 服务使用；重连维持稳定 client_id，同 id 续约不增 generation，新 id 接管保持既有语义。稳定 id 不是权限证明。
- 心跳使用独立连接，不受慢 snapshot/input/close 阻塞。Web/MCP 授权与 attachment 单 writer 仍由 P2/P3 接入实现，不从 runner 同用户 token 信任外推网络安全。

## 下一出口

F1/F2 修复及独立复验、F5 实施及独立复验、固定组合测试对修复后的重验与真实 launcher 生命周期闭环后，才评估 P2。Ctrl-C、真实 durable detach、浏览器、长稳、跨用户/主机及 POSIX 仍未验收。未合主线/PR、push、build 或重启。

## 2026-10-03 后续验收状态（不倒写首轮）

F1/F2 已按 emulator r4 独立审查 417137fb 接受并隔离集成（见仿真器 r4 接受文档）。F5 实现至 29d535bc 经最终独立审查 7141d4dd 接受并隔离集成；MA 在集成树定向 12 passed / 38 deselected（见 PAN_TERMINAL_RUNNER_F5_ACCEPTANCE_20261003.md）。I3 的确认语义校准为保守近似，无历史世代原子绑定；unknown baseline 不造值、不约束，来源需自含 reset 未确认语义。

修正后真实组合与宿主 launcher 的生命周期/失败收尾仍待验证。生产 launcher 尚未实现/批准，本轮组合只能使用明确标注的测试宿主，不得把它称为服务接入完成；P2 门保持。
