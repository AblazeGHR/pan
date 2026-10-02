# Pan Terminal IPC 与秘密存储接口（P1，2026-10-03）

- 任务：T-TERMINAL-PTY-20261003 的 **P1 传输/凭据基础**（不含 runner 生命周期、不含
  service/API/前端接线）。
- 工作树：`D:/project/pan-worktrees/terminal-ipc-implement-20261003`
  （branch `implement/terminal-ipc-20261003`，起点 `718567e4`）。
- 本 TA 的**写范围**（其余 core/backend/runner/service/emulator/server/MCP/frontend
  文件全部只读，未改 `requirements*.txt` / 锁文件）：

| 文件 | 内容 |
| --- | --- |
| `packages/core/terminal/ipc.py` | 帧协议（JSON-line）、消息 schema、双向认证（challenge-HMAC）、脱敏与有界诊断、有界调度（不迟到执行） |
| `packages/core/terminal/win_pipe.py` | Windows 命名管道 server/client（ctypes、overlapped 有界 I/O）、owner-only DACL 与安全对象原语、身份核验（同 handle PID + raw FILETIME + Wait） |
| `packages/core/terminal/secret_store.py` | DPAPI 用户作用域秘密文件（原子写、owner-only ACL、64 位身份精确、bootstrap hello、删除约束） |
| `tests/test_terminal_runner_ipc.py` | 纯逻辑 + 真实跨进程命名管道测试 |
| `tests/test_terminal_secret_store.py` | DPAPI/ACL/持久化/删除约束测试 |
| `audit/terminal/implementation/ipc/**` | 证据（机器可读 JSON + 生成脚本 + README） |

- 依赖方向（无环）：`contracts.py`（P0 冻结）← `ipc.py` ← `win_pipe.py` ← `secret_store.py`。
  `ipc.py` **不** import `win_pipe`（可在任意平台导入与测试）；`win_pipe.PipeConnection`
  以鸭子类型满足 `ipc.FrameTransport`。**本层无 backend/runner 依赖**，不 import
  `backend.py`/`runner.py`/`service.py`/`emulator.py`。
- 未改 `packages/core/terminal/__init__.py`（属公共核心 TA 的写范围）：使用方直接
  `from packages.core.terminal import ipc, win_pipe, secret_store`。

---

## 1. 角色与信任边界（先明确，再谈实现）

```
Pan 服务（client）                         runner（server，每终端一个进程）
  SecretStore.read_secret(term_id)           SecretStore.write_bootstrap_identity(...)
   → runner pid + raw FILETIME                → 等秘密出现（有界）→ verify_runner_identity(自身)
   → PipeClient.connect()                     → PipeServer.create()（FIRST_PIPE_INSTANCE）
   → GetNamedPipeServerProcessId              → accept() → GetNamedPipeClientProcessId
   → 同 handle PID+FILETIME+Wait 核验通过      → hello MAC 校验（compare_digest）
   → 才发 hello（HMAC）                        → ack MAC（双向）
```

- **连接方向的身份边界**：服务是客户端、runner 是服务器；双方各自用管道 API 读取
  **对端真实 PID**（不是对端自报值），再在**同一个进程句柄**上取 raw FILETIME 并
  `WaitForSingleObject(h, 0)` 判活。对端自报的 `pid` 只用于“与内核值一致”的交叉检查。
- **知道 `terminal_id` 不等于授权**：`terminal_id` 只决定对象名与管道名；授权由
  “DPAPI 秘密文件中的 256 bit token + 身份核验”共同承担（本层）；Web/MCP 入口检查
  属服务层（计划 §6.3），不在本层。
- **IPC token 与 attachment lease 是两套凭据**：IPC token 只用于 Pan↔runner 认证；
  lease 的 `revocation_id`/`generation` 由 `attachments.py` 独立生成与撤销，
  拿来当 IPC token 一定认证失败（测试 `test_ipc_token_is_independent_from_attachment_lease`）。
- **同用户边界（必须如实告知用户）**：DPAPI 用户作用域 + owner-only 文件/管道 DACL
  只隔离**其它 Windows 用户**。同一 Windows 用户下的进程理论上都能解密该秘密文件、
  也能连接本用户的命名管道。首版信任边界 = 同一 Pan 用户，**不承诺**“防同用户恶意
  软件”；该边界同时写在两个模块的 docstring 与本文件 §7。

---

## 2. `ipc.py`：帧协议

### 2.1 线格式

- 一帧 = 一行紧凑 JSON（UTF-8）+ `\n`；**不允许**跨行（JSON 转义已覆盖换行）。
- 单帧上界 `DEFAULT_MAX_FRAME_BYTES = 256 KiB`；解码后二进制负载上界
  `DEFAULT_MAX_PAYLOAD_BYTES = 128 KiB`。超界 → `FrameTooLargeError`（**拒绝**，
  不截断、不静默丢弃）；解码器在“没有换行但缓冲已超界”时立即拒绝（缓冲有界）。
- 首次违例后解码器进入**粘滞失败**：不“跳过坏帧继续解析”，连接必须关闭。

### 2.2 信封字段（按类型白名单，未知字段一律拒绝）

