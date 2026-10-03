# Pan Terminal WebSocket 桥 —— 接口与协议 v1（P2 WS 第一批，2026-10-04）

> 任务：T-TERMINAL-PTY-20261003 的 **P2 WS 桥第一批**。依据 MA 已决任务书
> `docs/design/PAN_TERMINAL_WS_BRIEF_20261004.md`（含顶部「2026-10-04 MA 拒绝
> 编码校准」），把**已接受**的同步控制层 `TerminalService` 经 WebSocket 接到
> Pan；**不重新设计核心**。
>
> 工作树：`D:/project/pan-worktrees/terminal-ws-implement-20261004`
> （branch `implement/terminal-ws-20261004`，起点 `d0e66503` + MA 校准 `cc9d76f5`，
> 生产代码基线 `8b86dba8`）。
>
> 交付：`packages/web/terminal_ws.py`（新增）、`tests/test_terminal_ws.py`（新增）、
> 本文件（新增）、`audit/terminal/implementation/ws/**`（新增证据）、
> `packages/web/server.py`（**薄接入**：路由注册 + lifespan 嵌套）、
> `packages/web/terminal_api.py`（**必要小修**：公开 `check_ready` 供 WS 复用 +
> 快照 `note` 有界投影）。
>
> **本批不含**：前端接线、MCP、完整 Pan 启动、部署、跨进程/多用户鉴权。

## 1. 范围与写边界

| 文件 | 性质 |
| --- | --- |
| `packages/web/terminal_ws.py` | **新增**：WS 路由 + 握手前 gate + 连接/队列/收尾 + lifespan helper |
| `tests/test_terminal_ws.py` | **新增**：确定性门控 + 1 条真实隔离 ASGI/ConPTY 链 |
| 本文件 | **新增**：协议 v1、错误分类、所有权与队列纪律 |
| `audit/terminal/implementation/ws/**` | **新增**：证据（新目录，旧证据不动） |
| `packages/web/server.py` | **薄接入**：`include_router(terminal_ws.router)`、内层 `websocket_lifespan` |
| `packages/web/terminal_api.py` | **必要小修**：`TerminalRuntime.check_ready()` 公开化（WS 复用同一 ready 门）；`project_snapshot` 的 `note` 上限 64 → `NOTE_MAX`(4096) 且新增 `note_truncated` |

未改：核心 `service/attachments/launcher/runner/IPC/emulator/registry/contracts`、旧测试/旧证据、
MCP、前端源码、依赖清单与锁、`.workflow`。

`terminal_ws` **禁止** import `packages.web.server`（避免循环依赖）；依赖方向单向：
server → terminal_ws → terminal_api → core.terminal.service。

## 2. 握手：**accept 前**拒绝（MA 校准 A）

`WS /ws/terminal/{terminal_id}`，可选 query `cursor`（初始绝对游标，缺省 `0`）。

**全部拒绝都发生在 `accept` 之前**；支持 `websocket.http.response` 扩展时发送
**标准 HTTP 状态** + 静态 JSON `{"ok":false,"error":{"code":<静态分类>}}`；
**不**支持时回退为 accept 前 `close`（客户端观察为 HTTP 403）。

| 分类 | HTTP | 说明 |
| --- | --- | --- |
| `forbidden-origin` / `forbidden-host` / `forbidden-fetch-site` | **403** | 与 REST 同一 Origin/Host/Fetch 门（忽略 `X-Forwarded-*`）；配置非法 fail-closed |
| `invalid-terminal-id` / `invalid-cursor` | **400** | 不触 service |
| `unknown-terminal` | **404** | 合法 id 但对象不存在 |
| `terminal-disabled` / `not-ready` / `terminal-unavailable` / `closing` | **503** | 就绪门；业务准入 `busy` 同样 503 |
| `connection-capacity-exceeded` | **429** | 每 app 32 连接，**accept 前**预留 |

- **不向 HTTP 填 `4400/4403/4404/1013`**；这些私有码在 accept 前**不可达**（close code
  只存在于握手完成后的 close frame）。**握手成功后**的合法 close code 仍照用（见 §6）。
- **不为编码而先 accept**。
- 安全 gate 拒绝**零 service 调用**；合法来源为确认对象存在允许**一次**
  `service.get`，但被拒连接**零 attach/读/写**。
- **WS 无 content-type 要求**：`WebSocket` 没有 `method` 属性，实现**不**把不存在的
  `WebSocket.method` 当 POST 判定（这是与 REST `check_gate` 的唯一差别，其余同源）。

