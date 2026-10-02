# Pan Terminal：CBC 无中断网页原生 TUI 可行性（TA 探索报告）

- 日期：2026-10-03（本地时间；证据文件内时间为 UTC，UTC+8）。
- 工作树：`D:/project/pan-worktrees/terminal-cbc-explore-20261003`，分支 `explore/terminal-cbc-20261003`，
  起点 `35b6fb1abaae9d741b3042470aa32a8c356e431d`。
- 任务：T-TERMINAL-PTY-20261003 / CBC 无中断 TUI 探索（brief 四条并行工作之一）。
- 专属目录：`audit/terminal/cbc/`；本报告：`docs/design/PAN_TERMINAL_CBC_FEASIBILITY_20261003.md`。
- 未修改 `main`/`practical`，未推送，未重启或访问任何既有 Pan 服务（8768）、既有 Session/Worker/CLI thread。

## 0. 一句话结论

**今天 Pan 正在运行的 CBC headless stream-json 进程，没有任何原生 TUI attach（接入）通道；**但 CBC 2.161.1
自带一套**同一进程内**的“网页原生 TUI + 结构化控制面”组合（`--serve`/`daemon` 的 Web UI〔含 xterm.js 终端〕
+ HTTP REST/SSE + ACP over HTTP/SSE），已实测可用。要让“网页原生 TUI”成立，Pan 必须把 CBC 会话的
runtime 所有权迁移到 CBC 自己的 daemon（常驻服务）上，用其 HTTP/ACP 通道做结构化旁路；这不是对现有
`Worker`+stream-json 运行时的小改动。Windows 上 `--bg` 不能作为过渡捷径：shell 作业实测随 launcher
退出即不可用（model 作业因隔离环境无凭证，死亡原因无法区分，见 §6.2）。

## 1. 三个原始问题的直接回答

### Q1：已运行的 headless stream-json 同一 backend 能否接入原生 TUI？

**不能（实测，机制明确）。**

- Pan 当前 argv（`packages/core/adapters/cbc/adapter.py:218` `base_args()`）：
  `cbc -p --output-format stream-json --input-format stream-json -y …`
- 实测该进程会注册进 worker 注册表 `~/.codebuddy/sessions/<pid>.json`，内容为
  `{pid, sessionId: "interactive-<pid>", cwd, startedAt, kind: "interactive"}`——**没有 `endpoint`、
  没有 `log`、没有会话 JSONL 映射**（样本：`evidence/samples/headless-worker-registry.json`）。
- `cbc ps --json` 能列出它；`cbc attach <pid>`（真实 ConPTY 内运行）立即失败退出：
  `Error: Cannot attach: session has no endpoint or log path`（`evidence/20261003-003303/headless.json`）。
- 进程存活期间无模型调用（stdout 为空），说明失败点就是“没有 attach 端点”，不是鉴权。
- `--serve` 侧的 Worker 视图 API（`GET /api/v1/workers/:id/logs`）只能读注册表中的日志/transcript，
  不能向该进程注入输入；对 `interactive-<pid>` 这种注册项也没有可读的会话 transcript。

### Q2：从一开始就运行 native TUI，能否同时可靠供 Pan 使用结构化事件/控制旁路？

**可以，但只能通过 CBC 自己的 daemon/runtime 路径，且必须是“CBC 拥有 runtime”的架构（实测 + 官方文档）。**

CBC 2.161.1 提供两种“原生界面 + 结构化旁路”同进程组合，均已实测：

| 组合 | 原生界面 | 结构化旁路（同进程） | 实测 |
|---|---|---|---|
| `cbc --serve`（daemon 亦为 `--serve` 进程） | 浏览器 Web UI：对话/编辑器/**xterm.js 终端**/Workers/Agent View/日志 | HTTP REST（120 条路径，含 OpenAPI）；`/api/v1/jobs/:id/stream`、`/api/v1/jobs/events`、`/api/v1/pty/:id/output` SSE | ✅ health/info/workers/sessions/jobs/PTY/Web UI/ACP 全部实测 |
| ACP（Agent Client Protocol） | 任意 ACP 客户端（如 Zed）；或 `--serve` 内 ACP over HTTP | JSON-RPC：`initialize` / `session/new` / `session/update` / 权限模式 config options | ✅ stdio `initialize` 与 HTTP `initialize`+`session/new` 实测 |

