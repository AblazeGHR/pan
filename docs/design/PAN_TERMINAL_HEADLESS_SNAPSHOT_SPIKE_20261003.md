# Pan Terminal：常驻 Node headless-xterm 权威快照候选 Spike（P1 门禁）

- 日期：2026-10-03（本地时间；证据 JSON 内时间为 UTC，UTC+8）。
- 任务：T-TERMINAL-PTY-20261003 P1 门禁——验证 `@xterm/headless` + `@xterm/addon-serialize`
  作为 runner 内常驻权威仿真器的候选（计划 §8，MA 接口冻结补充 §8.5）。
- 输入（只读）：集成树 `D:/project/pan-worktrees/pr6-terminal-20261002` @ `9e7e0c2f`
  `docs/design/PAN_TERMINAL_IMPLEMENTATION_PLAN_20261003.md`（重点 §7/§8/§8.5/§11 T3/T7/T17/T19/§13）。
- 工作树：`D:/project/pan-worktrees/terminal-cbc-explore-20261003`（branch `explore/terminal-cbc-20261003`）。
- 写入范围：仅 `audit/terminal/cbc/emulator/` 与本报告。未改旧 CBC 证据、生产代码、其它工作树或服务；
  未触碰任何既有进程/认证/模型服务；未派子代理。
- **诚实声明（贯穿全文）**：本 spike 的所有对比都是 **headless 仿真器之间的内部一致性**
  （参考终端 vs 快照恢复后的终端）。**没有运行真实浏览器**，因此不能据此声明浏览器渲染兼容性；
  浏览器兼容属于后续阶段的独立验收（§8 未验证项）。

## 0. 结论摘要

**引擎判定：有条件可用（conditional full）。** `@xterm/headless` 6.0.0 + `@xterm/addon-serialize` 0.14.0
可以支撑计划 §8 的“runner 内常驻权威仿真器 + 有序供料 + 序列化快照”，但 `fidelity=full` 必须限定在
**实测能力矩阵**内：

- 允许 `full` 的条件（本 spike 的 `fidelity_reasons` 为空时成立）：
  1. 使用到的终端特性全部属于**实测支持集**：alt 屏切换/退出、光标位置、滚动缓冲、resize、
     基础样式、以及 `IModes` 中除 2026 外的模式（应用光标键/键盘/括号粘贴/插入/原点/反向折返/
     焦点上报/折返）；
  2. 无 `feed_lag`（供料队列未打满）；
  3. 快照边界 `boundary_clean=true`（parser 不处于未完成的 UTF-8/ESC/CSI/OSC/DCS 中间）；
  4. 未发生过显式 `reset-baseline`（一旦发生，历史不再权威 → 永远 `partial`，不自动回到 full）。
- 任一不满足 → 快照必须声明 `fidelity=partial` + `recovery=partial|degraded` 与可读原因
  （`fidelity_reasons`），客户端走 fresh-view/重打基线。这与计划 §8.4/§8.5 的诚实性约束一致。
- 实测缺口（不是推测，见 §3/§4）：**DECSTBM 滚动区不序列化**、**synchronized output（2026）不序列化**、
  **pending parser state 不序列化**（由本 spike 的 hold-back 供料协议结构性覆盖）。
- 矩阵结果：**12 用例 = 10 pass + 2 pass-partial（声明式降级）**，无 fail；连续两次运行状态一致。

## 1. 环境与依赖 pin（installed facts）

| 项 | 值 | 证据 |
|---|---|---|
| Node | `v24.15.0`（`D:/software/nodeJs/node`） | `evidence/summary.json.versions` |
| `@xterm/headless` | **6.0.0**（exact pin） | `package.json` / `package-lock.json` |
| `@xterm/addon-serialize` | **0.14.0**（exact pin） | 同上 |
| npm | 11.12.1 | 本机 |
| 依赖安装 | `npm install`（仅 emulator/node_modules，仓库 `.gitignore:11 node_modules/` 已忽略） | `git status --ignored` |