| 类型 | 字段 | 说明 |
| --- | --- | --- |
| `challenge` | `nonce`（64 hex = 256 bit） | 服务器 → 客户端；不含任何秘密 |
| `hello` | `mac`（64 hex）、`client_id`、`pid?` | 客户端 → 服务器；**token 不上线** |
| `hello_ack` | `mac`、`server_id`、`pid`（= 服务器看到的 client PID） | 服务器 → 客户端 |
| `request` | `request_id`（`r-<16 hex>`）、`terminal_id`、`payload`、`timeout_ms`、`deadline` | 业务请求 |
| `response` | `request_id`、`ok`（bool）、`payload?`、`error?` | 业务响应 |
| `event` / `error` / `ping` / `pong` | 见源码白名单 | 事件/错误/保活 |

- 版本：`v` 必须存在且等于 `IPC_PROTOCOL_VERSION = 1`，否则 `ProtocolVersionError`
  （缺失/更高/更低都拒绝，不做“宽容解析”）。
- 请求操作（`payload.op`）与负载白名单：`read`（`cursor`/`max_bytes`）、
  `input`（`data_b64`/`seq`）、`snapshot`（`timeout_ms`）、`resize`（`rows`/`cols`）、
  `lease`（`client_id`/`role`/`generation`）、`stop`（`reason`）。未知 op → 拒绝。
- 二进制走 `data_b64`（严格 base64 + 尺寸校验）；响应负载只允许“数据 + 序号 + 尺寸 +
  状态”形态（`data_b64/seq/size/cursor/next_cursor/gap/truncated/rows/cols/status/
  detail/snapshot/reason/total_bytes/first_retained_seq/ok`），未知字段拒绝。
- **精确整数**：`cursor`/`seq`/`size`/`total_bytes` 等可能超过 2**53 的字段一律以
  **十进制字符串**上线（消费用 `ipc.payload_int`），浮点被拒绝——避免 Number 舍入
  （与 64 位身份口径一致）。

### 2.3 拒绝矩阵（全部有测试）

| 输入 | 结果 |
| --- | --- |
| 非 UTF-8 / 非 JSON / 非对象 / 空帧 | `MalformedFrameError` |
| 未知 `type`、未知 `op`、未知字段、`request_id` 非法、`rows/cols` 越界、base64 非法 | `MalformedFrameError` |
| 版本缺失或不等于 1 | `ProtocolVersionError` |
| 单帧或解码负载超界 | `FrameTooLargeError` |
| 通道在帧未完成时结束 | `FrameTruncatedError`（断帧） |

服务端 `serve()` 遇到协议违例 → 记录（脱敏）并**结束该连接**，不进入业务 handler。

---

## 3. `ipc.py`：双向认证（challenge → hello → ack）

1. 服务器 `accept` 后先发 `challenge{nonce}`（256 bit 随机，单次使用）。
2. 客户端**先核验服务器身份**（fail-fast）：`GetNamedPipeServerProcessId` → 同 handle
   读 raw FILETIME → `WaitForSingleObject` 判活 → 与 DPAPI 秘密中的
   `runner_pid + created_at_filetime` **精确比对**；核验通过后才计算
   `MAC = HMAC-SHA256(token, "pan-terminal-ipc|1|hello|<term>|<nonce>|<client_id>|<server_pid>|<server_filetime>|")`
   并发 `hello`。**核验不通过（缺探针/缺 raw FILETIME/PID 不符/FILETIME 不符/进程已退出）
   一个字节都不发**，连接立即关闭。
3. 服务器用 `hmac.compare_digest` 校验 `hello.mac`（同一上下文，包括**服务器自身**的
   PID + raw FILETIME——服务器必须声明 `local_identity`，否则拒绝认证，避免发出
   无绑定的 ack）。校验失败 → `AuthenticationError`，**不进入业务 handler**。
4. 服务器可（可选）同时核验客户端身份：`GetNamedPipeClientProcessId` +
   `expected_peer_identity` 精确比对；配置了期望身份却没探针 → fail-closed。
5. 服务器回 `hello_ack`，其 MAC 绑定 `nonce` + **服务器看到的 client PID** + 客户端
   `client_id` + **客户端已核验的服务器身份（pid:filetime）**；客户端校验 ack MAC，
   并确认 `ack.pid == 自身 PID`（证明服务器把 ack 绑到了这条真实连接）。

性质与理由：

- **token 不上线**：`challenge/hello/hello_ack` 里没有任何 token 明文，只有 HMAC；
  因此即使有人 dump 帧或打印协议日志，也拿不到 token（测试断言线上字节无 token）。
- **重放无效**：MAC 绑定当次 `nonce`；重放旧 hello 到新连接必然失败（测试覆盖）。
- **身份绑定**：MAC 绑定 PID + raw FILETIME + terminal_id + 角色域
  （`hello`/`ack` 域分离），替换任一字段即失败（测试覆盖）。
- **无 TOCTOU**：管道连接存活期间，服务器/客户端进程被内核引用，PID 不会被复用；
  身份核验与凭据交换都在**同一条已建立连接**上完成。
- **握手有界**：`DEFAULT_HANDSHAKE_TIMEOUT = 5 s`；超时抛 `HandshakeTimeout`
  （`AuthenticationError` 子类），连接关闭，**handler 零调用**（测试覆盖）。