- Web 终端是真 PowerShell PTY：`POST /api/v1/pty` 创建（`shell: …powershell`），
  `…/input/send` 写入，`…/output` SSE 回显含 ANSI，`…/resize`（120×40→200×50）生效，`DELETE` 后列表为空
  （`evidence/20261003-003807/serve.json`）。
- ACP over HTTP 由同一个 `--serve` 进程提供（不需要额外 `--acp`）：`POST /api/v1/acp/connect` 取
  connectionId → `POST /api/v1/acp`（`initialize`、`session/new`）→ SSE 流式回包；`session/new` 直接返回
  新 `sessionId` 与模型列表（`evidence/20261003-004056/acp-http.json`、`evidence/acp-http-variants.json`）。
- 关键约束：该 API 是 **Beta**；默认密码认证（回环首次启动仍打印密码，`--auth none` 仅限隔离/CI）；
  `POST /api/v1/acp` 要求 `Accept: application/json, text/event-stream` 同时包含两者，否则 406。
- Windows 特有约束（随包官方文档 `dist/web-ui/docs/cn%2Fcli%2Fdaemon.md`）：
  “在 Windows 上，只有通过 `daemon start` 启动的 daemon 独立于发起进程存活。其他后台会话、shell 命令、
  hooks 和隧道均随所属 CLI 退出而回收；`--bg` 和 `daemon stop --keep-workers` 不会解除这项生命周期约束。”
  本机实测与之一致（见 Q1/§4.3）。

### Q3：同 ID resume 与原 PID/turn 连续性的差别

**平台把两者明确区分，Pan 现有 takeover 属于后者。**

- `--resume <sessionId>` 是**新进程**加载既有 JSONL 对话；Pan `takeover_command()`
  （`adapter.py:400`）正是 `cbc --resume <id>` + 新建可见 console（`takeover_job.py:46`，
  `CREATE_NEW_CONSOLE|4` 挂 Job Object）。它不保留原 worker 进程、不保留正在执行的 turn。
- daemon/job 模型显式暴露连续性的两个正交维度：`alive`（进程是否活着）与 `settled`/`state`
  （是否已终结）；`GET /api/v1/jobs/:id` 同时给 `pid`、`procStart`/`procStartFt`（进程指纹）、
  `workerGeneration`、`sessionId`。`POST /api/v1/jobs/:id/respawn` 是“用同一对话重启新 worker”，
  job 目录内的 `respawnArgs`（样本 `evidence/samples/bg-model.state.json`）保存了带 `--session-id` 的重启参数。
- 本机实测：daemon 侧 job 的 `stop` 后 `alive=false, settled=true, firstTerminalAt` 写入
  （`evidence/20261003-003705/daemon-job.json`）；未验证（无凭证跑模型 turn）in-flight turn 在 attach /
  respawn / daemon 重启下的存续，这属于“原 PID/任务连续性”的最终判据。

## 2. 安装版与官方来源事实

**installed-version fact（本机实测）**

- `cbc --version` → `2.161.1`；入口 `D:\node_npm\node_global\cbc.cmd` →
  `node_modules\@tencent-ai\codebuddy-code\bin\codebuddy`（`package.json` version `2.161.1`），
  node `v24.15.0`。
- `cbc --help` 关键能力：`-p/--print`、`--output-format/--input-format stream-json`、`--resume/-r`、
  `--fork-session`、`--permission-mode`、`--effort`、`--settings`、`--mcp-config`、`--system-prompt`、
  `--session-id`、**`--serve`**（HTTP + Web UI + REST + ACP over HTTP SSE）、**`--acp`**（ACP stdio，
  `--acp-transport stdio|streamable-http`）、**`--bg`/`--background`**、**`--exec`**（与 `--bg` 联合跑
  shell job）、`--remote-control`、`--swarm`。
- 子命令：`ps [--json]`、`logs [-f] <pidOrName>`、`attach <pidOrName>`、`kill/stop/rm/respawn`、
  `agents`、`daemon start|stop|status|restart|install|uninstall`、`doctor`、`install`、`cleanup`。
