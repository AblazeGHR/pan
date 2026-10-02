# Pan Terminal：CBC 无中断网页原生 TUI 可行性（TA 探索报告）

> 修订 r2（2026-10-03）：MA 审查后按六项返工——结论分层与措辞收紧（Q1/Q2/Q3/§0/§7/§9/§12）、
> 清理安全重做（§3.1、§4.7）、`--remote-control` 只读评估（§3.2）、日志映射（§11.1）、
> 凭证扫描结论（§11.2）。原始证据全部保留（含首轮暴露缺陷的自测快照）。

- 日期：2026-10-03（本地时间；证据文件内时间为 UTC，UTC+8）。
- 工作树：`D:/project/pan-worktrees/terminal-cbc-explore-20261003`，分支 `explore/terminal-cbc-20261003`，
  起点 `35b6fb1abaae9d741b3042470aa32a8c356e431d`。
- 任务：T-TERMINAL-PTY-20261003 / CBC 无中断 TUI 探索（brief 四条并行工作之一）。
- 专属目录：`audit/terminal/cbc/`；本报告：`docs/design/PAN_TERMINAL_CBC_FEASIBILITY_20261003.md`。
- 未修改 `main`/`practical`，未推送，未重启或访问任何既有 Pan 服务（8768）、既有 Session/Worker/CLI thread。

## 0. 结论摘要（按证据层级）

**已验证（接口层，均为本机隔离环境实测）**

1. Pan 当前运行的 CBC headless stream-json 进程没有 attach 端点；在本机实测的 CLI attach 路径下
   （`cbc attach <pid>`）无法接入原生 TUI（§4.2）。这只证明**被测的 CLI attach 路径失败**，
   不能推广为“不存在任何接入方式”。
2. CBC 2.161.1 的 `--serve`/daemon 进程同时提供两类**彼此独立**的接口：
   - **普通 shell PTY**（`POST /api/v1/pty` + Web UI 内嵌 xterm.js 终端）：实测为真实 PowerShell
     会话，输入、SSE 输出（含 ANSI）、resize、销毁全部通过；它是 **OS shell，不是 CBC Agent 会话**，
     不承载模型对话、工具事件或审批。
   - **ACP 会话创建**（`/api/v1/acp/connect` → `initialize` → `session/new`）：实测返回新 sessionId、
     权限模式 config options 与模型列表；**仅验证到会话创建**，未执行 prompt/turn（隔离环境无凭证）。
   两类接口同属一个 daemon/`--serve` 进程，但 **daemon PID ≠ Agent PID ≠ job PID**：进程内 job 有
   各自独立的 PID（实测 daemon PID 5004 与 job `cmd.exe` PID 4032，§4.4）。相同 daemon PID 只说明
   “同一个宿主进程”，不说明“同一个 Agent/会话”。
3. daemon 拥有的 shell job 是真实独立进程，具备 `alive`/`settled`/`state`、stop 与清理语义（§4.4）。

**未验证（Agent / TUI 连续性）**

- 没有证据证明“同一个 CBC Agent/backend/正在执行的 turn 可以在原生 TUI（终端交互式 CLI 或 Web UI
  Agent View）与结构化接口之间连续切换”。本次没有成功 attach 任何 CBC Agent 会话，也没有运行模型
  turn；两者分别成功只说明**两类接口可用**。
- 因此“网页原生 TUI + 结构化旁路”在本报告中只成立为**接口能力**，不成立为**同一 Agent 会话的
  无中断切换**。

**候选与边界**

- daemon/`--serve` 是候选 runtime 所有者之一，**不是唯一可行路线**；替代方案与代价见 §9。
- Windows 上裸 `--bg` shell 作业实测随 launcher 退出即不可用（§4.3）；model 版 `--bg` 在隔离环境
  无凭证下无法区分“鉴权失败”与“随 launcher 回收”（§6.2）。

## 1. 三个原始问题的直接回答

### Q1：已运行的 headless stream-json 同一 backend 能否接入原生 TUI？