- 未认证前 `run_handler` / `serve` / `send_request` / `call` / `recv_message` 一律
  `AuthenticationError`（**唯一 handler 调用点 `run_handler` 自己带认证门**，不依赖调用方）。

### 3.1 业务帧绑定与 per-request 分派（r2：F1/F2）

- **可达性分层（复核 r1 校准 `3d046cfa`，本层与报告沿用、不夸大）**：直调 `run_handler`
  属**本进程公开接口的契约缺口**（调用者已在本进程内执行代码），**不是**远程/认证绕过；
  `serve()` 是唯一外部（管道）路径，认证前不可达（先 `handshake()`，失败即零 handler 调用）。
  认证后业务帧 `terminal_id` 未与会话绑定才是**真实缺口**（持 token 的对端可携带任意 id），
  其跨终端影响取决于尚未实现的 P2 handler 是否信任该字段（P1 无 handler 实现）。修复方式即
  下述**统一 fail-closed 门 + 严格绑定**。
- **一个 runner 一个终端**：`run_handler` 对每个请求依次执行
  ①认证 ②`validate_message` schema ③类型必须是 `request` ④
  `request["terminal_id"] == self.terminal_id`（不匹配 → `error: terminal-mismatch`，
  计入 `rejected_terminal_mismatch`）⑤期限复核；任一不满足**零 handler 调用**。
  不允许“按请求字段做对象路由”。
- **per-request 响应分派**：客户端每个在飞请求有独立响应槽（`request_id` 路由）；
  `call()` **只返回自己的响应**，别的请求的帧转投其等待者（`wait_response(request_id)` 可取回）；
  同一时刻只有一个线程做原始读（读者仲裁），其余等自己的槽位被唤醒——因此并发
  `call` / `send_request` / `recv_message` 不串线：`recv_message` 只返回请求/事件等
  “调用方可见”帧，不会偷走别人的响应。
- 重复 `request_id` 的业务帧**不去重**（连接完整性由内核管道保证、对端即调用方）：
  重放会重复执行；mutating 操作超时后禁止静默重试，重连后应先取快照。

---

## 4. `ipc.py`：有界调度与“不迟到执行”

- 请求携带 `timeout_ms`（1..60 000）与 `deadline`（epoch 秒）。
  服务端生效期限 = `min(sender_deadline, 入队时刻 + timeout_ms)`，**不随排队时间滑动**
  （否则等待越久预算越宽，等于允许迟到执行）。
- `RequestScheduler.claim()` 返回 `ClaimedRequest`（请求 + **入队固化期限**）；
  `run_handler` 对 `ClaimedRequest` **只认固化期限**，不在 dispatch 时重算放宽
  （复核 F12）。裸帧（`serve` 内联路径）仍按 `min(sender_deadline, now + timeout_ms)` 复核。
- 私有字段防伪造：帧与 payload 中以下划线开头的字段名一律拒绝
  （`_reject_reserved_fields`），客户端无法注入“可信期限”之类的内部字段。
- `run_handler` 是**唯一** handler 调用点：分发前复核期限，过期 → 回
  `error: expired` 且**不调用** handler；handler 异常 → 脱敏错误响应，连接不崩。
  handler 的**执行时长**不在本层设上限（文档如实标注：慢 handler 会推迟后续请求，
  客户端会超时并标记 `ambiguous_mutations`）。
- `RequestScheduler`（有界操作队列）：入队即过期 → `REJECTED_EXPIRED`；队列满 →
  `REJECTED_QUEUE_FULL`（拒绝，不阻塞、不无界增长）；`claim` 丢弃排队期间过期的请求
  并计数（`dropped_expired`）。
- 客户端 `PendingRequests`（有界）：超时后到达的响应一律丢弃
  （`late_responses`），超时的 **mutating** 操作标记为结果未知
  （`ambiguous_mutations` + `last_ambiguous_op`），调用方必须重新取快照，
  **禁止静默重试**。
- 诊断 `IpcDiagnostics`：计数器 + 有界错误列表（≤ 8 条、每条 ≤ 160 字符，全部经
  `redact`），`describe_message` 只输出结构元数据（`data_b64` 只留长度，MAC 只留
  “是否存在”），`data_b64`/MAC/token 永不进入日志文本。

---

## 5. `win_pipe.py`：命名管道

### 5.1 创建参数（结构性证据见测试 spy 用例）

```python
open_mode = PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED
if 本进程当前不持有任何同名实例: open_mode |= FILE_FLAG_FIRST_PIPE_INSTANCE
pipe_mode = PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS
DACL      = 当前用户 SID 唯一 ACE（FILE_ALL_ACCESS）
```

- `FILE_FLAG_FIRST_PIPE_INSTANCE`：名字已被别的进程占住时创建失败 →
  `PipeBusyError`（**拒绝共用同名管道**）；一旦本进程已持有实例则必须去掉该标志
  （Win32 硬性要求）。取消 accept 后会重建实例并**重新做占名检测**。
- `PIPE_REJECT_REMOTE_CLIENTS` **必须放在 dwPipeMode**：放进 `dwOpenMode` 会被
  Win32 判为 `ERROR_INVALID_PARAMETER(87)`（调试中复现）。
- 客户端连上后校验 `GetNamedPipeServerProcessId` 可得（拿不到 = 非本机管道/已断开）→
  fail-closed 拒绝。
