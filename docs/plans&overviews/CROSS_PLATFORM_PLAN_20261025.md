# Pan 跨平台支持规划（macOS / Linux）

2026-11-06 P6 Group adoption 复用 `job.runtimeOwner`/`processOwner` 的能力与实测边界见
[P6 平台与兼容矩阵](../reports/ACTION_UNIFICATION_P6_20261106.md#platform-and-delivery-limits)。
本地 Windows 证据不代表 Linux/macOS 或三平台 CI 验收；未新增 capability。

- 日期：2026-10-25
- 基线：`58c0739a`（跨平台审计基线 `3bf174b1`，结论见
  `docs/audit/CROSS_PLATFORM_GAPS_20261025.md`，审计工作树 `cross-platform-audit-20261025`）
- 状态：规划 + Phase A/B/C 正在并行实施；main 仍以 Windows 为已验收平台。

## 结论与目标

当前基线在 macOS / Linux **启动即失败**（P0：`guard.py` 顶层 `from ctypes import wintypes`），
不是功能降级。目标不是“一次性移植”，而是建立**平台缝**，让功能开发与跨平台工作并行且互不阻塞：

1. 三平台都能启动、跑 Session/Worker/大部分 API；
2. 平台相关功能有显式 capability，不支持的平台干净禁用；
3. CI 从第一天起三平台常绿（快层）；
4. 现有 Windows 体验与发布节奏不变。

## 原则

- 单一 main；不做长期平台分支。
- 平台差异只进平台层；共享代码零平台硬编码（见根 `AGENTS.md`）。
- 先“包一层”再“换实现”：Windows 现有实现先适配接口，POSIX 实现后替换，不改业务语义。
- 能力探测而不是 `sys.platform` 散布；不支持要显式，不静默。
- 小步合并：每个 PR 都必须保持 Windows 绿、三平台 import 绿。

## 阶段与出口条件

### Phase A — 解锁启动与门禁（进行中）

范围：
- 平台层骨架与 capability registry；
- `terminal/guard.py`、`server.py` 等导入链改为懒加载/条件导入；
- 后端路径策略（分隔符、临时目录、可执行位）；前端目录选择器按平台拼接；
- 非 Windows 的 Terminal/Rewind/生命周期 API 返回 unsupported；
- CI 增加 ubuntu/macos 的 import + 收集 + 单元子集。

出口：
- 三平台 `import packages.web.server` 成功；
- ubuntu/macos 能跑单元子集；Windows 全量不退化；
- 能力矩阵初版；无新增平台硬编码。

### Phase B — 进程所有权（进行中）

范围：`ProcessOwner` 契约（spawn、信号、等待、进程树终止、持久身份、崩溃后回收）；
POSIX 实现（`setsid`/进程组、SIGTERM→SIGKILL、`ps`/`libproc` 身份）；Windows 实现包装现有 Job Object。
集成到 Worker / Background runner；Ctrl+C 不再直打所有 CLI。

出口：契约测试双实现通过；POSIX 下前台中断只影响目标进程；硬崩溃后可按持久身份回收/拒绝重投。

### Phase C — 前端与能力 UX（进行中）

范围：目录分隔符、路径展示、Terminal/Rewind 入口按 capability 隐藏/禁用、平台提示文案、窄屏布局。

出口：POSIX 上不再出现“点了才报错”的入口；1440/390/320 无溢出；Windows 视觉不变。

### Phase D — 功能对齐（待 Phase A/B 完成）

范围：POSIX PTY Terminal backend、detach/reattach、Rewind、launchd/systemd、SecretStore、打包与升级；
不能对齐的能力明确标注“仅 Windows”，并在文档与 UI 一致。

出口：能力矩阵中每项都有“可用/受限/仅 Windows”的明确状态与证据。

## 并行工作流与路径所有权

| 流 | 范围 | 主要路径 | 依赖 |
|---|---|---|---|
| A 基础 | 平台层、导入链、CI、路径策略（后端） | `packages/core/platform/`、`packages/core/terminal/guard.py`、`packages/web/server.py`（仅导入段）、`.github/workflows/` | 无 |
| B 前端 | 目录选择、能力 UX、文案 | `packages/web/src/` | 与 A 的 capability 名称约定 |
| C 进程 | POSIX ProcessOwner、信号/身份 | `packages/core/platform/process_posix.py`、`packages/jobs/`、`packages/core/worker.py`（spawn/kill 段） | A 的平台层骨架 |
| D 终端 | POSIX PTY backend、detach | `packages/core/terminal/posix_backend.py`、`terminal_api.py` | A、C |

规则：同一文件同一时间只允许一个流修改；跨流改动由 MA 在整合树合并并跑组合回归。
并行分支不直接推进 main；MA 组合验证后再推进。

## 验收与证据

- 每流：单元/契约测试 + 目标平台证据（至少 ubuntu CI；macOS 由 CI 或实机）；
- 组合：三平台 import、ubuntu 真实服务 + 浏览器、Windows 回归；
- 明确区分“静态可判定”“CI 证明”“实机证明”“部署验证”。
- 真实数据迁移、服务重启、远端 CI、发布不在本规划默认授权内，需单独命令。

## 非目标

- 不做长期平台分支；不为跨平台重写业务核心；
- 不在本阶段承诺 macOS 全部终端/回滚能力；
- 不以“能 import”冒充“功能可用”。

## 能力矩阵（Phase A 初版，2026-10-25）

R6-I（2026-10-27）将 Job 根路径解析归入 `packages/core/platform/data_paths.py`。
这是三平台共用的纯路径策略，不新增 capability 或平台私有行为；Scheduler、handoff、
FinalService 与 Job facade 共用环境变量优先级，解析不创建目录或初始化旧 store。
本轮 Windows 隔离 import/startup 与路径/消费者回归有证据；macOS/Linux 仍需对应 CI，
不能把纯代码可导入视为三平台实机验收。

唯一事实来源是 `packages/core/platform/__init__.py` 的 `CAPABILITIES`；本表是它的
可读投影。`GET /api/platform` 返回同样的键与布尔值（只含平台名与能力，不含路径/账号）。

| 能力 | Windows | macOS | Linux | 说明 |
|---|---|---|---|---|
| `job.runtimeOwner` | 实现；本轮 fixture 实测 | 实现；本轮未实测 | 实现；本轮未实测 | 本地 Windows LockFileEx / POSIX flock + SQLite FULL/BEGIN IMMEDIATE epoch CAS；只将原生非阻塞竞争分类为 busy，普通 I/O/权限/磁盘错误原样抛出；旧异步 pass/runner 持久 reservation；未知平台显式 unsupported。不提供网络文件系统/跨主机租约。具名 owner 冲突可进入无效果维护 web，仍拒绝认领、业务 readiness 和执行；正常 owner 的非执行数据处置复用同一 fence/receipt，见 `docs/design/job-unification/JOB_OWNERSHIP_MAINTENANCE.md`；原 fence 见 `docs/design/job-unification/R4_RUNTIME_OWNER.md` |
| `terminal.native` | 可用 | 不可用 | 不可用 | Windows ConPTY + Job Object；POSIX PTY 属 Phase D |
| `terminal.detach` | 可用 | 不可用 | 不可用 | durable detach 依赖 Job Object 与命名管道所有权 |
| `rewind` | 可用 | 不可用 | 不可用 | 复用 ConPTY 交互式菜单自动化（仅 cbc） |
| `service.lifecycle` | 可用 | 不可用 | 不可用 | UI 旧入口保留；WP05b canonical 单步 service.restart/exit 经受保护 supervisor，取消窗口在 stopping 前关闭，ready/offline 需证据；POSIX 显式 capability_unsupported |
| `processOwner` | 可用 | 可用 | 可用 | WP05a typed executor 与 WP05b independent canonical Attempt 复用 `ProcessOwner`；POSIX 使用 setsid/进程组与持久身份核验；独立启动经短命 bootstrap 的 retained-handle exit receipt |
| `secretStore` | 可用 | 不可用 | 不可用 | 终端秘密存储使用 DPAPI |
| `fileManager.reveal` | 运行时探测 | 运行时探测 | 运行时探测 | 需要 Explorer / `open` / xdg-open 与桌面会话；探针为 false 时 API `501`、按钮禁用 |

The cc-switch bridge reuses the platform `RuntimeLock` for its existing
`.bridge.lock` fence: Windows LockFileEx and POSIX flock lock the same file
without writing a padding byte before acquisition. Only confirmed contention
enters the bounded busy retry; permission and storage errors propagate before
provider settings or CLI writes. Empty-file contention and concurrent settings
CAS have Windows and WSL Linux fixture evidence; this bridge change still needs
the exact candidate's macOS CI. No new capability or runtime owner is created.

Canonical global Run-history/log retention reuses the existing FinalService
pulse, owner fence, execution identities, `ProcessOwner` exit proofs and pinned
controlled log directory on all three platforms. No new platform capability is
declared. `test_job_global_retention.py` and `test_job_retention_process_logs.py`
are required fast-gate nodes; target-platform evidence remains mandatory. See
[the API and storage contract](../design/job-unification/CANONICAL_GLOBAL_RETENTION_20261110.md).

CP-X3 revision2：Worker 的普通 pipe 启动在 POSIX 通过平台层 `start_new_session` 隔离宿主终端信号，
Windows 继续使用既有窗口策略；停止仍由 Worker 自有树清理负责。Native Terminal TUI 的创建、配置、
启动、重启及 React 入口统一要求 `terminal.native=true`，明确 `false` 返回
`unsupported_platform`，探测异常保持为错误；未知能力探测不自动隐藏全部 UI，普通 stream Worker 不受影响。
Windows 后台 Job 的隐藏窗口策略属于命令启动行为，具体失败和可见回退边界见
`docs/design/JOB_WINDOW_POLICY_20261010.md`；POSIX 窗口行为不变。回退不声明隐藏能力成功。


不支持平台的 API 契约（稳定、可判定，不 500、不半启动）：

- 能力查询：`GET /api/platform` → `{"platform": "...", "capabilities": {...}}`；
- Terminal REST/WS：`501` + `{"ok": false, "error": {"code": "unsupported_platform"}}`
  （绑定形态/配置禁用仍是 `503 terminal-disabled`）；
- Rewind：`POST /api/sessions/{id}/history/{message_id}/rewind` → `501 unsupported_platform`；
- main lifecycle：`POST /api/main/exit`、`POST /api/main/restart` → `501 unsupported_platform`；
  `GET .../status` 保持 `200` 但 `available=false` 且带 `code: unsupported_platform`。

“可用”只表示**已声明**且 Windows 有实现；macOS/Linux 的**运行**证据来自
`posix-fast` CI（import + collect + 平台单元子集），在 CI 实际跑绿前不得当作已验证。

WP05b 本地证据为 Windows 独立 worker、同身份离线查询及临时 HTTP；lifecycle stop/start
使用 mock，未启停真实 Pan 服务。POSIX 的 independent worker 运行、三平台 CI、真实
supervisor 停机/重启验收仍待验证。接线与收据契约见
[WP05b execution](../design/job-unification/WP05B_EXECUTION_20261026.md)。

### Scheduler current host zone (revision 2)

`time.hostZone`: Windows/Linux/macOS use the platform `timezones.host_zone_name`
boundary and tzlocal zoneinfo discovery, refreshed before unreserved calendar drives.
Never substitute the current fixed offset for future DST rules. Failure is
`provider_unavailable`; explicit overrides remain independent. Host changes invalidate
only future, unreserved calendar cursor points; reserved envelopes settle unchanged.
Windows discovery was exercised locally; Linux/macOS require CI/runtime verification.

### Editor 的 OS 打开 / 定位

Editor 顶栏的“用系统默认方式打开”（`POST /api/editor/open-external`）保留原行为，
不新增能力门；新增的“在系统资源管理器中定位”（`POST /api/editor/reveal-external`）
登记 `fileManager.reveal`。两者共用同一套 Session workdir / effective Workspace
containment 与 `..`、符号链接解析，请求只携带路径、不携带命令。

`fileManager.reveal` 与静态 Phase A 能力不同：除声明 `supported_on` 外，每次
`GET /api/platform` 都由平台层探针计算可用性——Windows 需要 Explorer 路径，
macOS 需要 `open`，Linux 需要 `xdg-open` 且存在 `DISPLAY`/`WAYLAND_DISPLAY`；
未知平台直接 false。探针为 false 时请求返回 `501`，前端 `platformStore` 读取
同一布尔值并禁用按钮、在 title 显示明确原因；探针失败（旧服务/网络错误）仍按
后端 501 显示 toast，不静默隐藏。启动或退出失败返回 `500`。

- Windows：默认打开走 `os.startfile`；定位走 Explorer 固定 argv
  `explorer.exe /select, <file>`（Explorer 成功时也可能返回非零，按已交付处理）。
- macOS：默认打开 `open <file>`；定位 `open -R <file>`。
- Linux：默认打开 `xdg-open <file>`；定位打开父目录（`xdg-open <dir>`），没有
  可移植的“选中该文件”原语。无桌面会话或 opener 按不支持处理。

平台差异与命令构造集中在 `packages/core/platform/file_manager.py`，`server.py`
只负责解析路径、映射状态码并调用。本机 Windows 候选证据见
`evidence/editor-system-open/`；macOS/Linux 仅有 argv 构建与探针单元测试，尚无
目标平台运行证据，不得当成已验收。
