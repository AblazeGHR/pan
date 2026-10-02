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
- 未认证前 `send_request` / `recv_message` / `serve` 一律 `AuthenticationError`。

---

## 4. `ipc.py`：有界调度与“不迟到执行”

- 请求携带 `timeout_ms`（1..60 000）与 `deadline`（epoch 秒）。
  服务端生效期限 = `min(sender_deadline, 入队时刻 + timeout_ms)`，**不随排队时间滑动**
  （否则等待越久预算越宽，等于允许迟到执行）。
- `run_handler` 是**唯一** handler 调用点：分发前复核期限，过期 → 回
  `error: expired` 且**不调用** handler；handler 异常 → 脱敏错误响应，连接不崩。
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
- 并发有界：`max_instances`（默认 4）+ `max_active_connections`（默认 4，超限返回
  `None` 并计数，不排队）。

### 5.2 有界 I/O 与可收敛取消

- 全部读写走 **overlapped I/O + 事件等待**，每次调用都有预算；读超时返回 `None`
  （未完成的读被保留、下次续等，**不丢字节**）。
- 写超时发生在半帧处 → 连接标记 `broken` 并抛 `PipeTimeout`：**不续写**
  （避免对端把半帧当完整帧解析）。
- `close(timeout)`：先 `CancelIoEx`，再等在飞操作归零（Condition 计数），**只有收敛才
  关句柄**；未收敛 → 返回 `CloseReport(converged=False)` 并**保留资源**供重试，
  不伪造关闭成功、不留后台 daemon 线程冒充完成。
- `accept(timeout)`：超时返回 `None` 且保留待连接实例（可重试）；`cancel_accept()`
  取消在飞连接并重建实例；完成后处理与取消方共用同一把可重入锁，避免“关掉仍有在飞
  I/O 的句柄”的窗口（该窗口在测试中被复现与修复，见 §8）。

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
  JSON/schema/枚举/token/管道名/终端号非法 → `SecretCorruptError`；ACL 出现其它 SID 的
  allow ACE → `SecretSecurityError`。全部 fail-closed。
- `terminal_id` 必须匹配 `term_[A-Za-z0-9_-]{1,64}`（拒绝 `..`、分隔符、空白、非 `term_`
  前缀）；秘密/hello 路径或 secrets 目录是 reparse point（symlink/junction）→ 拒绝。
- **bootstrap 闭环**：runner 先写 `<path>.hello`（自身 pid + raw FILETIME 来自
  `GetProcessTimes`）→ 服务 `wait_for_bootstrap_identity`（有界）读 hello 并**用内核
  探针复核 hello 的身份**（不依赖 `Popen.pid`，免受解释器包装层影响）→ 写 DPAPI 秘密；
  runner `wait_for_secret`（有界 10 s）→ `verify_runner_identity(自身)`，
  不匹配即 fail-closed（不监听、退出非零）。argv **只**有 `--secret-file <path>`。
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
   恶意软件（产品文档必须写明）。
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
  IPC token 与 attachment lease 的凭据分层。
- **Windows 真实管道层（跨进程）**：自建 runner 子进程（DPAPI bootstrap → 身份自检 →
  `FIRST_PIPE_INSTANCE` → 认证 → 请求/响应）、两个客户端断连不杀服务器、错误 token、
  冒充者 PID/FILETIME、占名、握手超时、畸形/超大/断帧、过期 mutating 请求不执行、
  慢 reader 取消收敛、阻塞读取消并 join、accept 超时/取消/容量、同 handle 终止核验、
  DACL 与创建参数 spy、秘密哨兵扫描（报告/stdout/stderr/argv/config/registry/密文）。
- **秘密存储层**：加解密往返与密文不含明文、ACL 从创建时生效与外部 ACE 负例、
  损坏/缺失/身份不匹配 fail-closed、64 位身份精确、reparse/越界名拒绝、删除约束、
  bootstrap 闭环、跨进程解密、registry 不带 token。
- 证据：`audit/terminal/implementation/ipc/evidence/*.json`（含用例清单、计数、环境、
  缺陷记录、边界声明），由 `audit/terminal/implementation/ipc/generate_evidence.py`
  现场生成（可重复）。

## 10. 变更记录

- `2026-10-03` 首版：`ipc.py` / `win_pipe.py` / `secret_store.py` 与两份测试、
  证据与本文档；含 §8 四处缺陷的复现与修复记录。