官方 API 依据（安装包 typings/source，只读核对后再实测）：

- `write(data: string | Uint8Array, callback?: () => void)`（`xterm-headless.d.ts:847`）——
  callback 即“已应用”确认；实测 P6：`after-write-call` 先于 `write-cb`，`onWriteParsed` 触发一次。
- `onWriteParsed`（L742）、`resize(columns, rows)`（L784）、`scrollLines`（L805）、`reset()`（L867）、
  `buffer.{active,normal,alternate}`（L622/L1012-1027）、`modes: IModes`（L645/L1335-1382）。
- `SerializeAddon.serialize(options)`：选项 `range/scrollback/excludeModes/excludeAltBuffer`；
  序列化内容 = 普通缓冲（含 scrollback）+（active 为备用屏时）备用屏内容 + `_serializeModes()` 白名单
  （应用光标键、键盘、括号粘贴、插入、原点、反向折返、焦点上报、折返关、mouseTrackingMode；
  **不含** 2026、DECSTBM、光标可见性、光标样式、字符集、tab stops、标题等）。

## 2. 供料协议（spike 实现：`src/pipeline.mjs`）

计划 §8.2/§8.3 与 MA §8.5 要求的不变量，逐条落地并测试：

| 不变量 | 实现 | 证据 |
|---|---|---|
| reader 入队 ≠ 已应用 | `enqueueBytes()` 同步返回 `seq`；emulator 经 xterm write callback 推进 `applied_seq/applied_bytes` | C5：请求时 `applied_bytes=0 / enqueued_bytes=248000`（`applied_seq=0 vs enqueued_seq=17`） |
| feed/resize/snapshot/barrier 同一有序通道 | 单队列 + 单 applier 循环；快照/屏障是排在队尾的 op | C6（feed/resize 交错、dims==最后一次 resize） |
| 超时不阻塞 reader | `snapshot/barrier/resetBaseline({timeoutMs})` 到点返回 `{ok:false,timeout:true,...}`；enqueue 永不等待 | C8（stall 期间 enqueue 仍同步返回，仅显式拒绝超限块） |
| 跨块 UTF-8 保原始字节 | 只喂 `Uint8Array`；官方解码器跨 write 保持状态；**禁止**按 JS 字符串切分（P3b 实测会坏） | P3（`中A😀B` 分割喂入 == 参考） |
| partial parser 状态不丢 | applier 维护 hold-back 尾：扫描 `cleanPrefixLength()`，把未完成的 UTF-8/ESC/CSI/OSC/DCS/ST 串留在 `pending_tail`，快照携带其 base64；恢复时先喂 `pending_tail` 再续流 | C7（边界 75/82，5 字节 `4f4e442dc3`=“OND-”+CJK 首字节；恢复+续流==参考，diff=0）；C11 单测 12 种边界形状 |
| 有界队列（4 MiB / 8192 块） | 超限 `accepted:false, reason=feed_lag`；`feed_lag` **latch**、`recovery=degraded`；不静默丢字节 | C8（小上限 256 KiB 复现：4 块被显式拒绝；默认构造仍为 4 MiB 断言通过） |
| 溢出后显式重打基线、不得自动 full | `resetBaseline()`：`term.reset()` + 记录 `baseline_seq/bytes` + `recovery=partial` 并**永久**带 `BASELINE_RESET_FRESH_VIEW` 原因 | C8（degraded→reset→partial；最终快照仍 partial） |
| 快照原子三元组 | snapshot = `{serialized, cursor(applied_seq/applied_bytes), rows, cols, fidelity, recovery, pending_tail}`，与 resize 同序 | C5/C6/C12 |

补充规则（实测驱动）：hold-back 超过 8 KiB（如未终止的 OSC 标题流）时**强制喂入**并把
`boundary_clean=false`，此后快照因 `PENDING_PARSER_STATE_UNSERIALIZED` 自动降级 partial——
因为 serialize 不携带 parser 状态（P2a/P2b），此时没有安全的续流边界。

