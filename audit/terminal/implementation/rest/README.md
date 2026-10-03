# P2 REST/lifespan 第一批 — 证据（2026-10-04）

- 任务：`T-TERMINAL-PTY-20261003/rest/impl/1`，工作树
  `D:/project/pan-worktrees/terminal-api-implement-20261004`（branch
  `implement/terminal-api-20261004`，起点 `79db1bb9`，生产基线 `3dafe3f8`）。
- 契约：`docs/design/PAN_TERMINAL_REST_INTERFACES_20261004.md`（本批成稿）。
- 结构：`evidence_direct/`（E 盘解释器直连）、`evidence_uv/`（uv 临时环境）、`run.json`（结构化）。

## 1. 写范围

| 文件 | 性质 |
| --- | --- |
| `packages/web/terminal_api.py` | 新增（REST 路由 + gate + runtime/lifespan helper） |
| `tests/test_terminal_api.py` | 新增（确定性 + 接线 + 真实隔离 ASGI 链） |
| `docs/design/PAN_TERMINAL_REST_INTERFACES_20261004.md` | 新增（接口/错误/边界） |
| `audit/terminal/implementation/rest/**` | 新增（本目录证据） |
| `packages/web/server.py` | **薄接入**：`include_router` + lifespan 起停 + 必要 import |

只读：核心 service/launcher/runner/IPC/emulator/registry/contracts、旧测试/旧证据、
MCP、前端、依赖清单与锁、`.workflow` 与其它 worktree。

## 2. 计数（分直连 / uv / 相邻回归）

| 层 | 命令（要点） | 结果 |
| --- | --- | --- |
| 直连（E 盘解释器） | `E:/software/miniforge/python.exe -m pytest tests/test_terminal_api.py -o addopts= -q` | **28 passed**（7.65s） |
| uv 临时环境 | `uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest tests/test_terminal_api.py -o addopts= -q` | **28 passed**（9.42s，uv 装 42 包，未改锁） |
| 相邻回归（纯逻辑） | `... -m pytest tests/test_addressing_compat.py -o addopts= -q`（导入 `packages.web.server`） | **24 passed** |

真实隔离 ASGI 链（Windows + sidecar，`npm ci` exact pin 已装）为上面 28 中的 1 条，
不需单独计数。**未**跑 service 完整真机/launcher/组合/core/全库。

## 3. 源码 blob 锚定（工作树内容哈希）

| blob | 文件 |
| --- | --- |
| `100d992e7a1e34e697b9223f22763153cbba649e` | `packages/web/terminal_api.py` |
| `dd2039806482f497ba7719d995ece4809fcb05d8` | `packages/web/server.py` |
| `c121534917b25147aedb5329bcbe8787792b1dae` | `tests/test_terminal_api.py` |
| `66e4b02447572ec020b763f25d9e127bf2fb98b5` | `docs/design/PAN_TERMINAL_REST_INTERFACES_20261004.md` |

## 4. 重点 gate 的实测事实（逐条）

- **被拒请求零 service 调用**：Origin 缺失/`null`/伪相似子域（`evil.127.0.0.1`）/错端口/
  错 scheme/带 path/userinfo/通配/空白，Host 不匹配与伪造 `X-Forwarded-*`，
  `Sec-Fetch-Site: cross-site|none|weird`，POST 非 `application/json`——全部 403；
  之后 `service.names() == []`（`reconcile`/`shutdown` 属 lifespan，另计）。
- **body 身份伪造零作用**：`context`/`created_by`/`trusted_local`/`terminal_id`/`token`/
  `shell_argv` → 422 `unknown-field` 且零调用；合法 create 的 kwargs 恰为
  `{rows,cols,cwd,workspace_id,session_id,context}`，`context is ServiceContext("web-local", trusted_local=True)`。
- **4 槽有界**：4 个在途（阻塞门见证）时第 5 个 → 429 `busy`；`inflight==4`；释放后 `0`。
- **取消后槽不提前释放**：cancel 1 个在途后 `inflight` 仍为 `4`（底层线程仍在跑），
  线程真正结束后归 `0`，随后可继续服务。
- **启动不假 ready**：`starting` → 503 `not-ready`；reconcile 抛错 → 503
  `terminal-unavailable` 且 `runtime.service is service`、`failure.error_type=="RuntimeError"`，
  响应体不含异常文本。
- **停止未收敛保引用**：`shutdown_budget=0.2` vs service `shutdown` 阻塞 1.0s → 报告
  `status="unconfirmed"`、`unconfirmed=True`、`retained_service=True`，REST 转 503 `closing`，
  迟到结果被 `_retire` 消费（`tracked_tasks` 归 0，异常不裸泄漏）；`shutdown` 抛错 →
  `status="failed"`、`unconfirmed=True`、`error_type` 静态，report 中无异常文本。
- **游标精确**：`abc/-1/+1/1.0/1e3/0x10/" 1"/""/全角/2^64` → 422 `invalid-cursor`；
  `0`、`007`≡7、`2^64-1` 通过；`max_bytes` `0/131073/65536.0/true/abc/""` → 422，
  `1` 与 `131072` 通过；service 实收真整数（`007`→`7`，max_bytes ∈ {65536,1,131072}）。
