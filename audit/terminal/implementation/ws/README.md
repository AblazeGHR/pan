# P2 WebSocket 桥 —— 第一批证据（2026-10-04）

基线 `cc9d76f5`（MA 拒绝编码校准；原任务书快照 `d0e66503` 未改写）。
**只改/新增**：`packages/web/terminal_ws.py`（新增）、`tests/test_terminal_ws.py`（新增）、
`docs/design/PAN_TERMINAL_WS_INTERFACES_20261004.md`（新增）、本目录（新增）、
`packages/web/server.py`（**薄接入**：路由注册 + 内层 lifespan）、`packages/web/terminal_api.py`
（**必要小修** 24 行：公开 `check_ready` + 快照 `note` 有界 4096/`note_truncated`）。

核心 `service/attachments/launcher/runner/IPC/emulator/registry/contracts`、旧测试/旧证据、
MCP、前端源码、依赖清单与锁、`.workflow` **未改**。未重跑全库/共享 core 套件。

## 1. 计数

| 层 | 命令（要点） | 结果 |
| --- | --- | --- |
| 新 WS 套件（直连） | `E:/software/miniforge/python.exe -m pytest tests/test_terminal_ws.py -o addopts= -q --timeout=600` | **30 passed**（8.42s） |
| 新 WS 套件（uv 临时环境） | `uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest tests/test_terminal_ws.py -o addopts= -q` | **30 passed**（9.02s） |
| 确定性门控逐条（29 项） | 同上 `-v -k "not real"` | **29 passed**（逐条名见 `evidence_direct/gates.txt`） |
| 真实隔离链（1 条） | 同上 `-v -k real` | **1 passed**（6.74s） |
| 相邻：REST 全套 | `pytest tests/test_terminal_api.py -o addopts= -q` | **38 passed**（已接受基线保持绿） |
| 相邻：真实 Pan app 导入/路由 | `pytest tests/test_addressing_compat.py -o addopts= -q` | **24 passed** |

环境：Python 3.12.12（E 盘 miniforge）、uv 0.9.14、uvicorn 0.52.4、websockets 17.0.1、
fastapi 0.141.1 / starlette 1.6.0、Windows（`sys.platform == win32`）。

依赖：仅 sidecar 专属 `npm ci`（`packages/core/terminal/emulator_sidecar/`，`added 2 packages`，
**锁未变** —— `git status` 该目录干净）+ 临时 `uv --no-project`。无 8768、无账号/provider、
无 stash/reset/主线操作。

**基线先失败说明**：本批是**新功能**（基线 `cc9d76f5` 无 `terminal_ws.py`），
按任务书「主要新 feature 不强制造旧版失败」，**未**制造基线先失败日志。
开发期真实缺陷由本批新用例**先失败**暴露并留痕于 §3。

## 2. 源码 blob 锚定（工作树内容哈希）

| blob | 文件 |
| --- | --- |
| `9c88e1464b7ed417001b334de940a8d3e1fe5f7a` | `packages/web/terminal_ws.py` |
| `df5a083c71e961862fff304640aef3541a028143` | `tests/test_terminal_ws.py` |
| `72eb0f9108e1b6a333710973bab4d63703f720f9` | `packages/web/terminal_api.py` |
| `14f2d2c06e9f47d4e05834e5bbf01f8c9258ca10` | `packages/web/server.py` |
| `9bc74e7752bc27e7ecfd6d4e70b96b923dc77af9` | `docs/design/PAN_TERMINAL_WS_INTERFACES_20261004.md` |

## 3. 开发期实测修正的三处真实缺陷（新用例先失败）

1. **容量预留键未归还 → 额度泄漏**：`reserve()` 占位与路由侧使用的预留键**不一致**，
   每个已 accept 的连接都泄漏一格，最终 32 格耗尽后**所有**新连接被 429。
   失败表现：连合法正控都被拒（`connection-capacity-exceeded`）。
   修正：`reserve()` 返回预留键，`commit`/`release` 成对消费；新增"预留归还后可再用"断言。