## 3. 引擎能力探测（`evidence/capabilities.json`，P1–P6 全部实测）

| 探测 | 问题 | 结果 |
|---|---|---|
| P1 | 备用屏切换能否 round-trip | ✅ 快照含 `\x1b[?1049h`，恢复后 `active.type=alternate`、屏幕内容一致 |
| P2a | 未完成 CSI（`AB\x1b[3`）是否被序列化 | ❌ 未携带；恢复后继续喂 `1mRED` 输出字面量 `AB1mRED`（解析器状态丢失） |
| P2b | 未完成 OSC（`X\x1b]0;title-`） | ❌ 同上（标题流丢失，续流被当普通文本） |
| P3 | 原始字节分块 UTF-8 | ✅ `中A😀B` 按 1/4/剩余字节切分 == 参考 |
| P3b | JS 字符串分块 UTF-8 | ❌ 代理对切断产生 `�`（协议必须只走字节） |
| P4 | DECSTBM 滚动区 | ❌ 快照不含 `CSI r`；恢复后同编辑结果不同（见 C9） |
| P5 | 模式 round-trip | ✅ 应用光标键/键盘/括号粘贴/插入/原点/反向折返/焦点上报/折返关/mouseTrackingMode；❌ `synchronizedOutputMode`(2026) 丢失 |
| P6 | write callback 时机 | ✅ callback 在解析完成后触发（`before-write → after-write-call → write-cb`），`onWriteParsed` ×1 |

## 4. 能力矩阵（`evidence/matrix/*.json`，12 用例）

| ID | 场景 | 结果 | 关键实测数字 |
|---|---|---|---|
| C11 | hold-back 边界扫描器单测（UTF-8/ESC/CSI/OSC/DCS/8-bit CSI/畸形 UTF-8） | pass | 12/12 形状符合预期 |
| C1 | 主备屏切换 / 退出 / 恢复 | pass | 备用屏内取快照（`applied=52B`，`pending_tail=4B`）；恢复+续流==参考；最终==参考；fidelity full |
| C2 | 光标 / 模式 / 滚动 / resize | pass | `diffs=0`（含 modes/scrollback/screen）；两次 resize 后 dims==40×10 |
| C3 | 全程无浏览器仍消费（>256 KiB） | pass | `324 000 B`；`applied==enqueued`、`pending=0`、snapshot 计数仅 1、屏障 0；状态==参考 |
| C4 | 客户端游标再次被驱逐 | pass | 第二次驱逐 `firstRetained=577560 > cursor`；快照游标可用；恢复 full（无 lag） |
| C5 | 异步 feed 后立即 snapshot | pass | 请求时 `applied=0 vs enqueued=248 000`；快照未声称未应用数据；恢复+续流==参考；barrier 收敛到 248 000 |
| C6 | feed/resize 交错 | pass | 快照 dims==32×8（最后一次 resize）；恢复==参考 |
| C7 | UTF-8/CSI/OSC 跨快照边界 | pass | 边界 75/82；`pending_tail=5B`；恢复+显式 pending+续流==参考（diff=0） |
| C8 | 队列停滞/溢出 → degraded + 显式 reset | pass | 4 块显式拒绝（`feed_lag`）；快照 `degraded/partial`；reset 后 `recovery=partial` 且**不自动 full** |
| C9 | DECSTBM 序列化缺口 | **pass-partial** | `fidelity_reasons=[DECSTBM_NOT_SERIALIZED]`；差异可复现（`screenText`/`serialized` 均不同） |
| C10 | synchronized output (2026) 缺口 | **pass-partial** | `fidelity_reasons=[SYNCHRONIZED_OUTPUT_NOT_SERIALIZED]`；文本仍恢复 |
| C12 | 真实 `less.exe` TUI 回放（自有 ConPTY 捕获） | pass | 3 083 B（含 `?1049h`、`2J/H/K`、颜色）；快照@1552 B、`boundary_clean`；恢复+续流==参考（diff=0） |