**在本机实测的 CLI attach 路径下不能（机制明确；边界见本节末）。**

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
- **边界**：以上证明的是「2.161.1 的 CLI attach 看不到 print-mode 进程的 attach 端点」。
  未测试（§6）：model 会话 `--bg` job 的 broker 命名管道直连、Web UI Agent View 的 attach 行为、
  以及任何非 CLI attach 通道。因此措辞限定为“被测 CLI attach 路径失败”，不是“不存在接入方式”。

### Q2：从一开始就运行 native TUI，能否同时可靠供 Pan 使用结构化事件/控制旁路？

**分层结论：接口层可用已实测；同一 Agent 会话的无中断切换未验证。**

把问题拆成三层，避免把“接口可用”误读为“Agent 连续性”：

| 层 | 内容 | 本次状态 | 证据 |
|---|---|---|---|
| A. 普通 shell PTY | `--serve`/daemon 的 `/api/v1/pty` + Web UI xterm.js 终端；真实 OS shell | ✅ 实测可用（输入/SSE/resize/销毁） | §4.5 |
| B. CBC Agent 原生 TUI | 终端交互式 `cbc` TUI；`--serve` Web UI 的对话/Agent View（含 attach 交互） | ❌ 未验证（无凭证；未 attach 任何 Agent 会话） | 仅随包文档 |
| C. 结构化接口 | HTTP REST/SSE（jobs/events/stream）、ACP over HTTP（initialize/session/new）；daemon 的 jobs API 生命周期 | ✅ 接口实测可用；❌ 模型 prompt/审批/事件内容未验证 | §4.4/§4.6 |

- 层的独立性：PTY 实测创建的是 PowerShell（层 A），ACP `session/new` 返回的是新 ACP 会话（层 C），
  两者与“某个已运行的 CBC Agent 会话/turn”都没有建立绑定关系；daemon 宿主 PID 相同并不等价于
  Agent PID/会话相同（job 有独立 PID，§4.4）。**三类 PID 必须分开：daemon 宿主 PID、job/worker PID、
  Agent 会话 ID。**
- daemon 拥有的 job（服务端派发）是真实独立进程：`POST /api/v1/jobs {bash:true}` 实测产生 `cmd.exe`
  独立 PID 并写出标记文件，`stop` 后 `alive=false, settled=true`（§4.4）。这属于**层 C 的生命周期**，
  仍不等于层 B 的“原生 TUI attach 到该 Agent 会话”。
- Web UI（含 xterm.js 终端与 Agent View）由 `--serve`/daemon 同进程提供（实测 `GET /` 200 + xterm
  资源存在），但**其 Agent 会话交互未被本次验证**；CLI `attach` 对 daemon-API job 实测 `Session not found`。
- 关键约束：CBC 的该 API 标注 **Beta**；`--serve` 默认密码认证（回环首次启动仍打印密码，`--auth none`
  仅限隔离/CI）；`POST /api/v1/acp` 要求 `Accept: application/json, text/event-stream` 同时包含两者，
  否则 406（`evidence/acp-http-variants.json`）。
- Windows 特有约束（随包官方文档 `dist/web-ui/docs/cn%2Fcli%2Fdaemon.md`）：
  “在 Windows 上，只有通过 `daemon start` 启动的 daemon 独立于发起进程存活。其他后台会话、shell 命令、
  hooks 和隧道均随所属 CLI 退出而回收；`--bg` 和 `daemon stop --keep-workers` 不会解除这项生命周期约束。”
  本机实测与之一致（§4.3）。

**因此 Q2 的准确回答**：CBC 确实提供可用的结构化接口与网页终端接口（层 A/C），并且存在一个可常驻的
daemon 宿主；但“从一开始运行 native TUI，同时供 Pan 使用结构化旁路，且两者是同一个 Agent 会话/turn”
**未被本次探索证实**，需要带凭证的受控实验（§10）。

### Q3：同 ID resume 与原 PID/turn 连续性的差别

**CBC 平台自身把二者分开记录；Pan 现有 takeover 属于“同会话 ID、全新进程”一类（对话连续，不是进程/turn 连续）。**

- `--resume <sessionId>` 是**新进程**加载既有 JSONL 对话。Pan `takeover_command()`
  （`adapter.py:400`）返回的正是 `cbc --resume <id>`，由 `takeover_job.py:46` 以
  `CREATE_NEW_CONSOLE|4`（suspended）启动并挂 Job Object。分类上它属于：
  **同一 CLI 会话 ID + 新进程 + 无原 turn**——获得对话上下文，不获得原 PID、不获得正在执行的 turn。