2. **快照包络字段同名冲突**：`project_snapshot` 自带 `terminal_id`，与 `_event(name,
   terminal_id, **fields)` 的位置参数**重复** → `TypeError: _event() got multiple values
   for argument 'terminal_id'`，**任何真实 snapshot 都发不出去**（表现为 `internal-error`）。
   修正：投影里 `pop("terminal_id")` 且 `_event` 对 `v`/`type`/`terminal_id` **不覆盖**。
3. **白名单校验晚于权限门**：带伪造字段（`token` 等）的帧先走权限判定，报
   `not-control` 而**不是** `unknown-field` —— 畸形帧被当成权限问题评估。
   修正：`_validate_command` 的字段白名单**先于** `_require_control`（现有顺序即如此，
   由该失败用例锁定）。

## 4. MA 拒绝编码校准（选项 A）的落地与实测

- 全部拒绝在 **accept 前**；支持 `websocket.http.response` 时发**标准 HTTP 状态** +
  静态 JSON，无扩展时 accept 前 `close` 回退 HTTP 403。
- 状态映射：安全 gate **403**、非法 id/cursor **400**、不存在 **404**、
  disabled/starting/closing/busy **503**、32 连接容量 **429**。
  用例断言 `_HANDSHAKE_STATUS` **不含** `4400/4403/4404/1013`。
- **不为编码而先 accept**；握手成功后 1013 慢客户端 / 1000 终态语义不变。
- **历史探测只保留事实摘要**（探针脚本已删除，不补造日志）：
  在本机 uvicorn 0.52.4 上，accept 前 `close(4403)` 被降级为 HTTP 403（close code 不可见）；
  `send_denial_response(status_code=4403)` 被拒（真实 uvicorn 返回 500，仅测试替身显示 4403）；
  `accept()` 后 `close(4403/1013)` 客户端**可见**该码。四种 uvicorn WS 实现
  （`websockets`/`websockets-sansio`/`wsproto`/`auto`）结论一致 —— 故"握手前可观测
  close code"不可实现，MA 校准 A 与该事实一致。
- **浏览器可见性**：原生 `WebSocket` 不保证暴露握手拒绝的 HTTP 状态/JSON，客户端只见
  "连接失败"；分类可另用已有 REST 获取。文档**未**声称浏览器可读 denial body。
  真实链的拒绝负控因此只断言"连不上"，**不**断言具体码。

## 5. 真实链（1 条，自有真实 WS 中文输入/抢占/断连同 PID）

隔离端口 **8791**（**非 8768**），真实 uvicorn + 真实 ConPTY/sidecar：
REST create → observer 零输入得 hello → ping/pong → claim → **中文**输入 accepted
且真实输出含 `echo`/中文 → resize 分列（`pty_accepted=True`、`engine_confirmed∈(True,False,None)`、
无 `three_way_agreement`）→ 第二连接 claim（`gen_b > gen_a`）且旧连接旧代写被拒 →
两连接全断后 **同 PID 仍存活**（连接收尾不杀 PTY）→ REST close 得 `status=exited`。
另含**握手拒绝负控**（坏 Origin → 连接失败）。

清理：仅对**自有**资源按同 handle PID + raw FILETIME 核验后由用例 `finally` 收尾；
跑完 `node.exe`（sidecar）残留计数 **0**。

## 6. 文件

- `evidence_direct/pytest_ws_full.txt` — 直连全量（含真实链）
- `evidence_direct/gates.txt` — 29 条确定性门控逐条名
- `evidence_direct/adjacent_rest.txt` — REST 38 + 真实 Pan app 24
- `evidence_uv/pytest_ws_full.txt` — uv 临时环境全量
- `evidence_real/real_chain.txt` — 真实链断言清单 + 结果 + 清理计数
- `summary.json` — 紧凑结构化计数

## 7. 未测 / 未撤回

未跑全库、共享 core/launcher/IPC/emulator 套件、REST 真实 ConPTY 链（本批只跑其**确定性**
38 项；真实链由 WS 用例自建）、浏览器真实 Origin/CSRF 全矩阵、Ctrl-C、durable 恢复、
跨 sidecar 重启、长稳/背压、POSIX、`PAN_TERMINALS_DIR` 跨进程并发、完整 Pan 启动。
这些**仍未验收**，不得据本批证据声称通过。