- **名称所有权 = 本进程持有的全部实例**（含已交给活动连接的实例，r2/F4）：
  `_finish_accept` 不再递减计数，只有连接句柄**真正关闭**才归还；因此
  “活动连接存在时再 accept”不再因 FIRST 标志而必然失败。
- **实例池**：`create()` 建好 `min(max_active_connections, max_instances)` 个实例；
  `accept(timeout)` 用 `WaitForMultipleObjects` 等任一实例就绪（最多 63 个句柄一批）。
  并发连接上界因此是**可达能力**；池瞬时空（如客户端连上即断 ERROR_NO_DATA）会重建并重试。
- 并发有界：`max_instances`（默认 4）+ `max_active_connections`（默认 4，超限返回
  `None` 并计数，不排队）。
- 最后一个实例释放后再创建实例**重新带 FIRST 占名检测**：占名者存在即 `PipeBusyError`
  （释放-重占之间没有冒充窗口；运行期间本进程始终持有名称）。

### 5.2 有界 I/O 与可收敛取消

- 全部读写走 **overlapped I/O + 事件等待**，每次调用都有预算；读超时返回 `None`
  （未完成的读被保留、下次续等，**不丢字节**）。
- 写超时发生在半帧处 → 连接标记 `broken` 并抛 `PipeTimeout`：**不续写**
  （避免对端把半帧当完整帧解析）。
- `close(timeout)`：**每次调用**都幂等补发 `CancelIoEx`（r2/F6：close 与 ReadFile
  发起之间的竞态窗口内首次取消会落空，只有重试补发才能收敛），再等在飞操作归零
  （Condition 计数）与保留/孤儿操作**落地**，**只有全部落地才释放** `OVERLAPPED`
  并关句柄；未收敛 → `CloseReport(converged=False)` 并**保留资源**供重试。
- **`CloseHandle` 失败一律如实上报**（r2/F3）：connection / server / 待连接实例 /
  事件句柄四个阶段的关闭结果都并入 `CloseReport`；失败时 `closed=False`、资源保留、
  可重试收敛，绝不伪造“已关闭”。
- 读路径的竞态封闭（r2/F6）：准入后再查 `closing`（不发起）；发起后若 `closing`
  立即对该操作补发定向 `CancelIoEx`；读超时保留的操作与写超时的“孤儿”操作都会被
  `close` 等待落地后释放（内核在落地前仍会写 `OVERLAPPED`）。
- `accept(timeout)`：超时返回 `None` 且保留待连接实例（可重试）；`cancel_accept()`
  取消在飞连接并重建实例池；完成后处理与取消方共用同一把可重入锁。
- `PipeServer.close()`（r2/F5）：未 `issued` 的待连接实例**直接释放**（事件永不置位，
  等待只会假超时）；已 `issued` 的先取消再等落地；随后释放残留事件句柄；任何一步失败
  都 `converged=False` 且保留资源可重试。

### 5.3 身份核验原语

```python
probe_process(pid) -> ProcessProbe           # ALIVE+identity / DEAD / UNKNOWN
ProcessIdentityHandle.open(pid)              # 保留句柄：identity + is_alive() + matches()
terminate_verified_process(pid, filetime)    # 同 handle 核验后才终止；无 raw FILETIME 一律拒绝
```

- 判活**只**用同一句柄上的 `WaitForSingleObject(h, 0)`：`WAIT_TIMEOUT`=存活、
  `WAIT_OBJECT_0`=已退出。**不用**退出码 259（它同时是 STILL_ACTIVE 与合法退出码），
  也不用“再查一次 PID”（PID 复用无法这样防住）。`OpenProcess` 需要 `SYNCHRONIZE`
  才能 wait，否则 wait 失败 → 一律 `UNKNOWN`（**不得**当“已退出”）。
- 打不开/读不到 FILETIME → `UNKNOWN`（fail-closed 调用方拒绝动手）。
- 清理纪律：`terminate_verified_process` 在**同一句柄**上完成“读 FILETIME → 比对 →
  判活 → 终止”；任何一步不满足即拒绝（测试覆盖“FILETIME 不匹配 / 无 FILETIME /
  已退出”三种拒绝）。本 TA 的所有测试清理都走这条路径。

### 5.4 安全对象原语（`secret_store` 复用）

- owner-only 目录/文件**从创建时生效**：`CreateDirectoryW`/`CreateFileW` 直接带
  SECURITY_ATTRIBUTES（不是“先建后收紧”）；目录 ACE 带 `OBJECT_INHERIT|CONTAINER_INHERIT`。
- `dacl_entries(path)`（安全测试证据）、`owner_sid(path)`、`is_reparse_point(path)`、
  `replace_file_atomic`（`MoveFileExW` 替换，有界重试，绝不截断写）。
- `is_invalid_handle(...)`：`ctypes` 的失败伪句柄是**无符号** `2**64-1`，
  不能与 `-1` 比较（见 §8 缺陷 1）。

---

## 6. `secret_store.py`：DPAPI 秘密存储

- 路径：`<terminals_root>/secrets/<term_id>.secret`（密文）与
  `<term_id>.secret.hello`（runner 自证）；`PAN_TERMINALS_DIR` 与 registry 一致。