- CBC 自己的 job 模型把“进程连续”和“对话连续”分成不同字段：`alive`（进程活着）、`settled`/`state`
  （是否终结）、`pid` + `procStart`/`procStartFt`（进程身份指纹）、`workerGeneration`、`sessionId`；
  `respawn` 用同一 `sessionId` 起新 worker（`respawnArgs` 带 `--session-id`，样本
  `evidence/samples/bg-model.state.json`）。这与 Pan takeover 是同一类语义：**保留会话、替换进程**。
- 本机实测：daemon 侧 job 的 `stop` 后 `alive=false, settled=true, firstTerminalAt` 写入
  （`evidence/20261003-003705/daemon-job.json`）。
- **未验证**（无凭证跑模型 turn）：in-flight turn 在 attach / respawn / daemon 重启下的存续，
  以及“同 Agent 会话换界面”的连续性。这是“原 PID/任务连续性”的最终判据，留给带凭证实验（§10）。

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

### 3.1 清理安全模型（2026-10-03 返工，取代裸 `taskkill`）

初版探针用 `taskkill /PID <pid> /T /F`，存在 PID 复用/误杀风险，已整体替换（`probe_cbc_feasibility.py`
的 `safe_stop` / `safe_stop_tree` / `safe_stop_leftovers`）：

1. **单一 handle 核验身份**：`OpenProcess(QUERY_LIMITED_INFORMATION|TERMINATE|SYNCHRONIZE)` 打开后，
   用同一 handle 读 `GetProcessId` 与 `GetProcessTimes`（creation FILETIME），与启动时记录的
   identity 比对；不符则**拒绝终止并留证**。FILETIME 容差 ±10 000 单位（1 ms），只用于吸收
   `psutil.create_time` 的 float64 舍入；真实 PID 复用的时间差远大于此。
2. **Handle 即身份**：终止与等待都通过这个已验证 handle（`TerminateProcess` + `WaitForSingleObject`），
   handle 指向进程对象本身，PID 复用无法重定向；结束后 `GetExitCodeProcess` 记录退出码。
3. **子树必须自证**：`collect_own_descendants` 要求 (a) 扫描时刻的 parent 链属于本次自建 root，
   (b) 创建时间不早于 root，(c) cmdline 或 cwd 含本次隔离临时根路径——三项全过才纳入；
   否则跳过并记录原因。
4. **自链保护**：`protected_pids()` 把当前进程与其全部祖先排除；`safe_stop` 对其直接拒绝。
   该保护源于实测事故：当调用命令的 argv 里包含扫描 marker 时，初版扫描会匹配到发起命令的
   自身 shell 并把它终止（见 §4.7 T8/T9）。
5. **daemon/超时兜底**：daemon 这类脱离子进程在 `daemon stop` 后由 `safe_stop_leftovers`
   （扫描 → 身份核验 → 终止）兜底；`run_cli` 的 `subprocess.run(timeout=)` 走 Python 保留的
   Popen handle 终止（非 PID），已用 T6 验证其子进程确实消失且无关进程不受影响。
6. **剩余竞态**（明示）：扫描到 OpenProcess 之间进程关系可能变化；兜底路径的身份基准来自“扫描时刻”，
   窗口为毫秒级，且终止前仍以同一 handle 的 FILETIME 复核。探针无法消除该窗口，只能收敛并留证；
   Pan 侧长期方案应为自有 Job Object（对照 `packages/core/takeover_job.py` 的 suspended-launch 模式）。

### 3.2 `--remote-control`（只读评估，未连接任何远程服务）

任务书要求评估 `cbc --help` 中存在但此前未覆盖的 `--remote-control`。本次仅做**安装包官方文档 +
bundle 字符串只读检查**，不使用认证、不建立连接、不启动 tunnel：

- 官方文档（`dist/web-ui/docs/cn%2Fcli%2Fremote-control.md`）：Remote Control 在**当前会话本地**启动
  Gateway，经 Cloudflare Quick Tunnel 或局域网把 Web UI（**基于 ACP**）暴露给手机/浏览器；
  `/gateway`、`/gateway status|stop|token|tunnel` 为会话内子命令。