C12 捕获由 `tools/capture-less.py` 产生：自建 `less -R` + `TERM=xterm-256color` 进程、80×24 ConPTY、
按键序列 `\r → G → g → /word3 → q`；PID 46564、原始 creation FILETIME=`134354375799964470`
（hex `01dd5297cb938336`）；自然退出（`exited_naturally_after_q=true`）、无 watchdog 强杀、
无残留（`alive_after=false`、`signaled_after_terminate=true`、`tmp_removed=true`）。
**真实观察 1**：捕获尾部没有 `?1049l`（less 退出时最后一段离开备用屏序列未及读出/TUI 关闭）——
这正是计划 §7.1 "持续 drain 到 EOF" 的现实理由，也说明“快照永远停在备用屏”可能与真实退出画面不同。
**真实观察 2（集成坑）**：64 位 FILETIME（`1.34e17`）**超过 JS `Number.MAX_SAFE_INTEGER`**，
Node 读 JSON 时静默舍入（早期证据里 `…9964476` 曾在 JSON 中显示为 `…9964470`）。
runner ↔ sidecar 的身份字段必须以**字符串/hex** 传输（本仓库已同时记录 `filetime_str`/`filetime_hex`），
不得用 JS number 承载身份。

## 5. 序列化支持分层（诚实表，驱动 `fidelity`）

| 特性 | 分类 | 依据 |
|---|---|---|
| 备用屏内容与切换 | 支持（实测） | P1 / C1 / C12 |
| 十种 `IModes`（除 2026） | 支持（实测） | P5 / C2 |
| 光标位置 / 滚动缓冲 / 屏幕文本 / 尺寸 | 支持（实测） | C2 / C3 / C6 / C12 |
| 基础样式（fg/bg/attr，含 blank 压缩） | 支持（实测，addon 源码 + C1/C12 文本对比） | addon `_diffStyle` |
| DECSTBM 滚动区 | **缺口（实测）** | P4 / C9 |
| synchronized output 2026 | **缺口（实测）** | P5 / C10 |
| pending parser state（CSI/OSC/DCS/UTF-8 中间态） | **缺口（实测）**，由 hold-back 协议覆盖；`boundary_clean=false` 时降级 | P2a/P2b / C7 / C11 |
| 光标样式 DECSCUSR、窗口标题 OSC、tab stops、G0/G1 字符集、选择区、SGR 鼠标编码细节 | **未验证**（声明使用时强制 partial） | 未测；`FEATURE_SUPPORT` 表 |

对计划的落点建议：

- **T17（Browserless 恢复）**：本 spike 的 C3+C4 覆盖了“无浏览器连续消费 >256 KiB + 游标驱逐后取权威快照”，
  但“浏览器拿到的快照仍含被驱逐前的屏幕状态”属于**内容**判据 → 由 C3（快照==参考）与 C4（游标推进、
  不跳到窗口起点）联合支撑；验收断言建议直接引用 `fidelity_reasons` 为空与 `applied_bytes` 一致。
- **T19（供料降级诚实性）**：本 spike 的 C8 给出 runner 侧队列/降级语义的可执行参考
  （显式拒绝 + latch + 显式重打基线），断言建议：`feed_lag=true` 期间任何快照 `fidelity!=full`，
  且 reset 之后仍不得回到 full。
- **T3（TUI 与 resize）**：C12 提供真实 TUI 字节流的回放基线；ConPTY 尺寸联动属于 runner 集成阶段。
- **§13 参数**：默认 4 MiB / 8192 块、256 KiB 客户端窗口在本 spike 中被作为常量与模拟窗口使用；
  实测未出现“仿真器跟不上”（纯内存状态计算），C8 的停滞是**注入**的，不是自然发生的。

## 6. 复现命令