- 内容（**仅在内存中解密**）：`{schema_version, terminal_id, pipe_name, token(256 bit hex),
  runner{pid, pid_hex, created_at_filetime, created_at_filetime_hex}, created_at, updated_at}`。
  pid 与 raw FILETIME **同时**给十进制字符串与 `0x` 十六进制；读侧拒绝浮点、拒绝缺十六
  进制、拒绝两种表示不一致（fail-closed，禁止 Number 舍入）。
- 加密：`CryptProtectData`/`CryptUnprotectData` **用户作用域**（不带
  `CRYPTPROTECT_LOCAL_MACHINE`，带 `CRYPTPROTECT_UI_FORBIDDEN`），并绑定
  `terminal_id` 派生的 optional entropy（跨用途隔离）。
- 写：`ensure_secrets_dir()`（owner-only、拒绝 reparse）→ 临时文件（**同样 owner-only，
  从创建时生效**）→ `MoveFileExW` 原子替换；异常路径清理 tmp；从不截断写。
- 读：不存在 → `SecretNotFoundError`；DPAPI 失败 → `SecretProtectionError`；
  JSON/schema/枚举/token/管道名/终端号非法 → `SecretCorruptError`；ACL 复核不过 →
  `SecretSecurityError`。全部 fail-closed。
- **ACL 严格白名单**（r2/F7/F8）：`dacl_entries` 解析普通/object/callback/callback-object
  的 ACE（object ACE 的 SID 基址按本机实测 = 12 + GUID 数×16）；`verify_owner_only`
  只接受 ①当前用户 SID 的 allow 类 ACE ②任意 deny 类 ACE；**其余一律拒绝**：
  owner 查不到（`None`/查询异常）、NULL DACL、未知 ACE 类型、无法归属 SID 的 allow、
  其它主体/`None` 的 allow。不做“实现所有 ACE 类型”的盲目扩展——无法安全识别即拒绝。
- **写路径（r2/F9）**：唯一 tmp 名（`secrets.token_hex(8)`）+ `CREATE_NEW` 独占创建
  （撞名 → `FileExistsError` 换名重试，**绝不删除/覆盖他写者的文件**）→ ACL 复核 →
  `MoveFileExW` 原子替换；失败只清理**自己创建的** tmp（清理失败记入
  `cleanup_failures`，不假成功）。`write_secret` / `update_runner_identity` /
  `write_bootstrap_identity` / `delete_secret` 都在**按秘密路径命名的跨进程内核
  mutex**（`SECRET_LOCK_TIMEOUT = 5 s`，有界）内执行；`update_runner_identity` 是
  **锁内 read-modify-write**（并发提交不丢字段、不交叉发布）。
- **删除约束**（不变）：`verified_exit=True` 或显式 `caller_responsible`；断连不删除。
- `terminal_id` 必须匹配 `term_[A-Za-z0-9_-]{1,64}`（拒绝 `..`、分隔符、空白、非 `term_`
  前缀）；秘密/hello 路径或 secrets 目录是 reparse point（symlink/junction）→ 拒绝。
- **bootstrap 闭环**（r2/F10：核验是**强制入口**，不是“调用方记得检查”）：runner 先写
  `<path>.hello`（自身 pid + raw FILETIME 来自 `GetProcessTimes`）→ 服务
  `wait_for_bootstrap_identity`（有界）→ **默认 `verify=True`**：用
  `verify_bootstrap_record` 在同 handle 口径下（`GetProcessTimes` +
  `WaitForSingleObject`）确认 ALIVE 且 pid + raw FILETIME **精确匹配**才返回；
  探针缺失/`UNKNOWN`/`DEAD`/不符 → `SecretBootstrapUnverifiedError`（fail-closed，
  不重试掩盖）。`read_bootstrap_identity(..., verify=False)` 只是**诊断/取证**路径，
  不得用于绑定服务身份（docstring 明确标注）→ 写 DPAPI 秘密；runner
  `wait_for_secret`（有界 10 s）→ `verify_runner_identity(自身)`，不匹配即 fail-closed
  （不监听、退出非零）。argv **只**有 `--secret-file <path>`。
- **删除约束**：`delete_secret(...)` 必须满足其一——`verified_exit=True`（调用方已用
  内核证据确认 runner 已退出）或 `caller_responsible="<责任说明>"`（显式调用者责任，
  如“显式关闭终端”）；否则 `SecretDeletionRefused`。**断连/失联不构成删除理由**
  （detach 的闭环正是靠秘密跨 Pan 重启仍可解密）；删除同时清理 hello。
- 跨 Pan 重启：同一 Windows 用户的新 Pan 能解密同一秘密文件 → 得到 token 与 runner
  身份 → 直接重连旧 runner（`test_secret_is_decryptable_from_another_process`）。

### 装配示例