### 2.1 浏览器可见性（诚实边界）

浏览器原生 `WebSocket` **不保证**暴露握手拒绝的 HTTP 状态或 JSON —— 客户端只能看到
"连接失败"。具体分类可另用**已有 REST** 接口获取。本实现**不**宣称浏览器可读
denial body。

## 3. 连接身份与所有权

- 每连接由 **server** 生成唯一 `connection_id`（`conn_<uuid4hex>`）；**不**使用浏览器
  自报 `client_id`。
- server 持有 `LeaseToken`；连接默认 **observer**（`service.attach(role=observer)`）。
- 浏览器**永不**得到 runner token / pipe / secret，也拿不到 `revocation_id` 或任何
  可重建 `LeaseToken` 的凭据。消息**不能**覆盖 `terminal_id`（不一致 → `terminal-mismatch`）；
  `token`/`role`/`client_id`/`context` 等字段**不在白名单** → `unknown-field`。
- 首版（同一 Pan 本地信任面）允许任一合法连接显式 `claim`；**不**把关联
  workspace/session 当权限，**不**伪称用户认证。

## 4. 冻结消息 v1

发送为 **JSON 文本**；包络 `{v:1, type:<静态>, terminal_id, ...}`。
64 位游标 / input seq / generation 线上都是 **ASCII 十进制字符串**（uint64）；
真小整数（`rows`/`cols`/`size`/计数字段）除外。

### 4.1 入站命令（op 与字段严格白名单）

| op | 字段 | 语义与权限门 |
| --- | --- | --- |
| `claim` | 无 | 同连接已 control → **幂等**返回当前 generation（不反复颁发）；否则 `attach(role=control, client_id=connection_id)`，并撤销/释放该连接原 observer token |
| `release` | 无 | 撤销当前 token，签发同 connection 新 observer；**已是 observer 则幂等**（不制造 token） |
| `input` | `data_b64`、`generation` 必需，`seq` 可选 | 仅**本连接实际 control token + generation 精确一致**才写；observer/旧代/已撤销 → **零写** |
| `resize` | `rows`(1..500)、`cols`(1..1000)、`generation` 必需 | 同输入权限门；结果 PTY/引擎**分列** |
| `ack` | `next_seq` | 只允许**非递减**且 **≤ 已实际成功发送**的 output 末尾（**不是**入队 frontier） |
| `resume` | `cursor` | 显式从绝对偏移重拉；换 stream epoch，旧 epoch 的已排队 output **丢弃**（不混流） |
| `snapshot` | `timeout_ms`(1..5000) 可选 | 复用 REST 128KiB 上限与机器字段投影；**不**自动改 raw 游标 |
| `ping` | 无 | → `pong`；**不是** runner owner-heartbeat |

未知 op → `unknown-op`；额外字段 → `unknown-field`；`v != 1` → `unsupported-version`；
`type != "command"` → `unknown-type`；非法 JSON → `invalid-json`。
**白名单校验先于权限门**（畸形帧不会被当作权限问题评估）。
所有错误**只含静态 code**，**不**回显请求或异常文本。

### 4.2 边界

- 单条**接收**帧 ≤ **256 KiB**（先测 UTF-8 字节）→ 超限 `frame-too-large`。
- `base64` **strict**（`validate=True`）→ 非法 `invalid-base64`。
- 解码后裸字节 ≤ **128 KiB** → 超限 `payload-too-large`。
- 游标/序号解析复用 REST 的 `_parse_uint64`：前导零允许并规范化；float/bool/符号/
  科学计数/十六进制/空白/超 uint64 全拒。
- 二进制帧 → `binary-unsupported`。

### 4.3 出站事件

| type | 关键字段 |
| --- | --- |
| `hello` | `connection_id`、`role="observer"`、`control_generation=null`、`protocol{...}` |
| `output` | `seq`、`next_seq`（实际读取块的**绝对偏移**，十进制串）、`data_b64`、`size` |
| `gap` | `from_seq`、`to_seq`、`fresh_view_required=true` → **暂停**该连接 raw 读取 |
| `terminal-state` | `status`（`exited`/`lost`/`cleanup-failed`/不可用） |
| `claim-result` / `release-result` | `role`、`generation`/`control_generation`、`idempotent` |
| `input-result` | `accepted`、`size`、`status`、`seq`（**不**复制 `service.channel` 任意 Mapping） |
| `resize-result` | `accepted`、`status`、`rows`、`cols`、`pty_accepted`、`engine_confirmed`（**不含** `three_way_agreement`） |
| `ack-result` / `resume-result` / `snapshot` / `pong` / `error` | 各自语义 |

