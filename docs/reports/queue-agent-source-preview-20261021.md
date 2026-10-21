# 队列消息预览来源 Session —— 交付与偏差记录（2026-10-21）

定位：本文件记录 `feature/queue-agent-source-20261021` 的交付边界、一次**非预期真实
provider 调用**偏差，以及针对该偏差加入的隔离守卫。偏差编号 **D-1**，用于后续追溯，
不要在未读本文件的情况下复用本次浏览器的 e2e 运行方式。

## 1. 交付位置

| 项 | 值 |
|---|---|
| 工作树 | `D:/project/pan-worktrees/queue-agent-source-20261021` |
| 分支 | `feature/queue-agent-source-20261021` |
| 初始基线 | `d2790e6d` |
| 整合 main | `a4e1afdd`（队列行 Re/P 图标 + `docs/skills/pan/references/ma.md` 各保留，无冲突） |

实现范围：现有全文消息预览弹窗内展示结构化 agent 来源（Session name + session ID，
各自可点击复制并有可见反馈）、`Open source chat` 复用既有 Session 深链导航；来源只取
服务端 `meta.sourceSessionId`，非 agent 来源不渲染，缺失/不可解析来源给英文反馈且已知 ID
仍可复制，迟到剪贴板结果不落到另一个预览。队列行图标、锁、Steer、发送/报告语义均未改动。

## 2. 偏差 D-1：一次非预期真实 provider 调用

**事实**：首次真实浏览器验证时，我启动隔离 fixture 服务（`packages/web/e2e/server.py`）
时**没有设置** `PAN_E2E_FAKE_CLI`，随后用真实 HTTP 路由
`POST /api/sessions/broadcast`（带 `sourceSessionId`）向 fixture 会话 "Charlie Session"
入队一条短消息。产品的全局 watchdog 发现该 Session `queue_pending` 非空且无 live worker，
按既有行为自动 spawn 了 cbc worker，把这条消息**实际送进了真实模型**并产生了回复。

**根因**（三件事叠加，缺一不会发生）：

1. `e2e/server.py` 只有在 `PAN_E2E_FAKE_CLI` 存在时才把 `CbcAdapter._resolve_cbc_argv`
   指向 `tests/support/frontend_stream_cli.py`；缺省即真实 cbc CLI。
2. 全局 watchdog 自动补活（产品行为，fixture 未关闭），所以"只入队、不 spawn"不成立。
3. 我的启动命令遗漏了该环境变量，且当次脚本没有任何"实例/CLI 可信"前置校验。

**影响**：一条很短的自动化测试文本经真实 cbc 调用，产生一次真实 provider 交互与回复。
未改动仓库数据、未操作既有服务、未使用 8767/8768；该实例运行在
`%TEMP%/pan-queue-source-e2e-20261021` 的一次性目录，事后已删除。此后所有运行均改用具
确定性 stub 的 fake CLI，并先暂停目标队列再入队，**未再发生真实模型调用**。

**已采取的处理**：见 §3（脚本侧失败即退出的守卫）+ §4（负控证据）。本条偏差不隐藏：
任务约束明确"不调用收费模型"，本次属违反，记录在案。

## 3. 守卫：任何 mutation 之前先证明"这是隔离 fixture + 假 CLI"

`packages/web/e2e/queue-source-session.chromium.mjs` 在第一次写操作（rename / pause /
enqueue / delete）之前完成以下校验，任一不满足即 `[guard] ...` + `exit 1`：

| 守卫 | 内容 | 可核实来源 |
|---|---|---|
| G1 端口策略 | 拒绝 `8767`/`8768`；端口必须是 1..65535 的整数；`runtime` 必填且**不得位于本 checkout 内**；`artifacts` 必须落在 `runtime` 内 | 调用方 identity 文件（纯拒绝性检查，不发请求） |
| G2 实例身份 | 对**持有该端口的进程**调用新增的只读路由 `GET /__e2e/identity`，要求其自报的 `port` 与请求端口一致、`checkout` 等于本 checkout、`runtime` 等于请求的 runtime、`sessionDir` 在 runtime 内 | 运行中的服务进程自身（不采信调用方文字标志；读不到即拒绝） |
| G3 假 CLI | 该进程必须自报非空 `fakeCli`；其解析路径必须等于本 checkout 的 `tests/support/frontend_stream_cli.py`，且两者 **sha256 完全一致** | 服务进程自述 + 本地字节校验（证明被自动 spawn 的 worker 只会跑 stub） |
| G4 fixture 与 adapter | 种子会话 `Alpha Session` / `Charlie Session` 必须存在（证明是种子 fixture 实例），且两者 `adapter === 'cbc'`（stub 只覆盖 cbc） | `GET /api/sessions?summary=1` |