```python
# 服务侧（client）
secret = secret_store.SecretStore(terminals_root).read_secret(terminal_id)
conn = win_pipe.PipeClient(terminal_id, connect_timeout=5.0).connect()
session = ipc.IpcSession(
    conn, role="client", token=secret.token, terminal_id=terminal_id,
    expected_peer_identity=secret.runner_identity(),      # pid + raw FILETIME
    identity_probe=win_pipe.default_identity_probe,       # 可换注入验证器
)
session.handshake()                                       # 失败即不进入业务
page = session.call(ipc.OP_READ, {"cursor": cursor}, timeout_ms=5000)

# runner 侧（server）
store = secret_store.SecretStore(terminals_root)
store.write_bootstrap_identity(terminal_id)               # ① 出生自证
payload = store.wait_for_secret(terminal_id, timeout=10.0) # ② 等秘密（有界）
own = win_pipe.current_process_identity()
store.verify_runner_identity(terminal_id, own)            # ③ 身份自检（fail-closed）
server = win_pipe.PipeServer(terminal_id); server.create() # ④ FIRST_PIPE_INSTANCE
server_session = ipc.IpcSession(
    conn, role="server", token=payload.token, terminal_id=terminal_id,
    local_identity=own,                                   # 必须声明自身身份
    expected_peer_identity=<可选：固定调用者身份>, identity_probe=win_pipe.default_identity_probe,
)
server_session.serve(handler, max_requests=..., idle_timeout=...)
```

runner **生命周期**（PTY/Job/lease/detach/stop）不在本层：本层只提供“可注入 runner/service
的传输与凭据”。

---

## 7. 已知边界、未验证项与剩余风险

1. **同用户边界**：owner-only DACL + DPAPI 只隔离其它 Windows 用户；同用户进程仍可
   解密/复制秘密、也可连接本用户管道。首版信任边界 = 同一 Pan 用户，不承诺防同用户
   恶意软件（产品文档必须写明）。**object/callback ACE 对第二用户的有效访问语义未实测**
   （无第二账户）：F7 证明的是“该 ACE 存在于 DACL 且复核层拒绝使用该秘密”。
1b. **业务帧重放**：同一 `request_id` 重复发送会被重复执行（无 nonce/去重）；mutating
   超时禁止静默重试，重连后先取快照（r2 观察项，如实记录）。
1c. **handler 执行时长无上限**：期限只在分发前复核；慢 handler 推迟后续请求并触发
   客户端超时（`ambiguous_mutations`）——这是“内联=天然背压”的权衡。
2. **未实测的负验证（不得当作已解决）**：
   - “其它 Windows 用户/另一安全上下文连接被拒”：需要第二账户或第二安全上下文。
     **本 TA 未创建账户、未改任何账户权限**，因此只有结构性证据（创建参数 +
     当期 DACL 枚举无其它 SID 的 allow ACE + 自建临时文件加宽 ACL 后读取被拒）。
   - `PIPE_REJECT_REMOTE_CLIENTS` 的跨主机实测：无第二主机 / 未开任何网络监听。
     同样只有结构性证据（参数 spy + 常量断言）。
   - **未打开任何监听端口**：本层只使用本机命名管道，测试全程无 socket 监听。
3. **token 明文仍在管道内不出现**（HMAC 交换），但秘密文件对同用户可读：见第 1 条。
4. **不提供凭据轮换/吊销**：计划 §13 记 “rotate=可选，默认不 rotate”；本层未实现
   rotate（后续服务层在 detach/re-attach 时可选加入）。
5. **DPAPI 依赖用户配置目录健全性**：若用户 profile/DPAPI 主密钥损坏，
   `SecretProtectionError` fail-closed（不降级为明文存储）。
6. **runner 端 `expected_peer_identity` 默认未固定**：服务进程可能被重启（PID 变化），
   因此默认只用 token 证明客户端；需要固定调用者身份时由调用方显式传入。
7. **未做长稳压测**：并发连接上界（4）、诊断上界、队列上界均实现并有测试，但未做
   长时间 churn 压测。
8. **P1 其余门禁（原子 spawn/Job guard/权威仿真器）不在本 TA**：本层不声称任何
   “running 终端”语义。
9. **`wait_response` 超时 ≠ 放弃（R2B，语义说明，非缺陷）**：
   `wait_response(request_id, timeout=…)` 超时抛 `RequestTimeout` 但**保留**该请求的
   响应槽与 pending 登记；随后到达的响应仍会投递进槽位，可被后续 `wait_response` 取回
   （因此超时后不要再把同一 request_id 当成“新请求”等待）。`call()` 路径不同：超时即
   清槽，迟到响应计入 `late_responses`。槽位有界：≤ `DEFAULT_MAX_PENDING_REQUESTS = 32`
   （登记处超限抛 `OperationQueueFull`）。
10. **`accept()` 的补池假设（R2C，API 语义，非缺陷）**：实例池只在 `accept()` /
    `cancel_accept()` 内按 `min(max_active_connections, max_instances)` 补齐。若服务循环
    长时间**不调用** `accept()`，池被活动连接耗尽后，新客户端要等到下一次 accept 才被
    接起（客户端侧有界重试）。正常服务循环持续 accept，故这是模型说明。
11. **保留的低危观察（R2D/R2E，本次窄任务不做生产改动）**：
    - R2D：`PipeServer.close()` 重复调用时 `detail` 恒为 ``"closed"``（幂等成功、未伪造），
      与 `PipeConnection.close()` 的 “already closed” 早退属 API 表面差异；
    - R2E：`named_mutex` 在 `finally` 中抛 `ReleaseMutex` 失败、`write_file_owner_only` /
      `create_file_exclusive_owner_only` 在 `finally` 中抛 `CloseHandle` 失败，可能遮蔽体内
      原异常（关闭语义本身已如实上报）。以上仅列为后续整理项。