- 文档明示限制：“每个会话一个 Gateway”“终端需保持运行：Gateway 作为本地进程运行。关闭终端或停止
  CodeBuddy Code 进程后，远程连接会断开”“远程控制执行的任务自动以 `bypassPermissions` 运行”。
- CLI 侧 `--remote-control [client]` 的描述是“Connect to remote control service（无参数=AgentOS 模式，
  带 client 如 wecom=自动连接指定渠道）”，属**渠道长连接**而非 attach API。
- 与当前 headless 的相关性：它是“**发起它的那个交互会话**的远程窗口”，会话进程结束即断开；
  没有文档/代码证据表明 Pan 的 `-p` stream-json worker 能托管或被动接入 Gateway。
  因此它不改变 Q1/Q2 的结论。
- **未验证（明确）**：`--remote-control` 在无 TTY/`-p` 下能否启动；Gateway 能否桥接到别处已存在的
  会话；tunnel/局域网行为；认证与限流细节；与 Pan 结构化通道并存的可行性。均需独立实验与授权，
  本次不做，也不扩大探索范围。

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

### 4.7 清理安全故障自测（`evidence/20261003-011130/selftest-cleanup.json`，返工新增）

`probe_cbc_feasibility.py selftest-cleanup` 全部为自建进程，不调用 CBC、无模型请求：

| 用例 | 场景 | 期望 | 实测结果 |
|---|---|---|---|
| T1 | 故意给错 create_time（+120 s） | 拒绝终止、进程存活 | ✅ `refused`，reason=identity mismatch；`alive_after_refusal=true` |
| T2 | 正确 create_time | 核验通过并终止 | ✅ `identity_verified=true, terminated=true, gone=true` |
| T3 | 已自然退出的短命进程 | 报告 gone、不尝试 kill | ✅ `gone=true, terminated=false` |
| T4 | 自建 root + 其子进程 | 依据 ancestor+scope 终止两者 | ✅ owned=[child]，root/child 均 verified+gone |
| T5 | 同 marker 但非 root 后代的外部 decoy | 不被 root 扫描收集；仅按自身身份停止 | ✅ `foreign_collected_by_root_scan=false`；随后自身身份核验通过 |
| T6 | `subprocess.run(timeout=)` 超时路径 | 子进程经保留 handle 被终止，无关进程不受影响 | ✅ `timeout_raised=true`；无关 decoy 存活；后续清理 verified |
| T7 | 扫描 + 身份核验兜底（leftover） | 找到 cmd.exe 及其 ping 子进程并全部终止 | ✅ `all_gone=true`，`alive_after=false` |
| T8 | 当前进程自身 + 其父进程（祖先） | 拒绝（自链保护） | ✅ 两者 `refused`，父进程仍存活 |
| T9（复现场景） | marker 出现在调用命令 argv 中，运行 `cleanup-leftovers` | 不再自杀、正常完成 | ✅ 返工前该场景曾终止调用 shell（rc=1、无输出）；返工后 rc=0 并写出证据（`evidence/20261003-011156/cleanup-leftovers.json`） |

附注：首轮自测 `evidence/20261003-010736/selftest-cleanup.json` 暴露出真实缺陷——FILETIME 比较容差过紧
（±1 单位）导致正确的 identity 也被拒绝（T2/T7 失败，并留下 2 个待清理 ping 进程）；
改为 ±10 000 单位（1 ms，仅吸收 float64 舍入）后 `010837` 复跑通过，残留进程由
`safe_stop_leftovers` 兜底清除（见 §8）。

### 4.8 返工后冒烟复跑（验证新清理路径不回归）

- `headless`（`evidence/20261003-011202/headless.json`）：根进程 `identity_verified=true,
  terminated=true, gone=true`，无后代（符合预期）；attach 拒绝结论不变。
- `daemon`（`evidence/20261003-011225/daemon.json`）：`daemon stop` 后兜底扫描 0 候选、
  `all_gone=true`、端口释放、`daemon status=stopped`。

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