- 随包官方文档 798 份（`…/@tencent-ai/codebuddy-code/dist/web-ui/docs/`），本报告引用
  `cn%2Fcli%2Fdaemon.md`、`http-api.md`、`acp.md`、`web-ui.md`、`interactive-mode.md`。

**official-source fact（随包文档，版本随安装包）**

- Worker 注册表：`~/.codebuddy/sessions/<pid>.json`，字段 `pid/sessionId/cwd/startedAt/kind/url/mode/version/hostname`，
  存活用 `kill -0` 检测（`daemon.md`）。实测注册表含 `kind: interactive|daemon`。
- 后台会话 `--bg` 以 `--print -y` 运行，stdout/stderr 重定向 `~/.codebuddy/logs/{name}.log`；
  `attach` 语义是“附加到运行中的后台会话”。
- HTTP API 分层（`http-api.md`）：Runs、Sessions、**PTY**（create/list/SSE output/input/resize/destroy、
  兼容 WebSocket）、Workers & Daemon、**Jobs**（智能体实例：`/jobs`、`/jobs/events` SSE、
  `/jobs/:id/stream` SSE、`/jobs/:id/reply|stop|respawn|transcript`、`/jobs/resume|resumable`）、
  ACP（`/api/v1/acp`、`/acp/connect`）、进程/文件系统（E2B 兼容）等。
- 生命周期字段：`state=working|blocked|done|failed|stopped`、`status=busy|waiting|idle|stopped`、
  `tempo=active|idle|blocked`、`alive`、`settled`；“shell job 没有 session transcript，返回空流”。
- Web UI 技术细节：React + xterm.js + fit addon，终端为**独立 PTY 会话**、页面刷新后保持、最多 4 分屏；
  通信为 ACP over HTTP/SSE（非 WebSocket）；`/api/docs` 为 Swagger UI。
- 认证：`--serve` 默认密码认证，密码写入 `~/.codebuddy/settings.json` 并打印；`?password=` 只对
  `GET /` 与 `login` 有效；所有 `/api/v1/*` 需要 `X-CodeBuddy-Request: 1` 头（豁免路径除外）。

## 3. 实测环境、隔离方法与边界

- 所有探针进程由 `audit/terminal/cbc/probe_cbc_feasibility.py` 创建，使用：
  - 临时 cwd + **隔离 `CODEBUDDY_CONFIG_DIR`**（`%TEMP%\pan-cbc-probe-*`）；
  - 清空所有继承的 `CODEBUDDY_*`/`WORKBUDDY_*` 环境变量（避免继承本会话身份/代理）；
  - `DISABLE_TELEMETRY=1`；
  - 自选**已验证空闲的 loopback 端口**（`--serve` 52648 / daemon 53373 / 63242 / ACP-HTTP 57739 / 变体 56709、57759）。
- 运行器：`uv run --no-project --python E:/software/miniforge/python.exe --with pywinpty==3.0.5 --with psutil`
  （依赖只进 uv 缓存 `D:/tmp/uv-cache-pan-cbc`，不改全局与仓库依赖）。
- **未做**：Pan 服务/8768/任何既有 Session、Worker、CLI thread、用户 config 或认证内容的读写；
  未运行长时模型调用；未把 `%TEMP%` 之外的任何目录当数据根。
- “模型调用”边界：隔离 config 无凭证，故所有模型相关路径只验证到“鉴权前”的机制（会话创建、注册表、
  端点、生命周期），显式标注为未验证（§6）。
- 解释说明：任务书“不做服务/API/WS 探测”按“不探测既有/受保护服务”执行；本报告只探测**自己新建**、
  已完成清理的本机 loopback 测试服务（任务书同时要求“Windows 测试服务选已验证空闲的自有 loopback
  端口并记 PID/创建时间”）。

## 4. 实测记录（命令、观察、证据文件）

所有 run 目录在 `audit/terminal/cbc/evidence/`，文件名即证据 ID。

### 4.1 baseline（`evidence/20261003-003232/baseline.json`）