- `read` 每轮从当前 cursor 读 ≤ **32 KiB**；空读**至少 50ms** 等待（不忙循环）。
- `gap` 后**必须显式 `resume`** 才恢复；**不**补零、**不**自动 reset、**不**杀 PTY、
  **不**宣称 parser 完整恢复。`ack` **不能**悄悄跨 gap。
- 终态发 `terminal-state` 并正常关连接（1000），**不**假 `output_complete`。
- `read` 忙（4 槽满）→ 暂缓重试，**不当 EOF**。
- 事件与命令回执**允许交织**（只有同一命令的回执顺序有保证）；客户端按 `type` 分派。

## 5. 队列、单 sender 与慢客户端

- **单 sender**：reader（供输出）、receiver（串行处理本连接输入）、sender（**唯一**
  发送者）共用一条有界 outbound 队列；**禁止**多个 coroutine 直接 `send_json` 交织。
- 上界：**≤ 4 MiB**（按 `len(text.encode("utf-8"))` 即**线上序列化字节**计，
  **含正在 send 的帧** —— 在途帧完成前不释放记账）**且 ≤ 256 项**。
  任一越界 → 判 **slow-client** 并断开（**1013**）；**不**阻塞 runner reader、
  **不**默默丢字节。
- 单次 send 等待上限 **2s**；超时/失败即停止该连接。
- 每 app 最多 **32** 连接，**accept 前**预留；预留必须被 `commit` 消费或 `release`
  归还（否则额度泄漏）。未收敛连接**仍占预算**。
- **慢客户端只影响自身**：另一连接照常收发（有用例断言连接 A 卡在 send 时连接 B
  的 `ping/pong` 往返正常）。
- 所有业务同步调用一律经 `runtime.call`（现有 **4 槽**），**busy 不排队**。

## 6. 收尾纪律

停 reader/receiver/sender → 等**共享 2s 预算** → 事件循环外
`service.release_attachment(server token)`（有界等待、保留迟到 worker/result）。

- **只撤销连接**：绝不 `service.close`/`detach`/停所有者心跳；**连接断开不杀同 PID 的 PTY**。
- `claim`/`attach` 线程完成前 disconnect：**迟到发出的 token 必须被真实回收**，
  **不**只丢 wrapper（收尾会把迟到 token 逐个 `release_attachment`）。
- 重复收尾**单飞**（复用同一 `_close_task`）；未收敛项保留 task 引用供管理器观测
  （`manager.snapshot().pending_cleanup`），**不把 `loop.cancel` 当清理证明**。
- owner 续约归 **service 独立心跳**，与连接/业务阻塞无关；`ping` **不**续租。

## 7. 生命周期嵌套（server 薄接入）

```
terminal_api.terminal_lifespan(app)      # 外层：REST 既有 20s 预算
  └── terminal_ws.websocket_lifespan(app)  # 内层：WS 共享 2s 预算
```

退出时**先**停 WS 准入并关闭全部连接（**共享 2s**），**再**由外层走 REST **20s**；
两个预算**单列**，**不**把总计写成硬 20s。内层 `yield` 与 `stop_ws` 走 try/finally
（lifespan 体异常也请求收尾）。其它插件起停顺序/语义未改。

## 8. 共享投影小修（`terminal_api.py`）

- `TerminalRuntime.check_ready()`：把 `_check_ready` 公开，供 WS **复用同一实现**
  （避免复制一份不一致的 ready 门）。REST 行为不变。
- `project_snapshot`：`note` 上限 64 → **`NOTE_MAX` = 4096**，并新增
  **`note_truncated`**（截断时为 `true`）。理由：只截到 64 会**丢掉完整降级原因**，
  而降级说明正是客户端判断"能否继续信任流"的依据；有界截断时显式标注，
  **不**让"长 note"被误读成"完整说明"。其余字段与行为不变。

## 9. 确定性门控 ↔ 落点

