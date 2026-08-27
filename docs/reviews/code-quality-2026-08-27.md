# PAN 项目全局代码质量审查报告（2026-08-27）

- **分支**：main（审查时工作区为 main，commit 未记录）
- **审查方式**：5 个并行只读审查（core+main / web server / React 前端 / mcp+qq / 测试覆盖），高危结论逐一人工核验源码确认
- **性质**：只读审查，未改动任何代码

## 审查范围

- main.py（服务入口）
- packages/core/（worker/session/watchdog/adapter/memory/manifest/character）
- packages/web/（server.py HTTP/WS API + React 前端 src/ + legacy ts/app.ts）
- packages/mcp/（MCP server + monitor_workers）
- packages/qq/（plugin + channels/onebot + bot + mcp）
- tests/（覆盖缺口分析）

## 审查维度

架构与模块边界、代码坏味道、错误处理（区分有意静默降级与疏忽）、安全（已知：API 无鉴权但绑 loopback）、并发/异步、测试覆盖缺口、性能。

---

## 一、高危（已核验或证据充分，建议立即修）

### H1. `os.kill(pid, 0)` 在 Windows 上会直接杀掉目标进程

- `main.py:31-37`（`_is_pid_alive`）
- Windows 上 `os.kill` 的 sig 非 CTRL_* 事件时一律走 `TerminateProcess`（sig 即退出码），sig=0 不是"探测"而是"杀死"。`_spawn_qq_bot`（main.py:169）的防双启实际效果：每次重启 Pan 都把上一个 QQ bot 杀掉，再因"pid 还活着"跳过拉起 → **QQ 模块静默死亡**。
- 修复：用 `psutil.pid_exists(pid)`（worker.py 已依赖 psutil），或 win32 分支用 ctypes `OpenProcess`。

### H2. QQ mirror 回复路径必然崩溃：`lr` 未赋值即引用

- `packages/qq/plugin.py:509`（赋值在 :513）
- `if session and lr and lr.get("timestamp")` 引用的 `lr` 在 4 行之后才赋值，必然 `UnboundLocalError`；异常被 NoneBot dispatcher 吞掉 → **每条消息在"取回复"最后一步崩溃，用户收不到任何回复**，日志只有一条异常。
- 修复：把 `lr = data.get("lastResult") or {}` 上移到 :509 之前。

### H3. server.py `_log.warning` 调用必然 AttributeError

- `packages/web/server.py:36`（`_log` 是普通 print 函数，无 `.warning`）+ 两处调用：`_cleanup_mcp_config` / `_cleanup_kimi_home` 的 `except OSError` 分支
- 删 session 时若文件被占用（Windows 常见），异常处理器自身二次崩溃，`DELETE /api/sessions/{id}` 与 batch-delete 直接 500，且掩盖原始清理失败。
- 修复：改 `print(f"[...] ...")` 或统一引入标准 logging。

### H4. 群消息 @ 过滤 int/str 类型不匹配，群聊全丢

- `packages/qq/channels/onebot.py:105-112`
- `int(bot.self_id)` 与 `seg.data.get("qq")` 比较成员关系，但 OneBot v11 协议 `At.data["qq"]` 是字符串（NapCat 实发亦为 str）→ `int not in [str]` 恒 False → **所有群 @ 消息被丢弃**。`test_channels.py:133` 的 Fake 事件用 int 构造，恰好掩盖了该 bug。
- 修复：两侧 `str()` 归一后比较，测试替身改用 str 锁定契约。

### H5. MCP `session_managed` 读错字段层级，受限编排恒拿空列表

- `packages/mcp/server.py:483` vs `_caller_identity()`（:116-142）
- `restrictToManaged` 已被归一进 `caller["panAccess"]`（docstring 明说），:483 却读顶层 → 恒为 None → 直接 `return []`，**受限 caller 静默丢失全部 managed 会话**，编排者误判"无可管理会话"。
- 修复：`_caller_pan_access(caller).get("restrictToManaged")`。