1. **层 B（Agent 原生 TUI 与无中断切换）整体未验证**：没有把任何 CBC Agent 会话 attach 到终端 TUI
   或 Web UI Agent View；没有证明同一 Agent/backend/turn 能在原生 TUI 与结构化接口间连续切换。
   层 A（shell PTY）与层 C（ACP 会话创建/接口）的成功**不能替代**该验证（Q2 分层表）。
2. **模型 turn 相关**：隔离 config 无凭证，未跑任何真实模型 turn。因此未验证：`--serve`/`daemon` 下
   模型 job 的 `/jobs/:id/stream` 事件内容、审批（permission）事件在 Web UI/ACP 的实际往返、
   `session/prompt` 的完整流、以及 attach 后的输入控制权交接。
3. `--bg` 模型作业死亡原因二义性：既可能是“随 launcher 回收”，也可能是“无凭证启动失败”。
   需要带凭证的受控实验才能区分；本报告只把 shell job（无需凭证）的死亡作为机制证据。
4. CLI `attach` 未在任何场景成功：headless（无 endpoint）、daemon job（Session not found）、
   bg shell job（进程已死）。**没有证据表明 2.161.1 的 CLI attach 能在 Windows 上长期可用**；
   原生 attach 的可用样本仅剩 broker.json 命名管道机制（未直连验证）。
5. 未验证 pan 端“旧客户端迟到输入/尺寸控制权（lease/generation）”与 CBC ACP/PTY 的对照；
   CBC 侧 `hasClient` 字段存在（PTY 列表），但多客户端争用未测。
6. 未测 `--serve` 跨进程重启后 PTY 会话是否保留（官方说“页面刷新后保持”，不等于服务重启后保持）。
7. 未测 daemon 的 Windows 系统服务注册（`daemon install`，Task Scheduler）；未启用自动更新相关行为。
8. 未测 `jobs/:id/respawn` 与 `jobs/resume` 的真实对话恢复（需凭证）；只读到 `respawnArgs` 与文档语义。
9. 本机为单机单用户，未验证远程（非回环）绑定/隧道场景与 `--host 0.0.0.0` 的强制密码语义。
10. 未验证 CBC HTTP API 的版本兼容承诺（文档标 Beta）；升级 CLI 可能破坏字段/端点。
11. `--remote-control`/`/gateway` 仅做只读文档与 bundle 检查，未连接、未认证、未启动 tunnel；
    其与 headless worker 的关系与全部未验证项见 §3.2。

## 7. 验收场景对照（MA 关注项，按层级）

| 场景 | 涉及层 | 结果 | 证据 |
|---|---|---|---|
| 原 backend PID 连续（TUI 接入不换进程） | B | **未验证**：被测 CLI attach 对 headless（无端点）与 daemon job（Session not found）均失败；未成功 attach 任何 Agent 会话 | §4.2、§4.4 |
| 运行中任务连续（in-flight turn） | B/C | **未验证**：层 C 的 job 有独立 PID 与 `alive/settled` 生命周期、可 stop；turn 在 attach/respawn/daemon 重启下的存续未测 | §4.4、§6 |
| 操作权转交（Pan ↔ 原生 TUI） | B | **未验证/未实现**：无“同一 Agent 会话在 Pan 与原生 TUI 间交接”的证据；PTY/ACP 接口本身可用，但不构成会话交接 | §4.5、§4.6、§6 |
| 审批与输出事件保留 | C | 部分：daemon job 的 shell 输出**未进日志文件**（0 字节）且 shell job 无 transcript 流；模型事件的 SSE/审批未测 | §4.3、§4.4、§6 |
| 清理 | — | ✅ 探针进程/端口/临时目录均已清理；返工后清理全部走身份核验路径，T1–T9 覆盖误杀/自链/超时/兜底 | §4.7、§8 |
| 网页终端真实性（普通 shell PTY） | A | ✅ 真 PowerShell PTY、真 ANSI、真 resize；**它是 OS shell，不是 Agent 会话** | §4.5 |

## 8. 清理记录