另保留运行期约束：入队前 `PATCH .../queue/paused {paused:true}`，使条目保持 `queued`
以便预览，同时避免 watchdog 把它交接出去。

`packages/web/e2e/server.py` 相应地把 `fakeCli`（解析后的绝对路径）写入
`server-identity.json`，并以 `GET /__e2e/identity` 只读暴露同一份 identity 字典
（不含任何 mutation、不启 Worker、不接触 provider）。

## 4. 负控证据（本轮实测）

| 场景 | 命令要点 | 结果 |
|---|---|---|
| 应用端口 | `identity {"port":8768,...}` | `[guard] port 8768 is the application port; use an isolated e2e port`，`exit 1`，未发出任何请求 |
| runtime 不符 | `{"port":8790,"runtime":".../other-runtime"}` | `[guard] server runtime ... is not the requested runtime ...`，`exit 1` |
| 无假 CLI | 另起 fixture 实例（`PAN_PORT=8791`，不设 `PAN_E2E_FAKE_CLI`）后运行脚本 | probe 返回 `"fakeCli":null` → `[guard] server has no fake CLI; a real provider could be spawned`，`exit 1`；事后核对该实例："Alpha Session" 未被改名、Charlie 队列 `items:[]`、`queuePaused:false`，**证明守卫在任何 mutation 之前退出** |
| 正常路径 | `PAN_PORT=8790` + `PAN_E2E_FAKE_CLI=tests/support/frontend_stream_cli.py` | `"status": "passed"`，`guards` 记录 owner pid / checkout / fakeCli / sha256 |

## 5. 遗留风险（未在本次扩大范围处理）

- `e2e/server.py` 本身仍允许不设 `PAN_E2E_FAKE_CLI` 启动；本次加固只覆盖本脚本入口。
  若要根治，应让 harness 在缺少 fake CLI 时拒绝启动（会改变其它 e2e 的既有调用方式，
  超出本任务范围，留给 MA 决策）。
- 其它 e2e 脚本若同样入队且未设 fake CLI，仍有 D-1 同类风险。
- 本脚本需手动提供 identity 文件（`{"port":<隔离端口>,"runtime":"<一次性目录>"}`），
  尚未注册进 `e2e/run.ps1`。

## 6. 复现

```bash
# 1) 一次性 runtime + 假 CLI + 非 8767/8768 端口
PAN_E2E_RUNTIME="$TEMP/pan-queue-source-e2e-verify" PAN_PORT=8790 \
PAN_E2E_FAKE_CLI="<checkout>/tests/support/frontend_stream_cli.py" \
E:/software/miniforge/python.exe packages/web/e2e/server.py

# 2) identity 文件
printf '{"port": 8790, "runtime": "%s"}' "$TEMP/pan-queue-source-e2e-verify" \
  > "$TEMP/pan-queue-source-e2e-verify/identity.json"

# 3) 带守卫的浏览器验证
cd packages/web && node e2e/queue-source-session.chromium.mjs \
  "$TEMP/pan-queue-source-e2e-verify/identity.json"
```

本轮证据：`%TEMP%/pan-queue-source-e2e-verify/artifacts/`
（`evidence.json` 含 guards 与 checks，另有 `preview-wide.png`、`preview-320.png`、
`after-jump.png`、`preview-deleted-source.png`）。

## MA 独立验收：通过（2026-10-21）

复核补充候选6ce24116：卸载时递增copySeq，迟到then/catch均失效；浏览器守卫位于所有写操作前，拒绝保护端口并核实fixture及fake CLI。独立运行四文件Vitest：56 passed、0 skipped、exit 0；包含两条卸载迟到失败回归及队列图标共存。git diff --check通过。真实浏览器复制/跳转/320px证据沿用本候选TA隔离实测，不冒称MA重跑。D-1真实provider调用偏差保留，不改写为全程无模型调用。
按授权快进main/practical；不push/release，不操作既有服务。