```powershell
cd D:/project/pan-worktrees/terminal-cbc-explore-20261003/audit/terminal/cbc/emulator
npm ci                                   # 或 npm install（pin 见 package.json/lock）
node src/capability-probe.mjs            # P1–P6 -> evidence/capabilities.json
node run.mjs                             # 全部 12 用例 -> evidence/matrix/*.json + summary.json
node run.mjs c7                          # 单用例（按名字包含匹配）
# 真实 less 捕获（可选，需 uv；自有进程 + 看门狗 + 身份核验清理）：
UV_CACHE_DIR=D:/tmp/uv-cache-pan-cbc uv run --no-project `
  --python E:/software/miniforge/python.exe --with pywinpty==3.0.5 --with psutil `
  python tools/capture-less.py
```

## 7. 文件清单与清理

提交文件（`node_modules/` 按仓库 .gitignore 忽略，未提交）：

- `audit/terminal/cbc/emulator/package.json`、`package-lock.json`
- `audit/terminal/cbc/emulator/run.mjs`
- `audit/terminal/cbc/emulator/src/{pipeline,headless-state,cases,capability-probe}.mjs`
- `audit/terminal/cbc/emulator/tools/capture-less.py`
- `audit/terminal/cbc/emulator/evidence/capabilities.json`、`summary.json`
- `audit/terminal/cbc/emulator/evidence/matrix/`（12 个用例 JSON）
- `audit/terminal/cbc/emulator/evidence/captures/less-80x24.bin`、`less-80x24.meta.json`
- `docs/design/PAN_TERMINAL_HEADLESS_SNAPSHOT_SPIKE_20261003.md`（本报告）

清理与资源：

- Node 侧全部为进程内对象（无子进程/服务/端口/PTY），运行结束后无残留；
  连续两次 `node run.mjs` 状态一致（10 pass + 2 pass-partial）。
- `less` 捕获：ConPTY 进程自然退出（`exited_naturally_after_q=true`），
  未触发 watchdog；清理走保留句柄 `WaitForSingleObject` 核验（`signaled_after_terminate=true`）
  与 `alive_after=false`；临时目录已删除（`tmp_removed=true`）。无裸 `taskkill`。
- 复检：无 `less.exe`/spike `node`/`capture-less` 残留进程，无 `pan-less-capture-*` 临时目录。

## 8. 未验证项与下一验证门（不假装已过）

1. **真实浏览器渲染兼容**（本次范围外）：所有对比为 headless↔headless。下一门：把快照喂给真实
   xterm.js 前端（fit addon、滚动、鼠标、选区）做像素/语义对照。
2. **未验证特性**：DECSCUSR 光标样式、OSC 标题、tab stops、G0/G1 字符集、选择区、SGR 鼠标编码细节、
   光标可见性（DECTCEM）——`FEATURE_SUPPORT` 中标 `unverified`，一旦声明即 partial。
3. **自然供料停滞**：本次 `feed_lag` 为注入模拟（stall op）；真实 runner 下“仿真器是否跟得上”
   需要带 PTY 的长时间压测（>4 MiB 突发 + 持续输出）。
4. **跨进程/跨重启快照原子性**：本 spike 在单进程内验证了原子有序供给；runner（Python）↔ sidecar
   的 IPC（命令通道、applied_seq 回传、崩溃恢复）未实现，属于 P1 集成工作。
5. **ConPTY 尺寸/编码联动**（ResizePseudoConsole → feed resize 顺序、UTF-8 输出边界）未在本 spike 覆盖；
   C12 只回放了已捕获字节流。
6. **版本兼容**：pin 在 6.0.0/0.14.0；升级需重跑本矩阵（`fidelity_reasons` 变化即视为回归）。

## 9. 与 CBC 探索的关系

本阶段是 Pan Terminal 的 P1 快照门禁，与 `audit/terminal/cbc/` 的 CBC 原生 TUI 探索是两条独立的
验收线：CBC 部分按 MA 指令**未继续**（不做原生 TUI/模型实验），本 spike 不依赖任何 CBC 进程、
认证或模型服务；两者共享的仅有“清理必须基于身份核验（原始 FILETIME/同 handle）”这一纪律。