- `--version`→2.161.1；隔离 config 下 `ps --json`→`No active sessions.`；注册表为空。
- 隔离 config 初始即创建 `logs/`、`local_storage/`（仅临时目录内）。

### 4.2 headless stream-json 注册与 attach 拒绝（`evidence/20261003-003303/headless.json`）

- 命令：`node <entry> -p --output-format stream-json --input-format stream-json -y --model deepseek-v4.1-flash`
  （stdin 管道保持打开，无 prompt）。
- 6 s 后进程存活；PID 4040，create_time 1790958783.70（样本 `samples/headless-worker-registry.json`）。
- 注册 `{sessionId: "interactive-4040", kind: "interactive"}`，**无 endpoint/log**。
- PTY 内 `attach 4040` → `Error: Cannot attach: session has no endpoint or log path`，退出码 0。
- 强杀后注册文件残留（无优雅退出），说明 `ps` 的存活判定依赖进程探测，Pan 侧不能把注册表当权威状态。

### 4.3 `--bg --exec` shell job 生命周期（`evidence/20261003-003326/bg.json`、`003430/bg-lifetime.json`、`003451`、`003530/bg-observe.json`）

- `cbc --bg --name <n> --exec "ping -n 120 127.0.0.1"` 输出：
  `backgrounded · 3d1cdf84 · <n> (shell job)`、`PID: 47776`、`Log: <cfg>\logs\exec-3d1cdf84.log`。
- daemon 未介入时：
  - job 存储 `<cfg>/jobs/<shortId>/state.json` 写入 `state=working`，但 `updatedAt` 停在 launcher 退出前；
  - **PID 轮询从未观察到存活**：针对性 0.2 s × 20 s 采样（`003530` 时间线）在 launcher 退出前后均
    `pid_exists=false`；另一轮进程表扫描（`003451`）也未捕获到匹配进程；
  - exec 日志 0 字节；
  - `logs <name>`、`kill <name>` 均 `Error: Session not found`（这两个子命令按 shortId 解析，实测按
    `--name` 传入查不到；shell job 的 shortId 在被试时作业已不可用，无法验证其是否可解析）；
  - `cbc ps` 自始至终 “No active sessions.”。
- 结论（实测）：Windows 上裸 `--bg` 作业随 launcher 退出即不可用，与官方文档“bg 随所属 CLI 回收”一致。
- 附带观测：model 版 `--bg`（`evidence/20261003-003852/bg-model.json`）会生成
  `jobs/<id>/broker.json`：
  `{"pid": 1292, "attachSocket": "\\\\.\\pipe\\codebuddy-job-01a0fd7b-1292", "procStart": …, "procStartFt": …}`
  ——**原生 attach 的真实通道是 job worker 发布的命名管道**；但该 job 的 1292 进程随后也已不存在，
  state 停在 `working/starting…`（隔离 config 无凭证，不能区分“鉴权失败”与“随 launcher 回收”，见 §6）。

### 4.4 daemon（常驻服务）与 daemon 拥有的 job（`evidence/20261003-003552/daemon.json`、`003705/daemon-job.json`）

- `cbc daemon start --port 53373` → `Daemon started (PID: 36672, endpoint: http://127.0.0.1:53373)`；
  `daemon start` **进程退出后**（rc 0，3.0 s），后续独立 CLI 进程 `daemon status` 仍见其运行 →
  daemon 确实独立存活；`ps --json` 中 `kind: daemon`，带 `configuredPort/url/startupArgs`。
- daemon 进程身份（`003705`）：PID 5004，cmdline `…\bin\codebuddy --serve --permission-mode default`，
  cwd=隔离 ws → **daemon 就是 `--serve` 进程**。
- `POST /api/v1/jobs {prompt:"cmd /c \"echo <marker> > panmark.txt & ping -n 300 …\"", bash:true}`：
  - 返回 job `54f5305c…`，`alive:true`，`pid:4032`；
  - psutil 直接观测到 `cmd.exe` 4032 存活、cwd=隔离 ws；
  - 首次采样（派发后 <2 s）`panmark.txt` 真实出现且内容匹配 → **作业真执行**，非元数据伪造；
  - `GET /api/v1/jobs/:id` 状态随生命周期变化；`POST …/stop` → `stopped:true`，随后 PID 不存在、
    `state=stopped, settled=true, firstTerminalAt` 写入。