### H6. WS CONNECTING 窗口 send 假成功，队列消息永久丢失

- `packages/web/src/services/ws.ts:76-84`，联动 `stores/queueStore.ts:155-181, 447-505`
- `send()` 在 CONNECTING 时把消息挂到旧 socket 的 `open` 事件并返回 `true`；连接随后失败则 handler 随 socket 销毁，消息丢失但调用方已确认"成功"。queueStore 的 flush 据此 `onSent(true)` → **从内存和 localStorage 删除队列项，用户消息无提示丢失**。
- 修复：CONNECTING 时返回 false 或引入 pending 缓冲 + 失败回调；flush 收到失败时保留队列项。

### H7. legacy 前端 markdown 裸渲染 XSS

- `packages/web/ts/app.ts:350-354, 371, 1201-1204`
- `marked.parse()` 输出未经 sanitize 直接 `innerHTML`（全仓库无 DOMPurify）。worker 输出是不可信内容 → `/` 页面存在存储型 XSS（`javascript:` 链接、内联 HTML、`<img onerror>`）。React 端走 react-markdown 默认转义，安全。
- 修复：引入 DOMPurify 消毒，或 marked renderer 白名单。legacy 虽是备份但长期共存。

### H8. 同步嵌入推理/重 IO 阻塞 asyncio 事件循环（一类问题，多处）

- `packages/web/server.py:2564, 2599`：memory `index_directory()` / `search()` 同步调用，含 sentence-transformers 推理，index 整目录可达分钟级 → **期间所有 HTTP/WS 停摆**
- `packages/core/adapters/cbc/adapter.py:448`、`kimi/adapter.py:432`：`enrich_after_result` 内 `time.sleep(0.2/0.3)` + 全量读 JSONL，被 worker 在事件循环内同步调用（worker.py:495, 1368）→ 每轮 result 阻塞 0.3s~数百 ms
- `cbc/adapter.py:40`（10s）、`opencode/adapter.py:543`（15s）：模型列表 TTL 过期后同步 `subprocess.run`
- 修复：统一包 `asyncio.to_thread`（core 里 `_maybe_inject_memory` 已有先例）。

---

## 二、中危

### 并发/资源（core）

- **M1** `worker.py:553-593` — stdout EOF 路径 pop worker 但不 cancel `_consume_task`，consumer 协程永久挂在信号上泄漏（`_signal_task_done` 只唤醒一次，循环后再次挂起）。EOF 路径补 cancel，对齐 kill_worker。
- **M2** `worker.py:615-669` — `_consumer` 循环无异常兜底，进程刚死时 stdin write 抛 BrokenPipe/ConnectionReset 击穿 consumer → 任务静默积压到 watchdog 300s 超时。循环体包 try/except。
- **M3** `worker.py:2029-2041` — assign 幂等是 check→await→register 的 check-then-act，跨 await 点，并发同 taskId 可双跑。先占位后执行。
- **M4** kimi wrapper 管道处理三处缺陷（codex/opencode 已修的同款问题没回灌）：
  - `kimi/wrapper.py:152-158` spawn 未设 `stdin=DEVNULL` → kimi 与 `_stdin_reader` 两读者抢同一管道，任务字节可被偷走
  - `kimi/wrapper.py:87-104` stdin EOF 不入 `None` 哨兵 → 主循环永久挂 `queue.get()` 成孤儿进程
  - 对照 `codex/wrapper.py:231`、`opencode/wrapper.py:192`：`communicate()` 与 stderr pump 线程并发读同一管道 → stderr 丢失/偶发 Errno 22（kimi:69-71 注释明确记录过此坑）
- **M5** `worker.py:1897-1900` + 4 个 adapter — `supports_fork=True` 但 `fork_args` 返回 `[]`；kimi 的 `fork_args`（kimi/adapter.py:182-201）先执行 fork 副作用（复制会话文件、改 cli_session_id）再返回 `[]` → branch_worker 拒绝时**磁盘已留孤儿会话、状态已 mutate**。声明 `supports_fork = False` 或纯净化 fork_args。