| 任务书 gate | 断言要点 |
| --- | --- |
| 握手拒绝零 service 调用 | 坏/缺 Origin、Host、Fetch 全 403 且 `lease_calls()==[]`；合法正控才出现 `get`+`attach` |
| 拒绝用标准状态码 | id/cursor 400、不存在 404、ready 类 503、容量 429；`_HANDSHAKE_STATUS` **不含** 4400/4403/4404/1013 |
| 无 denial 扩展回退 | accept 前 `close`（`closed` 有值、`accepted` 假、`denial is None`） |
| server token 不外发 | `hello`/全部事件无 `revocation_id`/`rv-`/`pan-terminal-`/`token`/`pipe`/`client_id` |
| 伪造无效 | 消息覆盖 `terminal_id` → `terminal-mismatch`；`token`/`role`/`client_id` 字段 → `unknown-field`；observer 写 → `not-control` 且 `written()==[]` |
| 连接绑定 claim | 两连接抢占 `gen_b > gen_a`；旧代写 → `stale-generation` 且**零写**；真 control 正控写入成功 |
| claim/release 幂等 | 重复 claim 同 generation 且 `idempotent`；连续 release 不再 `attach` |
| resize 分列 | `pty_accepted is True`；`engine_confirmed in (True, False, None)`；**无** `three_way_agreement` |
| 绝对 cursor / ack 范围 | `seq`/`next_seq` 十进制串；ack 超**已发送**末尾 → `ack-ahead-of-sent`；回退 → `ack-regressed` |
| gap 暂停 + 显式 resume | gap 后无 output、游标不再前进；`resume` 后按新游标读；**无** `output_complete` |
| resume 不混流 | 旧 epoch output 在队列 → resume 后**不得**作为 output 发出（断言 payload 不含旧内容） |
| 快照 partial/F5 原样 | `fidelity`/`recovery` 原样；字符串 `"true"` → `None`；`reasons==[]` 不升级；`note_truncated` + 长度 4096 |
| 超大 frame/b64/队列 | `frame-too-large` / `invalid-base64` / `payload-too-large`；队列上限**含在途帧**、项数独立生效 |
| 慢客户端隔离 | A 卡在 send 时 B 的 `ping→pong` 正常；send 超时 → **1013** 且 `outbound.slow` |
| runtime 4 槽 | 满槽 → `busy` 且**不执行**业务调用；释放后可继续 |
| 迟到 attach 回收 | attach 线程阻塞中收尾 → 迟到 token **真实** `release_attachment`，`conn._token is None` |
| 收尾单飞/有界 | 两次 `close()` 同一 task、只释放一次；`service.close/detach` 零调用 |
| 终态 | `terminal-state` + 正常 1000 关闭；**不**假 `output_complete` |
| shutdown 交错 | WS 2s 与 REST 20s **单列**；`retained_service is True`（owner 保留） |
| lifespan finally | 体异常仍请求收尾；退出顺序 `ws-started → ws-stopped → rest-stopped` |

## 10. 已知边界 / 未验收（如实登记）

- **握手拒绝分类对浏览器不可见**（§2.1）：原生 WS 只暴露"连接失败"。
- 队列上界是**本层发送侧**纪律，**不**声称整个网络栈/WS transport 的硬 RSS 限额
  （transport 自身可能先缓存一帧）。
- 预算是**调用方侧有界等待**，**非** OS 硬 SLA：在途同步输入无法被 async 取消停止，
  核心输入门/lease 纪律仍是最终依据。
- **事件与命令回执允许交织**：本批只保证同一命令回执的相对顺序，不保证 output 不与
  回执交错（客户端按 `type` 分派）。
- 首版**不**含跨用户/远程鉴权；`PAN_TERMINAL_ALLOW_REMOTE=1` 只解除绑定禁用，**不是**鉴权。
- 未验收：真实浏览器 Origin/CSRF 全矩阵、Ctrl-C、durable 恢复、跨 sidecar 重启、
  长稳/背压、POSIX、`PAN_TERMINALS_DIR` 跨进程并发。

## 11. 变更记录

- **2026-10-04 首版**：`terminal_ws.py` + 测试 + 本文件 + 证据；server 薄接入；
  `terminal_api` 两处必要小修（公开 `check_ready`、`note` 有界 4096 + `note_truncated`）。
  实现期实测修正三处真实缺陷（均由本批新用例先失败暴露）：
  ① 容量**预留键未归还**导致额度泄漏、后续连接全被 429；
  ② `project_snapshot` 的 `terminal_id` 与包络字段**同名冲突**（`TypeError` → 快照全挂）；
  ③ 帧**白名单校验晚于权限门**（带伪造字段的帧被当作权限问题评估）。
  证据见 `audit/terminal/implementation/ws/README.md`。