- 控制面局限（实测）：`POST …/reply {text, bash:true}` → `{"delivered": false, "saved": false,
  "notice": "Bash replies only run inside the session — press enter to attach and run it there"}`；
  `GET …/stream` 对 shell job 返回空流（与官方“shell job 无 transcript”一致）。
- CLI 侧 `cbc logs 54f5305c` / `cbc attach 54f5305c` 均 `Error: Session not found`：
  **CLI 的 attach/logs 不认 daemon/API 创建的 job**（重要负面事实）。
- 清理核验：`daemon stop` → 端口释放、无残留进程、`cbc ps` 空、注册表空。

### 4.5 `--serve` + PTY + Web UI（`evidence/20261003-003807/serve.json`、`openapi.json`）

- `--serve --host 127.0.0.1 --port 52648 --auth none`，PID 40548（create_time 1790959087.97）。
- `GET /api/v1/health` → `{pid:40548}`；`/api/v1/info` → version 2.161.1 / win32 / node v24.15.0；
  `/api/v1/workers`、`/sessions`、`/jobs` 均 200。
- `GET /` → 200，Web UI SPA（`<title>CodeBuddy Code Remote Control</title>`）。
- `GET /api/openapi.json` → **120 条路径**（保存为 `openapi.json`）；其中含：
  `/api/v1/pty*`（5 条，另有非 OpenAPI 的 ws 端点）、`/api/v1/jobs*`（14 条）、`/api/v1/sessions*`（10 条）、
  `/api/v1/acp*`（2 条）、`/api/v1/workers*`（3 条）、`/api/v1/daemon*`（4 条）、`/api/v1/process*`（8 条）、
  `/api/v1/runs*`（4 条）。
- PTY 实测（真 PowerShell）：
  1. `POST /api/v1/pty {cols:120,rows:40}` → `sessionId cc9447a7…, shell …powershell.exe`；
  2. `POST /pty/…/input/send {"data":"echo PAN-CBC-PROBE-…\r\n"}` → 200；
  3. `GET /pty/…/output` SSE → `event: output` + `data:{"data":"…"}`，**包含我们的 marker 与真实 ANSI 序列**；
  4. `POST /pty/…/resize {200×50}` → 列表显示 `cols:200, rows:50`；
  5. `DELETE /pty/…` → 204，列表清空。
- 清理核验：kill 后端口释放、进程消失。

### 4.6 ACP：stdio 与 ACP over HTTP（`evidence/20261003-003836/acp.json`、`003954`、`004056/acp-http.json`、`evidence/acp-http-variants.json`）

- `cbc --acp`（stdio ndJSON）发 `initialize` → 结果：`protocolVersion:1`、
  `loadSession:true`、`multitaskSupport:true`、`mainAgentSupport:false`、prompt/mcp 能力、
  `authMethods: [iOA, external, internal, selfhosted]`；**无需鉴权即可握手**。
- `--serve`（普通，不必加 `--acp`）上：
  - `POST /api/v1/acp/connect` → 200，返回 `connectionId` + `sessionToken`（token 未写入证据，已脱敏）；
  - `POST /api/v1/acp` + `acp-connection-id`：`Accept` 必须同时含 `application/json` 与
    `text/event-stream`（否则 406，服务端错误信息即该文案）；
  - `initialize` → SSE `event: message` + JSON-RPC result（能力同 stdio）；
  - `session/new` → 返回新 `sessionId`、`session/update`（`config_option_update`：permission mode 选项，
    含 default/acceptEdits/plan/auto/dontAsk/bypassPermissions…）与模型列表；
  - `DELETE /api/v1/acp` → 200 断开。
- `--serve --acp` 与普通 `--serve` 行为一致（ACP over HTTP 本就默认开启）。

## 5. 代码/机制推断（源码与 bundle 阅读，非直接实测结论）

