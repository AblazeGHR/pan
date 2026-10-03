# Pan Terminal REST 接口与入口信任边界（P2 REST/lifespan 第一批，2026-10-04）

> 任务：T-TERMINAL-PTY-20261003 的 **P2 REST/lifespan 第一批**。依据 MA 已决任务书
> `docs/design/PAN_TERMINAL_REST_BRIEF_20261004.md`，把**已接受**的同步控制层
> `TerminalService` 接到 Pan；**不重新设计核心**。
>
> 工作树：`D:/project/pan-worktrees/terminal-api-implement-20261004`
> （branch `implement/terminal-api-20261004`，起点 `79db1bb9`，生产代码基线 `3dafe3f8`）。
>
> 交付：`packages/web/terminal_api.py`（新增）、`tests/test_terminal_api.py`（新增）、
> 本文件（新增）、`audit/terminal/implementation/rest/**`（新增证据）、
> `packages/web/server.py`（**薄接入**：路由注册 + lifespan 起停 + 必要 import）。
>
> **本批不含**：WS、MCP、浏览器输入/resize、生产部署、全库测试、新架构。

## 1. 范围与写边界

| 文件 | 性质 |
| --- | --- |
| `packages/web/terminal_api.py` | **新增**：REST 路由 + 入口 gate + runtime/lifespan helper |
| `tests/test_terminal_api.py` | **新增**：确定性门控 + 接线 + 真实隔离 ASGI 链 |
| 本文件 | **新增**：接口、错误分类、入口信任边界、生命周期/异步纪律 |
| `audit/terminal/implementation/rest/**` | **新增**：证据（新目录，旧证据不动） |
| `packages/web/server.py` | **薄接入**：`app.include_router(terminal_api.router)`、lifespan 起停各一行、必要 import |

未改：核心 `service/launcher/runner/IPC/emulator/registry/contracts`、旧测试/旧证据、
MCP、前端、依赖清单与锁、`.workflow`。`terminal_api` **禁止** import `packages.web.server`
（避免循环依赖），依赖方向单向：server → terminal_api → core.terminal.service。

## 2. 路由与响应包络

`APIRouter(prefix="/api/terminals")`，只做参数校验、出口投影、gate 与异步纪律。
成功 `200 {"ok":true,"result":...}`；失败 `{"ok":false,"error":{"code":<静态分类>}}`。
**失败体永不含**异常文本、路径、pipe、token、秘密或原始输入回显。

| 方法与路径 | 语义 | service 调用 |
| --- | --- | --- |
| `GET /api/terminals` | 列出全部终端（公共视图） | `service.list()` |
| `GET /api/terminals/{id}` | 单条公共视图 | `service.get(id)` |
| `POST /api/terminals` | 创建 | `service.create(rows,cols,cwd,workspace_id,session_id,context)` |
| `GET /api/terminals/{id}/read` | 按游标读输出 | `service.read(id, cursor, max_bytes=…)` |
| `POST /api/terminals/{id}/snapshot` | 协议 A 快照 | `service.snapshot(id, timeout_ms=…)` |
| `POST /api/terminals/{id}/close` | 显式关闭（`reason="explicit-close"`） | `service.close(id, reason="explicit-close")` |
| `POST /api/terminals/{id}/detach` | 显式 detach | `service.detach(id)` |

`service.describe()` **不是**公网接口，未接线。

### 2.1 请求字段（严格，拒绝额外字段）

- **POST /api/terminals**：JSON 仅 `rows`（默认 24）、`cols`（默认 80）、`cwd`（可空）、
  `workspace_id`/`session_id`（**仅元数据**）。**真整数**（非 bool）：`rows 1..500`、
  `cols 1..1000`（r2 统一到 `TerminalService.create` 已接受的门；任务书原写 `1..1000`
  为尺寸笔误，**不维持** `rows 501..1000` 的假可用）；字符串最长 4096。
  额外字段一律拒绝——尤其 `context` / `created_by` / `trusted_local` / `terminal_id` /
  `token` / `shell_argv`（**body 身份伪造零作用**）。