12. **R2A（测试可观测性，已修复）**：impostor 用例改为「可观测见证」——库记录的
    `ERROR_NO_DATA` 瞬时事件与真实 accept+读取**分开记录**，“零凭据字节”只在**有见证**
    的前提下断言（旧“精确计数”断言在瞬时路径下必然失败、且零字节结论会空集假通过）；
    并有字节级反空集锚点：身份匹配时必须**读到** hello。新增确定性回归
    `test_impostor_transient_connect_is_recorded_separately_from_reads`。

---

## 8. 本次实现中先复现、后修复的缺陷（测试驱动）

| # | 缺陷 | 复现 | 修复 |
| --- | --- | --- | --- |
| 1 | `CreateNamedPipeW`/`CreateFileW` 失败时返回 `ctypes.c_void_p(-1).value == 2**64-1`（**无符号**），旧写法 `int(handle) == INVALID_HANDLE_VALUE(-1)` 永远为假 → **创建失败被当成成功、占名检测形同虚设、客户端可能把“打开失败”当成连接** | 先跑 `test_pipe_name_squatting_detected_and_retryable`：外部进程占住管道名时 `PipeServer.create()` 未抛 `PipeBusyError`（`DID NOT RAISE`）；用独立调试脚本确认 raw `CreateNamedPipeW` 在同条件下返回 `2**64-1` + `ERROR_ACCESS_DENIED(5)` | 新增 `is_invalid_handle()`（识别 `0/-1/INVALID_HANDLE_VALUE/2**64-1`）并替换三处判定（管道创建、文件创建、客户端连接）；新增回归用例 `test_invalid_handle_values_are_detected` |
| 2 | `cancel_accept()` 关闭了**仍有在飞 I/O** 的实例句柄，且取消后不重建实例 → 客户端在窗口内连不上（`ERROR_FILE_NOT_FOUND`），取消方与等待方存在句柄竞态 | `test_accept_timeout_cancel_and_capacity_are_bounded` 在 `cancel_accept()` 后 `PipeClient.connect()` 超时失败 | 取消后等事件落地再关句柄，并**立即重建待连接实例**（重新做占名检测）；`accept` 的完成后处理与取消方共用同一把 RLock，取消路径不触碰已关闭句柄 |
| 3 | `RequestScheduler` 的本地期限在 `claim` 时按“当前时间 + timeout”重算 → 排队越久预算越宽，**可能迟到执行** | `test_scheduler_caps_remote_deadline_by_local_timeout` 期望 `claim(now=502)` 丢弃，实际返回了请求 | 入队时一次性固化生效期限（`min(sender_deadline, 入队时刻 + timeout_ms)`），`claim` 只与固化值比较 |
| 4 | `build_error`/`build_response` 无脱敏词表，调用方若把含 token 的异常文本直接传入会原样上线 | `test_describe_and_redact_never_expose_secrets_or_payload` 断言失败（token 出现在 error 文本里） | 两个构造函数新增 `secrets_` 参数，`IpcSession.run_handler` 统一传 `self._secrets`（含 token）；新增断言“诊断里必须出现脱敏占位符” |
| 5 | **取消后提前释放 `OVERLAPPED`/事件**：超时被保留的读操作、取消但未落地的写操作在 `close()` 释放句柄与事件后，内核仍会写那块内存 → **进程 access violation（段错误）** | 连跑同一套用例时出现 `Windows fatal exception: access violation`（exit 139），栈内线程停在 Condition wait | `_OverlappedOp.wait_complete()`：取消后**等事件落地**才释放；未落地则保留引用（`_orphan_ops` / 保留 pending 实例）并返回 `CloseReport(converged=False)` 供重试；`_drop_pending_locked` 改为 fail-closed 返回是否收敛 |
| 6 | `ConnectNamedPipe` 在“客户端连上又立刻断开”时返回 `ERROR_NO_DATA` → 旧实现抛 `PipeIOError`，**accept 循环被打死**（服务端不再接受任何连接） | 冒充者用例如有客户端极快 connect+close 时，服务端循环退出（`PytestUnhandledThreadExceptionWarning` + 后续 `assert received == [b""]` 失败） | `accept()` 把 `ERROR_NO_DATA` 当**瞬时状态**：丢弃该实例、重建并在同一预算内重试；新增回归用例 `test_client_that_leaves_before_connect_call_does_not_break_accept` |

另有两处**接口行为**在实现期调整（非缺陷）：解码器在传输边界即做 schema 校验
（未知类型/版本/字段在解码时拒绝）；客户端身份核验改为**先于**接收 challenge
（fail-fast：冒充者连 challenge 都换不到凭据）。

另有一处**跨 TA 的接口冲突**（本 TA 未越界修改）：`tests/test_terminal_driver.py::test_core_has_no_adapter_menu_literals`
断言 `packages/core/terminal/` 下 `.py` 文件**恰好 9 个**（P0 期不变式）。P1 按计划
§3.1/§12.2 新增 `ipc.py`/`win_pipe.py`/`secret_store.py` 后该计数必然变化，必须由
核心 TA / MA 更新（本 TA 的写范围不含该文件；本文件新增模块不含该用例禁止的
`cbc`/`codex`/菜单字面量，已核对）。