- 返工后（2026-10-03 第二轮）所有清理走 `safe_stop` / `safe_stop_tree` / `safe_stop_leftovers`
  （同一 handle 核验 FILETIME + 自链保护），**不再使用 `taskkill`**：
  - `selftest-cleanup`（T1–T9）全部通过并留证：`evidence/20261003-011130/selftest-cleanup.json`；
  - 复现自杀场景修复：`evidence/20261003-011156/cleanup-leftovers.json`（rc=0 正常完成）；
  - 冒烟：`evidence/20261003-011202/headless.json`（root `identity_verified=true, gone=true`）、
    `evidence/20261003-011225/daemon.json`（stop 后 0 候选、`all_gone=true`、端口释放）。
- 第一轮（提交 `f081484b`/`8e2be41c`）运行（当时使用 `taskkill`）：核验进程消失、端口释放：
  serve 52648 ✅、daemon 53373 ✅、daemon-job 63242 ✅、ACP-HTTP 57739 ✅、ACP 变体 56709/57759 ✅、
  headless 4040 ✅、bg/observe 相关 job PID 均不在 ✅；无误杀事件的旁证（全部操作对象均为自建进程）。
- 返工期间由自测**发现并修复两个真实清理缺陷**：FILETIME 容差过紧（正确的 identity 被拒）与
  自链未保护（marker 出现在调用命令 argv 时曾终止调用自身 shell，仅影响本探针命令，未触及其它进程）。
- 全部 `%TEMP%\pan-cbc-probe-*` 隔离根已删除（含返工新增的 `selftest-*`）；`Get-CimInstance` 复核
  无任何引用它们的进程。
- 证据中不保存 `sessionToken`/密码等凭证值（一次早期写入已在提交前脱敏，两轮提交与最终 evidence
  的扫描结果见 §11.2）。
- 唯一保留物：`D:/tmp/uv-cache-pan-cbc`（uv 依赖缓存，D 盘）。

## 9. 接口影响与可行替代的代价（A 为候选路线，不是唯一路线）

**接口影响（若采纳 A：daemon/Web-UI 路线）**

1. Pan 需要一个新的 CBC runtime 所有权模型：Pan 服务启动并持有 `cbc daemon`/`--serve`（回环端口 +
   认证策略），job/PTY/会话都在该进程内；这与当前 `Worker`+`stream-json` 是两套运行时，不能靠
   `base_args` 微调得到。**是否采纳是架构决策；B/C/D 路线仍然存在（见下表）。**
2. 结构化事件/控制改为 HTTP+SSE（ACP 或 Jobs API），要求 Pan 侧实现：
   连接生命周期（含 `X-CodeBuddy-Request`、Bearer/Cookie）、SSE 断线重连与游标、`Accept` 双值协商、
   版本兼容标记（Beta）。
3. 身份对照：Pan `Session`（session_id + worker）↔ CBC `job shortId/sessionId`、`~/.codebuddy/sessions/<pid>.json`；
   Pan 需要一份映射与“未迁移会话”的兼容路径（否则会与现有 Worker 生命周期打架）。
4. Pan 现有 control 缝（`encode_control_message`）在 CBC 上仍为空；若走 ACP，则控制语义在 ACP
   一侧（permission mode/session config），需要在 adapter 层做翻译，而不是复用 stdin 控制。
5. `takeover_job.py` 的 Job Object 仍可服务“本机可见 console 的 resume 式接管”，但它天然不等于
   无中断切换，不能作为本需求的实现。

**替代方案与代价（并列候选，无排序承诺）**

| 方案 | 保留原进程 | 网页 TUI | 性质 | 代价/风险 |
|---|---|---|---|---|
| A. CBC daemon + Web UI + ACP/HTTP | ✅（daemon 内 job/PTY 对象） | ✅ 提供方 Web UI（含 xterm）；Agent 层未验证 | 接口层已实测，Agent 层未验证 | 新运行时、端口与认证、Beta API、版本耦合、Pan↔CBC 身份映射 |
| B. 保持 stream-json worker，Pan 自建终端视图 | ✅ | ❌（非 provider 原生 TUI，需明确标注） | 不改 runtime | 自研渲染/屏幕语义；事件仍来自 Pan 现有解析 |
| C. 需要时另起 `cbc --resume` 原生 TUI（PTY） | ❌（新进程，丢 in-flight turn） | ✅（本机 TUI） | 对话连续、进程不连续 | 与“无中断”目标冲突；适合人工排查 |
| D. 把 `cbc`（新会话）放进 PTY 当普通终端 | ❌（与 Pan 会话无关） | ✅（PTY 内 TUI） | 独立终端 | 属 Terminal 产品本体（另一 TA 范围），不解决 Adapter 切换 |