- **body 读取（r2）**：按 `request.stream()` **逐块**累计，累计超过 **64 KiB 立即**返回
  静态 422 `invalid-body`（**不**先调用无界的 `request.body()` 把任意大 body 读进内存）；
  非法 UTF-8 / 非法 JSON / 深嵌套触发 `RecursionError` 的坏 body 一律静态 422 `invalid-json`。
- **GET /{id}/read**：`cursor` 为 ASCII 非负十进制字符串（缺省 `"0"`，范围 uint64）；
  `max_bytes` 真实十进制整数 `1..131072`（默认 65536）。前导零允许（`"007"` ≡ 7）。
- **POST /{id}/snapshot**：JSON 仅 `timeout_ms`（真整数 `1..5000`，默认 5000）；可空体。
- **POST /{id}/close** / **detach**：空 JSON（`{}` 或空体）；非空字段拒绝。
- **调用上下文固定**：`ServiceContext(created_by="web-local", trusted_local=True)`。
  它来自**通过入口检查的本地信任面**——本层**不**伪称登录认证、**不**声称 workspace 授权；
  `scope` 只是元数据。
- **`validate_terminal_id` 在调用 service 前完成**：非法 id → 422（不触 service）；
  合法但不存在 → 404。

## 3. 出口投影（白名单，禁止未知键外泄）

| 接口 | 出口字段 |
| --- | --- |
| list / get / create / close | service 公共视图白名单：`terminal_id`、`status`、`owner`、`rows`、`cols`、`pid`、`process_created_at_filetime`、`detached`、`detached_at`、`scope{workspace_id,session_id}`、`exit{code,reason}`、`lease_grace_seconds`、`created_by`、`created_at`、`updated_at`、`attached`、`authorization`、可选 `heartbeat{client_id,beats,lost}`。**不**透出 pipe/token/未知新增字段 |
| read | `data_b64`（bytes → base64）、`seq`/`next_cursor`/`total_bytes`/`first_retained_seq` **十进制字符串**、`gap`（两端十进制字符串或 null）、`size`（普通小整数）、`truncated`、`fresh_view_required`、`cursor_advanced`、`zero_fill`（恒 `false`）、`status` |
| snapshot | `data_b64`（`serialized_screen` 原字节的 UTF-8 编码，来源 bytes 直编）、`cursor`（十进制字符串或 null）、`rows`/`cols`、`fidelity`、`recovery`、`feed_lag`、`note`、`engine`（**只透出公共标识字符串**，非字符串一律 `null`，不泛化复制任意 Mapping）、`cursors_valid`/`reset_unconfirmed`（**真 bool 或 null**）、`applied_evicted`、`continuation_hint`、`auto_reset_applied`（恒 `false`）、`diagnostics`（**白名单投影**） |
| detach | `terminal_id`、`detached`、`status`、`state_changed`、`mechanism`、`durability` |

- **read 不补零、不跳缺口、不重置引擎**：游标只取协议给出的 `next_cursor`。
- **snapshot 不因 `reasons == []` 升级**；`cursors_valid`/`reset_unconfirmed` 只接受真 bool
  （字符串 `"true"` → `null`）；applied cursor 驱逐时给 `fresh-view-required` 提示，不自动 reset、不杀 PTY。
- **snapshot `serialized_screen` 上限 128 KiB**：超界 → 502 `snapshot-too-large`，
  **不截断伪造状态**。