### server.py（API 层）

- **M6** `server.py:2832-2841` — `react_spa_fallback` 无 `resolve()+relative_to()` 包含检查，是任意文件读原语；当前被 :2826 的 StaticFiles mount 遮蔽成**不可达死代码**，但路由顺序一调即爆。加同款包含检查或删除。
- **M7** `server.py:371-388` — `_resolve_workdir` 对绝对路径仅检查恒为 `None` 的 `_ALLOWED_WORKDIR_ROOTS` 即 mkdir → 任意位置创建目录树。兑现白名单。
- **M8** `server.py:1784-1788` — `/api/cbc/sessions?project_dir=` 未拦 `..`，可越界枚举任意目录 `.jsonl` 元数据。core 侧做包含检查。
- **M9** `server.py:897-899` — `/ws/agent` spawn 分支不捕 `ValueError`（HTTP 侧有）且不查重名，WS 连接异常断开。补齐对齐 HTTP 行为。
- **M10** 错误约定三套并存（`{"error"}` / `{"ok":false,error:{code,message}}` / `{"ok":true}`），HTTP 永远 200 → 调用方无法统一判错，监控/重试失效。统一形状，渐进迁移。
- **M11** `server.py:232-284, 287-326` + `core/config.py:118-124` — `load_config()` 每次读盘解析，`_session_to_api` 每 session 调一次 → `GET /api/sessions` O(N) 次同步磁盘 IO。加 mtime 缓存，修复成本低收益立竿见影。
- **M12** `server.py:1276, 1301, 1057` + `worker.py:1718` — `asyncio.create_task` 返回值不保存，task 可被 GC 静默取消（worker 清理/respawn 偶发无声失败）。存集合 + done callback 记异常。
- **M13** `server.py:2748-2752, 2684-2804` — fs write 固定 `.tmp` 后缀并发互相覆盖；fs 五端点 5MiB 同步 IO 跑在事件循环。tmp 加 uuid；IO 包 to_thread。
- **M14** `server.py:2267-2303` — server 自持 `_memory_managers` 缓存与 core `memory_context` 缓存并存，lifespan 只清 core 那份 → 模型/连接泄漏。合并为 core 单一缓存。

### qq / mcp

- **M15** `qq/plugin.py:96-104` — `_refresh_command_routes` 失败也置 `_command_routes_loaded = True` → 一次瞬时失败永久禁用（与 :659 注释"lazy-retry"矛盾）。失败保持 False。
- **M16** `qq/plugin.py:476-500` — `_pending[session_id] = evt` 并发覆盖，先完成方删掉后一条的 waiter → 同会话快速两条消息一条超时/错乱。per-session 锁或 waiter 集合。
- **M17** `qq/plugin.py:546-609` — `handle_qq_message` 无顶层 try/except，任何异常 = 用户只看到"processing"后永久沉默。包一层，失败回送错误。
- **M18** `mcp/server.py:1016-1021`（同模式 :1099, 1139）— `_worker_session_id()` 返回 None 时**跳过 `_check_access` 直接放行** kill/restart/task；`worker_list`（:1025）无隔离过滤 → 受限 caller 可操作任意 worker。None 按 deny 处理。
- **M19** `mcp/server.py:54-72` — `_api` 只捕 HTTPError/URLError/TimeoutError，`read()` 阶段 ConnectionReset / 非 JSON 响应的 JSONDecodeError 漏网 → LLM 收到裸异常。外层补 `OSError/JSONDecodeError`。
- **M20** `qq/plugin.py:56` — `_WS_URL` 由 http→ws 简单替换，https 部署下得不到 wss → WS 主通道不可用退化为轮询。按 scheme 分支映射。

### 前端