- `bin/no-orphans.cjs` + `bin/windows-job.cjs`：CBC 在 Windows 上用 Job Object（`ensureKillOnCloseJob`）
  做子进程包含，注释明确“daemon/bg 进程必须比 launcher 活得久，但仍要清理自己的后代”；可通过
  `--no-orphans` / `CODEBUDDY_DISABLE_CHILD_PROCESS_CONTAINMENT` 控制。这与实测“bg 随 launcher 回收”吻合：
  bg 的父/所有者是发起 CLI，launcher 退出即回收。
- `jobs/<shortId>/`（`state.json`/`broker.json`/`inbox/`）是 job 所有权与 attach 通道的落盘形式；
  `broker.json.attachSocket` 指向 `\\.\pipe\codebuddy-job-<shortId>-<pid>`。
  **推断**：CLI `attach` 需要该 broker 记录/管道；daemon-API 创建的 job 没有该记录 → `Session not found`。
  本报告没有对该管道做直接连接测试（未验证项）。
- `dist/web-ui/assets/TerminalPaneView-*.js` 使用 xterm.js；与 §4.5 的 `/api/v1/pty` SSE 对应。
- Pan 侧现状（本树源码）：
  - CBC worker 由 `packages/core/worker.py:5913-5919` 以 `asyncio.create_subprocess_exec` +
    stdin/stdout 管道启动；
  - Pan 已有“控制消息”缝 `worker.py:6286-6306`（`interrupt/steer/compact/approval_response/
    permission_response/…` → `adapter.encode_control_message` 写入 stdin）；
  - **CBC adapter 未实现 `encode_control_message`**，故 CBC worker 目前只有 prompt 文本通道；
  - takeover 是 `cbc --resume` 新进程（`adapter.py:400-421`，`takeover_job.py:46` 挂 Job Object），
    不保留原 worker。

## 6. 未验证项与残余风险（不得当作已证实）

1. **模型 turn 相关**：隔离 config 无凭证，未跑任何真实模型 turn。因此未验证：`--serve`/`daemon` 下
   模型 job 的 `/jobs/:id/stream` 事件内容、审批（permission）事件在 Web UI/ACP 的实际往返、
   `session/prompt` 的完整流、以及 attach 后的输入控制权交接。
2. `--bg` 模型作业死亡原因二义性：既可能是“随 launcher 回收”，也可能是“无凭证启动失败”。
   需要带凭证的受控实验才能区分；本报告只把 shell job（无需凭证）的死亡作为机制证据。
3. CLI `attach` 未在任何场景成功：headless（无 endpoint）、daemon job（Session not found）、
   bg shell job（进程已死）。**没有证据表明 2.161.1 的 CLI attach 能在 Windows 上长期可用**；
   原生 attach 的可用样本仅剩 broker.json 命名管道机制（未直连验证）。
4. 未验证 pan 端“旧客户端迟到输入/尺寸控制权（lease/generation）”与 CBC ACP/PTY 的对照；
   CBC 侧 `hasClient` 字段存在（PTY 列表），但多客户端争用未测。
5. 未测 `--serve` 跨进程重启后 PTY 会话是否保留（官方说“页面刷新后保持”，不等于服务重启后保持）。
6. 未测 daemon 的 Windows 系统服务注册（`daemon install`，Task Scheduler）；未启用自动更新相关行为。
7. 未测 `jobs/:id/respawn` 与 `jobs/resume` 的真实对话恢复（需凭证）；只读到 `respawnArgs` 与文档语义。
8. 本机为单机单用户，未验证远程（非回环）绑定/隧道场景与 `--host 0.0.0.0` 的强制密码语义。
9. 未验证 CBC HTTP API 的版本兼容承诺（文档标 Beta）；升级 CLI 可能破坏字段/端点。

## 7. 验收场景对照（MA 关注项）