- **snapshot diagnostics 白名单**（已知公共事实）：bool——`engine_dead`/`reset_unconfirmed`/
  `cursors_valid`/`reason_overflow`/`feed_lag`/`closing`/`closed`；int——`reset_count`/
  `queue_bytes`/`queue_ops`/`max_queue_bytes`/`max_queue_ops`/`sidecar_pid`；str——`producer_frontier`/
  `expected_next`/`applied_cursor`/`baseline_cursor`/`sidecar_filetime`；range——`gap_ranges`/
  `duplicate_ranges`/`rejected_ranges`（转十进制字符串）；`reasons`（截断 64）；map——`overflow`/`counters`。
  **未列入的键一律丢弃**（含自由文本 `engine_error`/`engine_sticky_error`、路径 `node_binary`）。

## 4. 静态错误分类（HTTP 状态 → code）

| 状态 | code | 触发 |
| --- | --- | --- |
| 403 | `forbidden-origin` | Origin 缺失/`null`/不在 allowlist/伪相似子域/错端口/错 scheme/带 path/通配；**allowlist 配置非法 fail-closed** |
| 403 | `forbidden-host` | Host 与允许 origin 的 authority 不匹配（忽略 `X-Forwarded-*`） |
| 403 | `forbidden-fetch-site` | `Sec-Fetch-Site` 存在且不是 `same-origin`/`same-site`（`cross-site`/`none`/未知） |
| 403 | `forbidden-content-type` | POST 的 media type 不是 `application/json`（含 charset 允许） |
| 422 | `invalid-json` / `invalid-body` | body 非合法 JSON / 非 JSON object / 超 64 KiB |
| 422 | `unknown-field` | 出现额外字段（含全部身份伪造字段） |
| 422 | `invalid-field` | 字段类型/范围/长度不合法 |
| 422 | `invalid-cursor` / `invalid-max-bytes` | read 查询参数不合规 |
| 422 | `invalid-terminal-id` | 路径 id 不匹配 `term_[A-Za-z0-9_-]{1,64}`（不触 service） |
| 422 | `invalid-size` | service 侧 `StartupFailed("invalid-size")`（见 §6 偏离说明） |
| 404 | `unknown-terminal` | 合法 id 但记录不存在 |
| 409 | `cleanup-unconfirmed` | close 未获三项证明（**不**宣称 exited，**不**删凭据；重试正常可达） |
| 409 | `detach-refused` | detach 被拒（**不**伪称 durable） |
| 409 | `not-attached` | 本实例未持有该终端连接 |
| 429 | `capacity-exceeded` | service 容量准入（默认 8） |
| 429 | `busy` | **本层 4 槽在途已满**（不排队） |
| 502 | `startup-failed` | create 启动失败 |
| 502 | `snapshot-too-large` | `serialized_screen` 超 128 KiB |
| 503 | `terminal-disabled` | 非 Windows 或非 loopback 绑定未显式放行 |
| 503 | `not-ready` | 启动中（reconcile 未完成） |
| 503 | `terminal-unavailable` | reconcile 失败（保留 service 引用与静态失败状态，**不假 ready**） |
| 503 | `closing` | shutdown 已开始（REST 准入已关）；**也包括**已通过入口 gate、但在取槽复查时发现已关门的在途请求（r2） |
| 500 | `internal-error` | 未归类的异常（**零**异常文本回显） |

## 5. 入口安全（先于任何服务调用/实例创建）

1. **Origin 精确 allowlist**（**所有** REST 含 GET）：默认
   `http://127.0.0.1:<PAN_PORT>` 与 `http://localhost:<PAN_PORT>`，`PAN_PORT` 缺省 8768。
   允许域**不**由请求 `Host` 拼接。`PAN_TERMINAL_ALLOWED_ORIGINS`（逗号分隔）可替换；
   只接受合法 http/https origin（无 path/query/fragment/userinfo/wildcard/null）；**配置非法
   fail-closed**（不放行任何 origin），不静默回退放行。
2. **Host 必须与允许 origin 的 authority 匹配**；`X-Forwarded-*` 一律忽略（不靠转发头扩信任）。
   Origin 缺失/`null`/伪相似子域/错端口一律 403。