- **快照降级不升级**：`diagnostics.reasons==[]` 不升级（`fidelity` 原样 `partial`）；
  `reset_unconfirmed:"true"`（字符串）→ `null`；`applied_evicted=True` +
  `continuation_hint="fresh-view-required"`；`auto_reset_applied is False`；
  `engine` 为非字符串 Mapping → `null`（只透出公共标识字符串）；诊断白名单丢弃
  未知键与 `engine_error`/`node_binary`/`token`。
- **快照超界**：`serialized_screen` =128KiB → 200；=128KiB+1 → 502 `snapshot-too-large`
  （不截断、不伪造状态）。
- **秘密不外泄**：list/get 出口无 `pipe`/`token`/`pan-terminal-`/路径；异常
  `RuntimeError(r"SECRET-TOKEN-XYZ ... \\.\pipe\pan-terminal-1 ...")` → 500
  `internal-error`，响应体无 `SECRET-TOKEN-XYZ`/`pan-terminal-`/`.secret`/`Users`/`RuntimeError`。
- **close 重试可达**：首次 409 `cleanup-unconfirmed`（体无 `exited`），证据齐备后同一路由
  200 且 `status=="exited"`；两次都固定 `reason="explicit-close"`。
- **detach 不伪称 durable**：`detached=False` → 409 `detach-refused`；成功态 200
  `detached True` + `mechanism="runner-reported"`。
- **非法/未知 id**：格式非法 → 422 `invalid-terminal-id`（零 service 调用）；
  合法但不存在 → 404 `unknown-terminal`。
- **禁用零构造**：非 Windows（`platform=linux`）/非 loopback 绑定 → 503 `terminal-disabled`
  且 `runtime.service is None`；`PAN_TERMINAL_ALLOW_REMOTE=1` 才解除绑定禁用。
- **导入纯净**：全新解释器导入 `packages.web.terminal_api` 不建线程、不建 `PAN_TERMINALS_DIR`。
- **接线（真实 Pan app）**：`packages.web.server.app` 上 `GET /api/terminals` 返回 503
  `not-ready`（**非** 404），即路由确实注册且 gate 生效（未启动完整 lifespan）。
- **慢方法不阻塞事件循环**：0.4s 慢 `list` 期间 ticker 推进 ≥8 次（20ms 步长）。

## 5. 真实隔离 ASGI 链（1 条）

`test_real_isolated_asgi_chain_create_read_snapshot_close`：临时 root + 真实
`TerminalService`（真 ConPTY/Job/DPAPI/命名管道/headless 引擎），httpx `ASGITransport`
（**无监听 socket、不用 8768、不启动完整 Pan**）：

- 负控：错 Origin（`http://evil.local:8768`）→ 403（真实 app 的 gate 生效，非全拒假过）。
- `create` → 200 `status=running`、`pid` 为 hello 自证身份；响应体无 `pipe`/`pan-terminal-`。
- `read` → 200，`data_b64` 解出**真实默认 shell 初始提示**（REST 无 input，无新增旁路伪造生产能力）。
- `snapshot` → 200，F5 字段 bool-or-null，`continuation_hint` 属允许集合。
- `close` → 未确证时有界重试（409 `cleanup-unconfirmed`），最终 200 `status=exited`，
  秘密文件已删除；launcher 侧输出 `exit code=0 reason=exited engine-cleanup=converged`。
- 零残留：同 handle 身份核验（raw FILETIME）确认 runner 不再存活；进程扫描无残留
  `pan-terminal`/`sidecar` 进程。

## 6. 开发期发现的真实缺陷（先失败 → 后修）

`TerminalRuntime.call` 原为 `try/else: return await shield(task)`，**`return` 会跳过 `else`**，
导致**成功路径不回收槽位**——首次跑测试时四槽用例实测"取消 1 个后 `inflight` 应为 0 却为 3"，
整文件 `11 failed, 16 passed`。改为成功后显式回收（取消路径交给 task 完成回调）后
`28 passed`。契约文档 §6/§9 已记录该纪律（不得把回收写在 `try/else`）。

## 7. 未测 / 未承诺

WS、MCP、浏览器输入/resize、完整 Pan lifespan、provider/账号/网络服务、8768 监听、
远程（非 loopback）暴露与远程鉴权、跨主机/跨用户、POSIX、长稳背压、跨 sidecar 重启恢复、
共享 `PAN_TERMINALS_DIR` 的跨进程并发创建。

## 8. 偏离（详见契约 §7.1）

- 任务书 `rows/cols` REST 校验 `1..1000`，但已接受 service 内部 `rows<=500`；本批
  按任务书保持 `1..1000`，并把 service `invalid-size` 如实归 **422**（未擅自收窄为 500）。
- `snapshot-too-large` 状态码未指定，取 **502**。
- 就绪 code 细分为 `not-ready`/`terminal-unavailable`/`terminal-disabled`，避免与
  create 的 502 `startup-failed` 撞码。