| 场景 | 结果 | 证据 |
|---|---|---|
| 原 backend PID 连续（TUI 接入不换进程） | **现状不可行**：headless 无 attach 端点；CLI attach 对 daemon job 失败 | §4.2、§4.4 |
| 运行中任务连续 | **未证实**；daemon job 有真实独立 PID 与 `alive/settled` 状态，可 stop；in-flight turn 在 attach/respawn 下未测 | §4.4、§6.1 |
| 操作权转交（Pan ↔ 网页 TUI） | **机制存在**：Web UI xterm/PTY API + ACP connection/session；但“同一会话在 Pan 与网页间交接”未实现也未测 | §4.5、§4.6 |
| 审批与输出事件保留 | 部分：daemon job 的 shell 输出**未进日志文件**（0 字节）且 shell job 无 transcript 流；模型事件的 SSE/审批未测 | §4.3、§4.4、§6.1 |
| 清理 | ✅ 全部探针进程/端口/临时目录已清理，无残留（§8） | — |
| 网页终端真实性 | ✅ 真 PTY（PowerShell）、真 ANSI、真 resize | §4.5 |

## 8. 清理记录

- 每次探针均在 `finally` 里 `taskkill /PID <own> /T /F`，并核验进程消失、端口释放：
  serve 52648 ✅、daemon 53373 ✅、daemon-job 63242 ✅、ACP-HTTP 57739 ✅、ACP 变体 56709/57759 ✅、
  headless 4040 ✅、bg/observe 相关 job PID 均不在 ✅。
- 全部 19 个 `%TEMP%\pan-cbc-probe-*` 隔离根已删除；`Get-CimInstance` 复核无任何引用它们的进程。
- 证据中不保存 `sessionToken`/密码等凭证值（已有一次早期写成值，已就地脱敏，复查 0 命中）。
- 唯一保留物：`D:/tmp/uv-cache-pan-cbc`（uv 依赖缓存，D 盘）。

## 9. 接口影响与可行替代的代价

**接口影响（若采纳 daemon/Web-UI 路线）**

1. Pan 需要一个新的 CBC runtime 所有权模型：Pan 服务启动并持有 `cbc daemon`/`--serve`（回环端口 +
   认证策略），job/PTY/会话都在该进程内；这与当前 `Worker`+`stream-json` 是两套运行时，不能靠
   `base_args` 微调得到。
2. 结构化事件/控制改为 HTTP+SSE（ACP 或 Jobs API），要求 Pan 侧实现：
   连接生命周期（含 `X-CodeBuddy-Request`、Bearer/Cookie）、SSE 断线重连与游标、`Accept` 双值协商、
   版本兼容标记（Beta）。
3. 身份对照：Pan `Session`（session_id + worker）↔ CBC `job shortId/sessionId`、`~/.codebuddy/sessions/<pid>.json`；
   Pan 需要一份映射与“未迁移会话”的兼容路径（否则会与现有 Worker 生命周期打架）。
4. Pan 现有 control 缝（`encode_control_message`）在 CBC 上仍为空；若走 ACP，则控制语义在 ACP
   一侧（permission mode/session config），需要在 adapter 层做翻译，而不是复用 stdin 控制。
5. `takeover_job.py` 的 Job Object 仍可服务“本机可见 console 的 resume 式接管”，但它天然不等于
   无中断切换，不能作为本需求的实现。

**替代方案与代价**

| 方案 | 保留原进程 | 网页原生 TUI | 代价/风险 |
|---|---|---|---|
| A. CBC daemon + Web UI + ACP/HTTP（本报告推荐探索方向） | ✅（daemon 内 job/PTY） | ✅（CBC Web UI，含 xterm） | 新运行时、端口与认证、Beta API、版本耦合、需要 Pan↔CBC 身份映射 |
| B. 保持 stream-json worker，Pan 自建终端视图 | ✅ | ❌（非 provider 原生 TUI，需明确标注） | 需自研渲染/屏幕语义；事件仍来自 Pan 现有解析 |
| C. 需要时另起 `cbc --resume` 原生 TUI（PTY） | ❌（新进程，丢 in-flight turn） | ✅（本机 TUI） | 与“无中断”目标冲突；仅适合人工排查 |
| D. 把 `cbc`（新会话）放进 PTY 当普通终端 | ❌（与 Pan 会话无关） | ✅（PTY 内 TUI） | 属于 Terminal 产品本体（另一 TA 范围），不解决 Adapter 切换 |

## 10. 下一步建议（供 MA 决策，不扩大承诺）

1. 若要继续 A 方向：先做一个**受控凭证实验**（临时、可回收的凭证来源，不在本报告中处理）验证
   模型 job 的 `/jobs/:id/stream`、审批事件、`respawn`/`resume` 语义与 in-flight turn 连续性。