3. **`Sec-Fetch-Site`**：若存在只接受 `same-origin`/`same-site`；缺失**不单独拒绝**
   （测试/本地客户端可无，但 Origin 必须）。
4. **POST 必须 `application/json`**（允许 `charset`）；拒绝 form/text-plain 等 simple request。
5. **绑定**：`PAN_HOST` 默认 `127.0.0.1`；非 loopback 绑定**默认禁用**终端功能，
   `PAN_TERMINAL_ALLOW_REMOTE=1` 才显式解除该禁用（仍不能绕过上述 gate）。
   非 Windows 同样禁用。**禁用时零 Windows 资源构造**，routes 一律 503。
   ⚠️ 该开关**不是**完整远程鉴权；**不推荐远程暴露**。
6. 不触动全站 CORS/login/其他路由；浏览器**永不**获取 runner token/pipe/DPAPI 秘密；
   MCP 无 Origin 的接线**另批**，本批**不添加** bypass header。

## 6. 生命周期与异步纪律

- `terminal_api` 提供可独立测试的 `build_runtime` / `start_runtime` / `stop_runtime`，
  依赖工厂（`service_factory`）可注入；真实 server 只薄接入。
- **每 app 一个 `TerminalService`**；root 默认沿用 `TerminalService()` 既有默认
  （尊重 `PAN_TERMINALS_DIR`），**不另造共享目录**。
- **import 不派生进程**；非 Windows/绑定禁用不构造 Windows 资源。
- Windows 启动只构造 service 并安排**一次** reconcile；启动中 REST 503，reconcile 成功才
  `ready`，失败**保留 service 引用 + 静态失败状态**（不假 ready）。
- **所有同步 service 方法（含查询磁盘）离开事件循环**（`asyncio.to_thread`）。
- **每实例最多 4 个在途请求执行**（`DEFAULT_MAX_INFLIGHT`），**不设无界等待队列**；满则 429 `busy`。
  任务/线程结果均可追踪（`runtime.tracked_tasks`）。
- **HTTP caller 取消/断开不取消底层线程、不丢 task/owner**；槽位直到**真实完成**才释放；
  完成异常被消费。**不以 asyncio wrapper cancellation 假报资源结束**。
  （实现要点：`await asyncio.shield(task)`，且**成功路径也必须回收槽位**——不能把回收写在
  `try/else`（`return` 会跳过 `else`）。本批实测修正该缺陷。）
- `shutdown`：**先关 REST 准入**（`state=closing`，后续请求 503 `closing`），再在事件循环外调用
  同一 `service.shutdown(budget=20)`；对等待采用 `shield` + **总预算 20s**，超时如实报告
  `unconfirmed` 并**保留 service/tasks 引用**与完成消费，**不裸释放、不报成功**、
  不依赖 `executor.shutdown(wait=True)` 卡住 loop。既有其他 server 起停顺序/语义未重构。

### 6.1 r2 窄修（MA 亲验四项 + 局部修正）

- **① 消费真实服务报告**：`runtime.shutdown` 的返回值由 `_classify_shutdown` 从
  `service.shutdown(...)` 的**实际报告**归一，**不再**把"调用完成"当清理证明。
  `confirmed` 只在四条件**同时**成立时给出：
  ① 报告是 Mapping 且 `unconfirmed` 是**空列表**；② `secrets_retained` 为真 `False`；
  ③ `budget_exhausted` 为真 `False`（`True` 或缺失/非 bool 均不确认）；④ 本层**无在途请求、
  无未完成 reconcile**。其余（未知/畸形/缺证/有残留/超时/service 异常）一律 `unconfirmed`，
  报告只含**静态分类 `problems` + 计数 + 必要公共 terminal id**，**不复制自由文本**。
  `secrets_retained` 只有 `confirmed` 时才是 `False`（不得假报）。