- **M21** `src/stores/sessionStore.ts:666-674` + `ThinkingBlock/ToolGroup` — `clearUnread` 全仓库零调用，`markUnread` 把每条 thinking/tool **完整内容字符串**塞进 Set 永不清理 → 未读点反复出现（行为退化）+ 长会话内存泄漏。展开时 delete，removeSession 同步清。
- **M22** `Sidebar.tsx:44-67`、`TopBar.tsx:20-23`、`InputRow.tsx:164-182` — zustand 无 selector 全量订阅 → 流式期间每个 chunk 触发 Sidebar 整树重渲染（专门做的 React.memo 被父层击穿）。改细粒度 selector。
- **M23** `ImportModal.tsx:98-242` — 三 adapter 约 250 行逐行重复 + fetch effect 无 stale-response 守卫 + 重置 effect 漏依赖。抽 `useAdapterImport(adapter)`。
- **M24** `sessionStore.ts:450-497` — `removeSession` 不清 `inputDrafts[id]`/`sessionUnread[id]`，慢性泄漏。同 M21 一并修。
- **M25** `services/ws.ts:121-135` — 重连 timer 不可取消；心跳只发 ping 不检测 pong → 半开连接 UI 假显示"已连接"。保存 timer + pong 超时主动 close。

---

## 三、低危（择要）

- `worker.py:1419-1425` `_consumer_mcp` 自述"下个 PR 删除"的别名仍在；`:1718` respawn task 无引用可被 GC；`:1433-1438` `_spawn_locks` 永不清理；`:1661` kill 时防抖 flush 循环可能空转不退出
- `worker.py:1787-1864` restart/respawn 不持 spawn 锁，watchdog 可插入 → 同 session 双 worker
- `memory/__init__.py:313-319` 空 .md 触发 `NameError`（`embeddings` 未初始化）；`embedder.py:494-508` bge 模型误用 8191 截断阈值（上限 512）
- `manifest_loader.py:332-356` `${PLUGIN_DIR}` 替换无 containment 校验，与 memory_dir 的防护标准不一致
- `session.py:608-741` handoff 多次独立 save，中途崩溃留半更新状态；`:496-503` delete 不清 `.json.tmp`
- watchdog 对持续失败 session 每 30s 无退避重试（worker.py:1037-1058）
- `mcp/monitor_workers.py:63-68` 裸 `except: return None`；`mcp/server.py:815,1165` f-string 拼 query 未 urlencode
- `server.py:344` `_MAX_TEXT_LEN` 定义未用，text 无长度上限；`:1289` batch-delete 不校验 list 类型；`:2655` min_score 未捕获 ValueError
- `qq/plugin.py` 每消息 2-3 次同步读 config + 同步文件 IO；`qq/mcp.py:53` 每次调用新建 httpx client；`main.py:181` 覆盖而非追加 PYTHONPATH
- 前端：`sessionStore.ts:347` `data.history || data.history` 笔误重复；`api.ts:37-46` 丢弃后端 error body；`Toast.tsx` 计时器互相重置；`CommandPalette` sess-import TODO 死入口；`DetailPanel.tsx:67` aside 缺 `relative` 手柄错位；`useVisualViewport.ts` 整文件无引用
- 重复代码：kimi/codex/opencode 三个 wrapper 脚手架约 150 行同构（可抽基类）；qq `_get/_post/_patch` 与 history/inbox 两套平行实现
- 超 100 行函数：`worker.py _consumer_oneshot`（约 275 行）、`_read_stdout`（约 175 行）、`session.py handoff_session`（约 134 行）、`mcp/server.py session_import`（118 行）

---

## 四、测试覆盖缺口

覆盖呈**哑铃形**：worker 状态机/watchdog/session 持久化/report 订阅单测密度高质量好；HTTP/MCP 边界和进程级写路径近乎裸奔。

### 高优先缺口