## 10. 下一步建议（供 MA 决策，不扩大承诺）

1. 若继续评估 A：先做**受控凭证实验**（临时、可回收的凭证来源，不在本报告中处理），并**按层验证**：
   先层 C（模型 job 的 `/jobs/:id/stream`、审批事件、`respawn`/`resume` 语义），
   再层 B（把某个 Agent 会话 attach 到原生 TUI/Web UI，验证原 PID、in-flight turn 与控制权交接）。
   不得以“daemon PID 相同”或“resume 同 sessionId”替代这两个判据。
2. 明确首版边界：`--serve` 的 PTY + ACP 是层 A/C 的可用接口，但服务的是 CBC 的 job/PTY 对象，
   不是 Pan 现在运行的 worker；把两者统一（或不统一）是独立设计决策。
3. 若短期要落地网页终端：层 A（PTY API + SSE + Web UI xterm）是最接近完成的现实能力；
   建议与公共 PTY 契约 TA 的结论合并评估“Pan 自建 PTY host”与“复用 CBC PTY”的成本差异。
4. 不建议依赖：Windows 裸 `--bg`、CLI `attach`（对 headless/daemon job 实测均失败）、
   把注册表或 daemon PID 当作 Agent 存活/连续性的权威。

## 11. 复现命令与文件清单

```powershell
# from worktree root
chcp 65001 | Out-Null
$env:UV_CACHE_DIR = "D:/tmp/uv-cache-pan-cbc"
uv run --no-project --python E:/software/miniforge/python.exe `
  --with pywinpty==3.0.5 --with psutil `
  python audit/terminal/cbc/probe_cbc_feasibility.py <mode>
# modes: baseline | headless | bg | bg-lifetime | bg-observe | bg-model |
#        serve | daemon | daemon-job | acp | acp-http |
#        selftest-cleanup | cleanup-leftovers "<path-substring>"
uv run --no-project --python E:/software/miniforge/python.exe `
  --with pywinpty==3.0.5 --with psutil `
  python audit/terminal/cbc/probe_acp_http_variants.py
```

文件清单（两轮提交合并；`8e2be41c` 之后的返工内容见本节末尾“返工新增”）：

- `docs/design/PAN_TERMINAL_CBC_FEASIBILITY_20261003.md`（本报告）
- `audit/terminal/cbc/probe_cbc_feasibility.py`（13 个 mode 的有界探针驱动，含身份核验清理与自测）
- `audit/terminal/cbc/probe_acp_http_variants.py`（ACP over HTTP Accept 变体）
- 第一轮证据（`f081484b`/`8e2be41c`）：
  `evidence/20261003-003232/baseline.json`、
  `evidence/20261003-003303/headless.json`（+ `headless.stdout.txt` / `headless.stderr.txt`）、
  `evidence/20261003-003326/bg.json`、
  `evidence/20261003-003430/bg-lifetime.json`（+ `bg-lifetime.launcher.txt`）、
  `evidence/20261003-003451|003530/bg-observe.json`（+ `bg-observe.launcher.txt`）、
  `evidence/20261003-003552/daemon.json`、
  `evidence/20261003-003705/daemon-job.json`、
  `evidence/20261003-003807/serve.json`、`openapi.json`（+ `serve.stdout.txt` / `serve.stderr.txt`）、
  `evidence/20261003-003836/acp.json`、
  `evidence/20261003-003852/bg-model.json`、
  `evidence/20261003-003938|003954|004056/acp-http.json`（+ 同名 `*.stdout.txt` / `*.stderr.txt`）、
  `evidence/20261003-004402/baseline.json`（提交前冒烟）、
  `evidence/acp-http-variants.json`（+ .err）、
  `evidence/samples/{bg-model.broker.json,bg-model.state.json,daemon-job.state.json,headless-worker-registry.json}`