- **② 请求槽与生命周期任务分类型**：请求槽由 `_SlotLedger` 按 **token 身份**记账；
  `_retire_request` 只在任务**确实持有请求槽**时释放一次（重复调用是 no-op，不偷减别的槽）；
  `reconcile`/`shutdown` 走 `_retire_lifecycle`，**绝不**扣请求槽。所有结果/异常都被消费。
- **③ 关门复查**：`call` 在**取槽与就绪复查的同一"无 await 区"**内再跑一次 `_check_ready`；
  已通过入口 gate、但此间 `shutdown` 关门的调用被拒（`GateRejected`），**零业务执行**；
  `_execute` 把 `GateRejected` 映射为**对应 503**（不是 500）。
- **④ normalization 稳健**：畸形 URL/authority（如 `http://[broken`、`http://host:abc`）
  由 `urlsplit` 抛出的 `ValueError` 一律捕获归 `None` → 请求静态 **403 `forbidden-origin`** /
  配置 **fail-closed**，不得冒泡成未捕获 500。origin **明确无 path**（尾斜杠 `/` 也算 path → 拒绝）。
- **单飞**：并发/超时重试**复用同一尚在途的 shutdown task**（不叠加）；已结束但未证明时允许
  对**同一 service 幂等重试**（新调用），迟到结果被真实消费而非永久缓存失败。
- **lifespan finally**：真实 server 的 `yield` 与其收尾经 `terminal_lifespan`（内部
  **try/finally**）接入；lifespan 体异常/取消也一定请求收尾。等待 shield 与引用保留规则不变。

## 7. 重点 gate 的落点（与任务书逐条对应）

| 任务书 gate | 落点 | 断言 |
| --- | --- | --- |
| 被拒请求**零 service 调用** | gate 在 `_enter` 内、任何 service 调用之前 | 全部 Origin/Host/Fetch/content-type 负例后 `service.names() == []` |
| body 身份伪造**零作用** | 额外字段白名单 + 固定 `ServiceContext` | 伪造 `context/created_by/trusted_local/terminal_id/token/shell_argv` → 422 且零调用；合法 create 的 kwargs 恰为 5 个数据字段 + 固定 context |
| 4 槽**有界** | `_Slots` 非阻塞计数器 | 4 个在途时第 5 个 → 429 `busy`；释放后可继续 |
| **取消后槽不提前释放** | 取消路径把回收挂到 task 完成回调 | cancel 后 `inflight` 仍为 4；线程结束后归 0 |
| 启动**不假 ready** | `startup` → `starting`；reconcile 成功才 `ready`；失败 `failed` | starting → 503 `not-ready`；reconcile 抛错 → 503 `terminal-unavailable` 且 `runtime.service is service` |
| 停止**未收敛保引用** | `shutdown` 超时/异常分支保 `service` + task | 超时报告 `unconfirmed=True`、`retained_service=True`，迟到结果被消费 |
| 游标**精确** | `_parse_uint64` / `_parse_bounded_int_str` | float/bool/负/科学计数/十六进制/空白/全角/超 uint64 → 422；`007`→7 |
| 快照**降级** | 不作升级、不自动 reset | `reasons==[]` 不升级；`reset_unconfirmed:"true"` → null；`applied_evicted` 提示；`auto_reset_applied=false` |
| 秘密**不外泄** | 白名单投影 + 静态错误 | 出口无 `pipe`/`pan-terminal-`/token；异常文本不回显 |
| **r2**：报告未收敛**不假 confirmed** | `_classify_shutdown` | 非空 `unconfirmed`/`secrets_retained=True`/`budget_exhausted` 缺失/畸形 → `unconfirmed`；干净报告 + 无在途/无 reconcile → `confirmed` |
| **r2**：请求槽**不被生命周期偷减** | `_SlotLedger` token + `_retire_request`/`_retire_lifecycle` 分型 | 4 在途时 shutdown 完成仍 `inflight==4`；重复 `_retire_request` 不二次扣减 |
| **r2**：关门后**零业务执行** | `call` 取槽同区复查 `_check_ready` | gate 已过 + body 暂停 + 关门 → 释放 body 得 503 `closing` 且 `service` 零调用 |
| **r2**：畸形 origin **静态 403** | `normalize_origin` 捕 `ValueError` | `http://[broken`、尾斜杠 `/` → 403/`config fail-closed`，不 500 |
| **r2**：lifespan **finally** | `terminal_lifespan`（try/finally） | 体异常仍请求收尾；`_execute` 对 `GateRejected` 映射 503 而非 500 |