2. 明确首版边界：`--serve` 的 PTY + ACP 已是可用“结构化旁路 + 网页终端”，但它服务的是 CBC 的
   job/PTY 对象，不是 Pan 现在运行的 worker；把两者统一是独立设计工作。
3. 若短期要落地网页终端：PTY API（`/api/v1/pty` + SSE）与 Web UI xterm 的组合是最接近完成的现实能力；
   建议与公共 PTY 契约 TA 的结论合并评估“Pan 自建 PTY host”与“复用 CBC PTY”的成本差异。
4. 不建议依赖：Windows 裸 `--bg`、CLI `attach`（对 daemon/headless 都失败）、把注册表当权威存活状态。

## 11. 复现命令与文件清单

```powershell
# from worktree root
chcp 65001 | Out-Null
$env:UV_CACHE_DIR = "D:/tmp/uv-cache-pan-cbc"
uv run --no-project --python E:/software/miniforge/python.exe `
  --with pywinpty==3.0.5 --with psutil `
  python audit/terminal/cbc/probe_cbc_feasibility.py <mode>
# modes: baseline | headless | bg | bg-lifetime | bg-observe | bg-model |
#        serve | daemon | daemon-job | acp | acp-http
uv run --no-project --python E:/software/miniforge/python.exe `
  --with pywinpty==3.0.5 --with psutil `
  python audit/terminal/cbc/probe_acp_http_variants.py
```

文件清单（本 commit 新增）：

- `docs/design/PAN_TERMINAL_CBC_FEASIBILITY_20261003.md`（本报告）
- `audit/terminal/cbc/probe_cbc_feasibility.py`（11 个 mode 的有界探针驱动）
- `audit/terminal/cbc/probe_acp_http_variants.py`（ACP over HTTP Accept 变体）
- `audit/terminal/cbc/evidence/20261003-003232/baseline.json`
- `audit/terminal/cbc/evidence/20261003-003303/headless.json`（+ headless stdout/stderr 日志）
- `audit/terminal/cbc/evidence/20261003-003326/bg.json`
- `audit/terminal/cbc/evidence/20261003-003430/bg-lifetime.json`（+ launcher 日志）
- `audit/terminal/cbc/evidence/20261003-003451|003530/bg-observe.json`（+ launcher 日志）
- `audit/terminal/cbc/evidence/20261003-003552/daemon.json`
- `audit/terminal/cbc/evidence/20261003-003705/daemon-job.json`
- `audit/terminal/cbc/evidence/20261003-003807/serve.json`、`openapi.json`（+ stdout/stderr）
- `audit/terminal/cbc/evidence/20261003-003836/acp.json`
- `audit/terminal/cbc/evidence/20261003-003852/bg-model.json`
- `audit/terminal/cbc/evidence/20261003-003938|003954|004056/acp-http.json`（+ stdout/stderr）
- `audit/terminal/cbc/evidence/20261003-004402/baseline.json`（提交前用最终脚本的复跑冒烟）
- `audit/terminal/cbc/evidence/acp-http-variants.json`（+ .err）
- `audit/terminal/cbc/evidence/samples/{bg-model.broker.json,bg-model.state.json,daemon-job.state.json,headless-worker-registry.json}`

## 12. 附：与既有 PR 审查的关系

PR #6 审查（`docs/PR6_TERMINAL_REVIEW_20261002.md`）给出的探针方向“原生 TUI 作为独立客户端连接仍运行的
backend；或从一开始就在 PTY 中启动交互式 CLI 并验证结构化旁路”在本报告中得到具体化：

- 对 CBC，第一条（独立 TUI 连接正在运行的 Pan headless backend）**不成立**（§4.2）；
- 第二条成立，但形态不是“PTY 里的 TUI + 屏幕抓取”，而是 **CBC 自带 daemon 的 Web UI + HTTP/ACP 旁路**（§4.4–4.6）；
- 因此“屏幕抓取替代 history/usage/tool/approval”这一风险可以通过 CBC 原生 API 规避——前提是接受 §9 的
  运行时迁移与 Beta 依赖。