---

## 9. 测试分层与运行方式

```bash
# 纯逻辑（跨平台；本机 Windows 也会跑全部）
E:/software/miniforge/python.exe -m pytest tests/test_terminal_runner_ipc.py -q

# 秘密存储（Windows：DPAPI + ACL）
E:/software/miniforge/python.exe -m pytest tests/test_terminal_secret_store.py -q
```

- **纯逻辑层（无 ctypes）**：token/HMAC/绑定与重放、帧编解码与全部拒绝矩阵、精确整数、
  脱敏与 `describe_message`、有界调度与迟到响应、`IpcSession` 握手（fake transport）、
  IPC token 与 attachment lease 的凭据分层；r2 新增：`run_handler` 认证/绑定/类型
  （F1）、per-request 分派与并发纪律（F2）、固化期限与私有字段防伪造（F12）。
- **Windows 真实管道层（跨进程）**：自建 runner 子进程（DPAPI bootstrap → 身份自检 →
  `FIRST_PIPE_INSTANCE` → 认证 → 请求/响应）、两个客户端断连不杀服务器、错误 token、
  冒充者 PID/FILETIME、占名、握手超时、畸形/超大/断帧、过期 mutating 请求不执行、
  慢 reader 取消收敛、阻塞读取消并 join、accept 超时/取消/容量、同 handle 终止核验、
  DACL 与创建参数 spy、秘密哨兵扫描（报告/stdout/stderr/argv/config/registry/密文）。
- **秘密存储层**：加解密往返与密文不含明文、ACL 从创建时生效与外部 ACE 负例、
  损坏/缺失/身份不匹配 fail-closed、64 位身份精确、reparse/越界名拒绝、删除约束、
  bootstrap 闭环、跨进程解密、registry 不带 token。
- **r3 回归（1 项，R2A）**：`test_impostor_transient_connect_is_recorded_separately_from_reads`
  ——确定性构造 ``ERROR_NO_DATA`` 瞬时路径，断言「瞬时事件」与「真实读取」分开记录、
  读取观测可捕获字节（反空集）；配合 impostor 用例的 `_ImpostorWitness` / `_SendFrameCounter`
  见证（每次身份拒绝各自可证、客户端零帧发送、字节级 hello 锚点）。
- **r2 回归（34 项，`test_f1_*` … `test_f12_*`）**：F1/F2/F12 纯逻辑；F3/F4/F5/F6
  真实命名管道 + 句柄注入；F7/F8 真实 DACL（object/未知/AUDIT 类型）与注入（callback）；
  F9 真实并发 + CREATE_NEW 独占据名；F10 真实/伪造/探针不可用；F11 子进程自证身份 +
  核验清理 + 不广杀 decoy。
- 计数（r3）：两份文件合计 **115 项**（原 80 + r2 新 34 + r3 新 1）；直连与 uv 隔离
  均全绿（`evidence/r2/post_fix/*.log` 与 `r2_runs.json`；r3 见
  `evidence/r3/README.md`、`r3_freeze.json` 与 `post_fix/` 日志）。
- 证据：首轮 `audit/terminal/implementation/ipc/evidence/*.json`（未改动）；
  r2 在 `evidence/r2/`（pre-fix 失败阶段 + post-fix 六个变体 + 闭环矩阵 + 清理扫描），
  由 `audit/terminal/implementation/ipc/collect_r2_evidence.py` 重跑生成。

## 10. 变更记录

- `2026-10-03` 首版：`ipc.py` / `win_pipe.py` / `secret_store.py` 与两份测试、
  证据与本文档；含 §8 四处缺陷的复现与修复记录。
- `2026-10-03` **r2（独立复核返工，F1–F12 全部闭环）**：
  F1 `run_handler` 统一认证/schema/类型/terminal 绑定（零 handler 调用拒绝）；
  F2 per-request 响应分派 + 并发 call/send/recv 纪律；
  F3 `CloseHandle` 结果并入 `CloseReport`（connection/server/instance/event 四阶段，保留可重试）；
  F4 名称所有权含活动连接 + 实例池（并发连接能力可达）+ 释放后重占名检测；
  F5 未 issued 的 pending 直接释放、取消后重建、close 幂等收敛；
  F6 `close` 每次补发取消 + 发起前后 closing 复核（竞态封闭，`OVERLAPPED` 保留至落地）；
  F7 object/callback ACE 的 SID 归属 + 严格白名单（未知/外部/不可归属一律拒绝）；
  F8 owner 查询失败 fail-closed；
  F9 唯一 `CREATE_NEW` tmp + 只清理自有 + 锁内 read-modify-write；
  F10 bootstrap 身份**强制核验入口**（默认 `verify=True`）；
  F11 跨进程测试改为子进程自证身份 + 内核核验、launcher/runner 各自核验清理；
  F12 固化期限不可被 claim→handler 放宽、私有字段不可被客户端伪造。
  复核报告与 21 项负例原文见 `PAN_TERMINAL_IPC_SECURITY_REVIEW_20261003.md`
  （只读复核树，未修改）；返工证据见 `audit/terminal/implementation/ipc/evidence/r2/`。