- 返工新增（2026-10-03 第二轮）：`evidence/20261003-010736/selftest-cleanup.json`（首轮自测，暴露容差缺陷）、
  `evidence/20261003-011130/selftest-cleanup.json`（T1–T9 全过）、
  `evidence/20261003-011156/cleanup-leftovers.json`（自杀场景修复复现）、
  `evidence/20261003-011202/headless.json`、`evidence/20261003-011225/daemon.json`（新清理路径冒烟）

### 11.1 证据日志映射（`.log` → `.txt`，读者定位用）

第一轮采集时重定向输出文件名为 `*.stdout.log` / `*.stderr.log` / `*.launcher.log`；`.gitignore` 第 77 行
忽略 `*.log`，因此在提交 `8e2be41c` 中**同名内容**改为 `.txt` 后缀（内容不变，仅去除尾部空行），
采集元数据（时间、命令、PID）仍保留在各 run 的 JSON 中。映射关系：

| 采集时文件名（JSON 内 `*_path` 字段仍引用此名） | 已提交文件 |
|---|---|
| `headless.stdout.log` / `headless.stderr.log` | `20261003-003303/headless.stdout.txt` / `.stderr.txt` |
| `bg-lifetime.launcher.log` | `20261003-003430/bg-lifetime.launcher.txt` |
| `bg-observe.launcher.log` | `20261003-003451/bg-observe.launcher.txt`、`003530/bg-observe.launcher.txt` |
| `serve.stdout.log` / `serve.stderr.log` | `20261003-003807/serve.stdout.txt` / `serve.stderr.txt` |
| `acp-http.stdout.log` / `acp-http.stderr.log` | `20261003-003938|003954|004056/acp-http.stdout.txt` / `.stderr.txt` |

说明：各 JSON 证据中的 `stdout_path`/`stderr_path` 字段按**采集时刻**原样记录 `.log` 名称，属历史元数据，
未回改；返工后的探针直接写 `.stdout.txt` / `.stderr.txt`。

### 11.2 凭证扫描结果（只列文件/键/是否脱敏，不打印值）

扫描范围：两次已提交树（`f081484b`、`8e2be41c`=HEAD 之前）+ 工作区最终 `evidence/`。

| 项 | 结果 |
|---|---|
| `sessionToken` 值 | 历史与最终证据中均无裸值；仅出现在 OpenAPI schema（字段名）、探针代码与显式脱敏注记 |
| 早期运行 `20261003-003938` 的 token | **未进入 Git 历史**：`f081484b` 中该文件的 `acp_connect` 即为 `{"status":200,"note":"initial run redacted: sessionToken value removed"}`（写入发生在首次 `git add` 之前） |
| `password` / `Bearer ` / `gateway_session` / `CODEBUDDY_API_KEY` / `CODEBUDDY_AUTH_TOKEN` | 无值命中（`--serve` 探针均用 `--auth none`，启动横幅不含密码） |
| 高熵字符串扫描（≥32 字符） | 命中均为 CBC 会话/作业 UUID、local_storage 条目名、日志文件名哈希、`--help` 旗标串，非凭证 |
| 结论 | 无需改写历史；未发现需要上报 MA 的泄露 |

## 12. 附：与既有 PR 审查的关系

PR #6 审查（`docs/PR6_TERMINAL_REVIEW_20261002.md`）给出的探针方向“原生 TUI 作为独立客户端连接仍运行的
backend；或从一开始就在 PTY 中启动交互式 CLI 并验证结构化旁路”在本报告中得到具体化与限定：

- 对 CBC，第一条（独立 TUI 连接正在运行的 Pan headless backend）在**被测 CLI attach 路径**下不成立（§4.2）；
  其余 attach 通道（broker 命名管道直连、Web UI Agent View）未测（§6）。
- 第二条**部分成立**：形态不是“PTY 里的 TUI + 屏幕抓取”，而是 CBC daemon/`--serve` 的 Web UI（层 A）
  与 HTTP/ACP 结构化接口（层 C）；但“原生 TUI 与结构化接口共存于同一 Agent 会话/turn”**未验证**（Q2 分层）。
- “屏幕抓取替代 history/usage/tool/approval”的风险可经由 CBC 原生 API 规避的前提是：
  接受 §9 的运行时决策与 Beta 依赖，并补做 §10 的带凭证分层验证。