1. **worker 生命周期写操作零测试**：`kill_worker`/`restart`/`branch`/`_kill_process_tree`/`cleanup_worker_background`（worker.py:1632-2103）——watchdog 测试里 kill 全被 mock，真实实现从未被测过。杀错 PID、进程树泄漏无回归保护。
2. **server.py 70 个端点只有约 8 个有测**，全部写操作（spawn/task/assign/kill/restart/takeover/delete/batch-delete）无 HTTP 层测试。
3. **`/api/fs/write|delete` 及 `_resolve_fs_path` 越界防护零测试**——全项目最危险的未测面，且恰是项目已明确的安全重点（workdir/manifest 边界）。
4. **memory 启用路径**：embedder 降级、watcher 循环、`_maybe_inject_memory` 启用分支均无测（只测了 disabled 跳过）；四个 memory 端点无测。
5. **MCP 27 个工具约 16 个无测**，含 session_delete / batch_delete / worker_kill / worker_send_force 等写操作。

### 中优先缺口

- session.py 剩余函数：`branch`、`rename`、`accumulate_raw_usage`/`compute_total_usage` 用量合并数学、`_migrate_legacy_fields`（纯函数表驱动单测成本极低）
- manifest_loader.py 无专测：坏 manifest 降级路径、`/api/manifest/reload`、command-routes
- qq/bot.py 完全无测（NoneBot 启动/事件桥接）
- adapters/registry.py、resolution.py、base.py、mcp.py 无直接测试
- WS 握手/断连：`/ws`、`/ws/agent` 连接建立、死连接清理、`_send_ws` 失败路径无端到端测试
- opencode adapter 最薄（仅模型目录解析，无 spawn/parse/import 测试）

### 测试基础设施问题（放大以上所有缺口）

- 全仓**无 pytest.ini/pyproject 配置、无 CI、pre-commit 不跑 Python 测试**；README 唯一命令 `pytest tests/` 使 `packages/qq/test_*.py` **事实不运行**（且 import 即 `nonebot.init()`，包外环境直接 ImportError）
- 假绿：`test_claude_adapter.py` probe 系列缺目录时 `print("SKIP")` 后正常返回，pytest 记 PASS
- 脆弱：`test_worker_watchdog.py:55,596` 等真实 sleep 等 tick，慢机器误报
- adapter 覆盖不均：codex 最厚、opencode 最薄、cbc/kimi 充分

---

## 五、总体评价

架构基本面良好：core 集中、五 adapter 有统一协议与共享抽象（重复总体可控）、qq/mcp 生产代码未越层 import core、路径防护主范式（`_resolve_fs_path` 的 resolve+relative_to）正确、注释里大量"踩坑编号"显示问题驱动迭代健康。

但本次审查发现的最突出问题呈两个鲜明模式：

1. **"静默失效"型 bug 密集**——QQ 回复崩溃、群聊全丢、managed 恒空、WS 假成功、command-routes 永久禁用，全是功能坏了但无报错，说明"错误必须可见"这条纪律在边界层（NoneBot dispatcher、异常处理器、WS 回调）失守。
2. **同类修复不回灌兄弟实现**——三个 wrapper 各坏一处同款管道问题、WS spawn 与 HTTP 行为不一致，修一处漏一处。

API 层 2847 行承载六大域业务逻辑是最大的结构性债务。

### 最优先修的 5 项（多为小改动、高回报）

1. **`main.py:31` `os.kill(pid,0)`** — Windows 上 QQ bot 防双启变"每次重启杀死 QQ"，换 `psutil.pid_exists` 一行事
2. **`qq/plugin.py:509` `lr` 未绑定 + `onebot.py:105` int/str** — QQ mirror 回复全断 + 群聊全丢，两处各一行
3. **`server.py` `_log.warning`** — Windows 文件占用时删会话 500，改 print/log 两处
4. **`ws.ts:76` CONNECTING 假成功 + `app.ts` 裸 innerHTML** — 用户消息永久丢失 + 存储型 XSS，前端唯一两个真高危
5. **事件循环阻塞族**（server.py:2564 memory 端点、cbc/kimi enrich 同步读、模型列表 subprocess.run）— 全局周期性卡顿的根因，统一包 `asyncio.to_thread`

次优先：M18（MCP worker 隔离绕过）与测试基础设施（pytest 配置 + 把 server.py fs/写端点测起来）。