### 7.1 已知偏离/矛盾（如实报告，不擅自选另一方案）

- **`rows` 范围不一致——已在 r2 收敛**：首版按任务书实现 REST 校验 `1..1000`，而已接受
  `TerminalService.create` 内部门为 `rows <= 500`。MA 判定任务书尺寸约束有误，r2 **统一到核心口径**：
  REST `rows 1..500` / `cols 1..1000`，**不维持** `rows 501..1000` 的假可用；核心未改。
  `service_error` 仍保留 `StartupFailed("invalid-size") → 422` 的映射作为纵深防御（REST 侧
  已不可达）。
- **`snapshot-too-large` 的状态码**：任务书只指定静态 code，未指定状态码。本批取 **502**
  （上游 runtime 产出的屏幕无法忠实服务），并在 §4 显式登记。
- **`Sec-Fetch-Site` 缺失不单独拒绝**：任务书明确（本地客户端/测试可无），因此仅有 Origin 强校验。
- **reconcile 失败的就绪 code**：任务书只写"closing/unavailable 503"。本批细分为
  `not-ready`（启动中）/`terminal-unavailable`（失败）/**`terminal-disabled`**（禁用），
  以免与 create 的 502 `startup-failed` 撞码。
- **`budget_exhausted` 缺失视为缺证**：MA 的判据写作"`budget_exhausted` 不 True"（字面允许缺失），
  本批按"缺证 → unconfirmed"取**更保守**口径：非 `False` 即不确认（已被接受的
  `service.shutdown` 总会返回该字段）。

## 8. 明确未承诺 / 未验收

- 不接 WS / MCP / 浏览器输入/resize / 前端渲染/fit。
- **远程暴露不安全**：`PAN_TERMINAL_ALLOW_REMOTE=1` 只解除绑定禁用，**不是**鉴权；
  无完整远程鉴权、无多用户模型、无 workspace 授权（`scope` 仅元数据）。
- 本层预算是**调用方侧有界等待**，**非** OS 硬 SLA；剩余线程受进程生存限制，
  **不**泛化为跨进程可重试。
- 未验收：真实浏览器 Origin/CSRF 全矩阵、跨主机/跨用户、POSIX、长稳/慢客户端背压、
  跨 sidecar 重启恢复、`PAN_TERMINALS_DIR` 共享目录的跨进程并发。
- 真实隔离 ASGI 链只在**本机 Windows + 隔离临时 root**验证；证据见
  `audit/terminal/implementation/rest/`。

## 9. 变更记录

- `2026-10-04` **首版**：`terminal_api.py` + 测试 + 本文件 + 证据；server 薄接入。
  实测修正一处真实缺陷：`TerminalRuntime.call` 原用 `try/else` + `return`，导致**成功路径
  `else` 不执行、槽位不回收**（首个四槽用例暴露：取消 1 个后 `inflight` 应为 0 却为 3）。
  改为成功后显式回收；测试由失败转通过。
- `2026-10-04` **r2 窄修**（MA 亲验四项 + 局部修正；只改 `terminal_api.py` / server 薄接入 /
  本文件 / 自身 tests / 新 `audit/.../rest/r2/**`；旧证据与 brief 不动）：
  见 §6.1。逐项证据见 `audit/terminal/implementation/rest/r2/README.md`
  （含 d8 `git archive` 副本上的**先失败**日志与修后直连/uv 计数）。
