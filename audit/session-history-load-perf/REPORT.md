# Session 列表 / History 冷加载与弱网优化（TA 交付）

基线 `03158ec517a98493e41b292b7f0575fefb49fe12`，分支 `feature/session-history-load-perf-20261024`。
本报告只描述本工作树得到的测量与改动；不含 main / practical 推进、push、真实服务重启。

## 1. 结论

| 场景（真实 Chromium，生产 bundle） | 指标 | 修前 | 修后 | 变化 |
|---|---|---:|---:|---:|
| cold-fast（浏览器空缓存，无节流） | Session 列表可见 | 356 ms | 164 ms | **-54%** |
| cold-fast | FCP | 252 ms | 96 ms | **-62%** |
| cold-fast | 导航阶段请求数 | 41 | 30 | **-27%** |
| cold-weak（1.6 Mbit/s / 150 ms RTT，空缓存） | Session 列表可见 | 2720 ms | 2314 ms | **-15%** |
| cold-weak + 1.2 s 思考时间 | Session 列表可见 | 2725 ms | 2332 ms | **-14%** |
| cold-weak + 1.2 s 思考时间 | 点击→首屏 history 可读 | 217 ms | 218 ms | 持平 |
| cold-weak（卡片出现即点击，最坏情形） | 点击→首屏 history 可读 | 982 ms | 1308 ms | +33%（见 §7.2） |
| cold-weak | 导航阶段请求数 | 35 | 28 | **-20%** |
| warm-fast / warm-weak（浏览器热缓存） | 列表 / history | 90 / 541 ms、51 / 225 ms | 106 / 543 ms、49 / 235 ms | 区间重叠，无确定变化 |

服务端数据面未改动（同代码、同数据集）：`GET /api/sessions?summary=1` 30.1 ms / 3.19 KB(gzip)，
`/api/sessions/{id}/history?before=0&limit=50` 32.5 ms / 618 B(gzip)，40 000 行会话首读 41.7 ms。
这些量级远小于 150 ms RTT，**本轮不动服务端读取与缓存**（见 §4.3）。

## 2. 环境与不可越界项

- 隔离工作树 `D:/project/pan-worktrees/session-history-load-perf-20261024`，`git status` 干净起步，HEAD = 指定基线。
- 真实 HTTP/WS：`tests/support/isolated_http_server.py`，随机 loopback 端口；代码层拒绝 8767/8768。
- 真实生产 Chromium：`C:\Users\14709\AppData\Local\ms-playwright\chromium-1243`（Playwright 1.63），headless。
- 数据：临时 data root，`audit/session-history-load-perf/seed.py` 通过真实 store 写入
  31 个 Session / 76 000 行 / 13.7 MB（30 × 1 200 行 + 1 × 40 000 行），每次运行重新播种，无真实用户数据。
- 固定同一机器、同一数据集、同一 Node/Python/浏览器，先跑基线再跑候选。

## 3. 测量方法

复现（仓库根目录）：

```bash
# 变量：--label 只是证据标签；--repeat 3 = 每个场景 3 次取中位数
E:/software/miniforge/python.exe audit/session-history-load-perf/measure.py \
  --label candidate --out audit/session-history-load-perf/candidate.json --repeat 3
```

流程：播种 → 起一个隔离 Pan（fakeCLI，队列 watchdog 关闭）→ 等服务器进入稳态 → Node 脚本用
Playwright 生产 Chromium 跑 5 个场景各 3 次，产出原始 timeline 与中位数。

指标定义（页内 `performance.now()`，即“相对 navigationStart”）：

- **列表可见**：`[data-session-card-id]` 首次出现在 DOM（MutationObserver 打点，不含 Playwright 轮询延迟）。
- **首屏 history 可读**：点击卡片后，渲染出**该 Session 自己的**且文本非空的历史行（用会话标记串匹配，
  避免复用上一个 Session 的残留行）。点击时刻也在页内打点，因此扣除 Playwright 调用开销。
- **请求数/字节**：CDP `Network.loadingFinished.encodedDataLength`（线上字节，含 br/gz 压缩后）。
- 场景：`cold-fast`、`warm-fast`、`cold-weak`、`warm-weak`、`cold-weak-think`（列表出现后等 1.2 s 再点击）。
- 弱网：CDP `Network.emulateNetworkConditions`，150 ms RTT、1.6 Mbit/s 下行、750 kbit/s 上行（HTTP/1.1，未模拟丢包）。
- 服务器稳态：`settle()` 等待启动期一次性卡顿结束后，再要求连续两次读 < 120 ms（见 §8.2）。

原始证据：`baseline.json` / `candidate.json`（中位数 + 服务端逐次样本）、`baseline.browser.json` /
`candidate.browser.json`（3 次 × 5 场景的完整请求时间线）。

## 4. 测量发现的瓶颈

### 4.1 首屏 CSS 里有一个渲染阻塞的第三方字体 `@import`

基线 `cold-weak` 时间线：HTML 0→191 ms，入口 chunk 185→1388 ms，但**首个依赖 chunk 到 1406 ms 才发起**；
`cold-fast` 上更清楚：入口 chunk 98.5 ms 已下载完，`ChatView` 图到 218 ms 才开始，FCP 192–264 ms。

原因：`src/index.css` 第一行是 `@import url('https://fonts.googleapis.com/...')`。Vite 原样保留成
`@import"https://fonts.googleapis.com/..."`，浏览器的 module script 必须等 CSSOM 完整才执行，
而 CSSOM 要被这张跨域样式表（150 ms RTT、1.2 KB）补齐。等于把 **应用启动和第一次数据请求** 挂在一个第三方域名的往返上；
该域名不可达时，首屏会被拖到浏览器放弃该样式表为止。

### 4.2 图标被打包成十几个独立小 chunk，抢占启动连接

基线 `cold-weak` 导航阶段共 21 个 script 请求，其中 16 个 < 3 KB，10 个是 lucide 单图标模块
（`copy`、`play`、`pause`、`check`、`clipboard`、`download`、`file`、`terminal`、`user-round`、`external-link`），其余是应用小模块。
构建产物层面共有 14 个 lucide 单图标 chunk。Chrome 对同一 origin 的 HTTP/1.1 并发连接有限，
这些小请求每个都要占一个连接和一个 RTT。实测：把它们合成 1 个 chunk 后，导航阶段 script 请求 21 → 13、
`< 3 KB` 的 16 → 7、总请求 35 → 28；`/api/sessions?summary=1` 的响应完成时间从 2730 ms 提前到 2334 ms
（同一次 `cold-weak` 时间线），列表可见时间 -390 ms。

### 4.3 服务端读取不是本轮瓶颈

| 端点 | 中位耗时 | 线上字节 |
|---|---:|---:|
| `GET /api/sessions?summary=1`（31 会话） | 30.1 ms | 3 186 B |
| 同上（重复读） | 31.8 ms | 3 186 B |
| `/api/sessions/ses-perf-00/history?before=0&limit=50`（首读） | 32.5 ms | 618 B |
| 同上（重复读，命中 tail page cache） | 28.4 ms | 618 B |
| `/api/sessions/ses-perf-large/history?before=0&limit=50`（40 000 行首读） | 41.7 ms | 624 B |
| 同上（`before=20000` 翻旧页，非 tail） | 41.5 ms | 624 B |
| `GET /api/sessions`（兼容全量列表，React 不用） | 529.5 ms | 17 085 B |

首读与重复读、40 000 行与 1 200 行的差异都在 10 ms 级，且列表/历史响应压缩后只有 3.2 KB / 0.6 KB。
相较 150 ms RTT 可忽略，因此**没有**改 `history_page`、没有新增服务端缓存（改动会放大正确性风险却不改变观感）。

## 5. 改动（3 个源文件 + 1 个构建校验）

1. `packages/web/src/index.css`：删掉渲染阻塞的字体 `@import`（保留文件顶部注释说明原因）。
2. `packages/web/src/index.html`：`preconnect` + `media="print" onload="this.media='all'"` 非阻塞加载同一 Inter 样式表，
   另加 `<noscript>` 兜底。字体族、字重、`display=swap` 语义不变，只是不再阻塞 CSSOM。
3. `packages/web/vite.config.ts`：`manualChunks` 增加 `lucide-react → icon-vendor`，把十几个单图标 chunk 合成 1 个
   （45.2 KB raw / 8.4 KB br）。产物：`dist/assets/*.js` 57 → 45 个，入口 chunk 415 KB → 394 KB（br 109.5 → 105.2 KB）。
4. `packages/web/e2e/verify-precompressed-assets.mjs`（每次 `pnpm build` 与 CI 都跑）：新增三条冷加载回归断言
   —— CSS 不得含外部 `@import`；webfont `<link rel=stylesheet>` 必须非阻塞（`media="print"`/`preload`）；
   必须恰好存在一个 `icon-vendor-*.js`。已验证这三条在基线上会失败（见 §9 的红色证据）。

新增测量工具（不进运行时）：`packages/web/e2e/session-load-perf.mjs`、`audit/session-history-load-perf/{seed.py,measure.py}`。

## 6. 前后对比

### 6.1 浏览器（3 次中位数；括号为 3 次样本）

| 场景 | 指标 | 修前 | 修后 |
|---|---|---:|---:|
| cold-fast | 列表可见 | 356 ms (1910/278/356) | 164 ms (1771/164/152) |
| cold-fast | FCP | 252 ms (336/192/252) | 96 ms (232/96/92) |
| cold-fast | 导航请求 / 字节 | 41 / 550.6 KB | 30 / 541.6 KB |
| cold-weak | 列表可见 | 2720 ms (2720/2720/2730) | 2314 ms (2312/2324/2314) |
| cold-weak | 点击→history（即点即看） | 982 ms (982/991/945) | 1308 ms (1308/1336/1293) |
| cold-weak | 导航请求 / 字节 | 35 / 535.9 KB | 28 / 532.5 KB |
| cold-weak-think | 列表可见 | 2725 ms (2725/2711/2725) | 2332 ms (2318/2332/2342) |
| cold-weak-think | 点击→history | 217 ms (217/216/217) | 218 ms (206/218/221) |
| warm-fast | 列表 / history | 90 / 51 ms | 106 / 49 ms（样本 73–112 与 82–92 重叠） |
| warm-weak | 列表 / history | 541 / 225 ms | 543 / 235 ms（history 样本 220–340 vs 225–283） |
| cold-weak | 40 000 行会话 history | 320 ms | 303 ms |
| 全部场景 | history 阶段请求数 | 8–11 | 8–11（未新增请求） |

`cold-fast` 的第 1 次样本（1910/1771 ms）都撞上了 §8.2 的服务端启动卡顿，两台机器两边一致，中位数取剩下两次。

### 6.2 服务端（未改代码，仅作对照）

见 §4.3 表；候选构建前后的服务端逐次样本见 `baseline.json` / `candidate.json` 的 `server` 字段
（`list` 30.1/31.8 ms、`historyStandardFirst` 32.5/28.4 ms、`historyLargeFirst` 41.7/35.0 ms）。

## 7. 试过但未采用（保留失败证据）

### 7.1 让列表请求排在聊天视图图之前（`candidate 1/2`）

把 `preloadChatView()` 从 router 求值期改到 App effect、再把 `loadSessions()` 提到 `main.tsx` 模块作用域。
测量结果：`cold-weak` 列表 2710 / 2723 ms（基线 2720），**无改善**。原因：`ChatRoute` 挂载 `ChatView` 后
`deferredView` 的 effect 必然 `import()`，图无论 preload 与否都在 1.4 s 左右开始；HTTP/1.1 只有 6 条连接，
240 KB 的图把连接占满后，3.2 KB 的列表响应只能排队。结论：顺序不是杠杆，**字节数/请求数**才是。
两处改动因此全部回退（未留在交付里）。

### 7.2 把 ws 快照后的列表刷新改走 `scheduleRefreshSessions()`（`candidate 1`）

启动仍有 2 次并发列表读（WS effect + 快照），改用节流器只是把“收尾读”推迟 300 ms 并要求额外状态，
请求数没减少（3→3），属于无收益的行为变更，已回退。

### 7.3 关于“即点即看”history 变慢 326 ms

列表提前 400 ms 出现，意味着用户点击时 `markdown-vendor`（163 KB）还剩更多没下完，而首条消息是 Markdown，
必须等它。三次样本完全分离（982/991/945 vs 1308/1336/1293），不是噪声。
从导航到 history 可读的**总时长**：2720+982 = 3702 ms → 2314+1308 = 3622 ms（-2%），
且 1.2 s 思考时间下 217 → 218 ms 持平。若 MA 认为“即点即看”必须不退化，可单独去掉 §5.3 的图标合并
（列表回落到基线水平、history 回到 982 ms 附近），字体改动与列表无关，可保留。

## 8. 正确性与缓存一致性

### 8.1 没有新增任何缓存

改动只涉及 CSS 引入方式与构建分块。**没有**新增 localStorage/IndexedDB/内存缓存、没有改服务端读路径、
没有新增请求或去重层，因此不存在“跨用户/跨实例串缓存”“把过时缓存当权威新状态”“暴露受限数据”的新路径；
服务端响应字节与字段集合不变（§4.3 同一构建、同一数据集、同一端点）。

### 8.2 既有语义未被触碰（负控）

- `historyEpoch` / `historyRevision` / `summaryRevision`、跨 Session 迟到响应、WS 增量、rewind、
  Queue 原 ID / unknown 语义、附件授权：这些代码本轮**一行未改**。对应回归用现有测试验证，全部通过（§9）。
- 前端窗口合并/拒收逻辑（`messageOrdering` 的 `ambiguous-epoch-page`、`older-epoch-page`、overlap 拒收）
  与 `sessionStore` 的 `_selectionSeq`/`_pageSeq`/`_refreshSeq` 守卫：972 个 store/chat 用例通过。
- 真实 HTTP/WS 冷加载 E2E（并发列表 + history + 心跳 + fakeCLI worker）：2 个用例通过。
- 浏览器 HTTP 缓存语义：hashed `assets/*` 仍 `immutable` + 预压缩 `.br/.gz`（构建校验 96 个资源字节一致），
  `index.html` 仍 `no-cache`；SPA 深链回退、缺失 dist 的 503 行为未改（`test_react_static_assets.py`、`test_web_frontend.py` 通过）。
- 负控“缓存不串实例”无法用新增缓存来测，因为本轮没有新增缓存；可复核的是上表这些既有守卫测试。

### 8.3 一个已知的启动期现象（未修，仅记录）

在全新进程上，约在首个请求后 1.3–1.6 s 处存在**一次性**约 1.3 s 的请求卡顿：连续 400 次轮询
`/api/adapters` 只有 1 次 1336 ms，p95 40 ms；重复 3 次进程都能复现。它会命中那个时刻正好在飞的请求
（本轮 `cold-fast` 的 1 号样本、`cold-weak` 的 `historyLarge` 都可能被它抬高）。
用 WS resync 复测：快照本身只有 2–4 ms / 65 KB，因此不是快照构建，尚未定位到具体启动任务，
**本轮未修**（属启动期后台任务，改动面与验证成本超出“最小优化”）。

## 9. 验证与退出码

| 检查 | 命令 | 结果 |
|---|---|---|
| 类型检查 + 构建 + 资源校验 | `cd packages/web && pnpm build` | exit 0，`Verified 96 compressed production assets` |
| 冷加载回归断言（负控：基线必失败） | `node e2e/verify-precompressed-assets.mjs`（基线树） | exit 1，`index-t1BeNpq3.css contains a render-blocking external @import` / `icon modules are not grouped into one icon-vendor chunk` |
| 服务端历史/列表/静态/前端单测 | `python scripts/run_tests_isolated.py -- python -m pytest tests/test_eventloop_history_offload.py tests/test_history_tail_chunked.py tests/test_react_static_assets.py tests/test_web_frontend.py tests/test_real_history_coldload_e2e.py -q` | exit 0，76 passed（22+47+1+4+2） |
| store / chat 前端用例 | `cd packages/web && npx vitest run src/stores src/components/chat` | exit 0，69 files / 972 passed、1 个既有 skip |
| views 用例 | `cd packages/web && npx vitest run src/views src/stores/messageOrdering` | exit 1：`views/PluginPanelView.test.tsx` 1 个用例失败（界面入口分组 label）。**既有失败**：把本轮改动全部 stash 后同样的用例同样失败，与本轮无关 |
| 前后测量 | `python audit/session-history-load-perf/measure.py --label {baseline,candidate} ...` | 各 3 次 × 5 场景，见 §6 |

## 10. 剩余边界

1. `cold-weak` 列表仍有 2.3 s：启动字节就是 532 KB（入口 105 KB br + react 87 + markdown-vendor 162 + ChatView 65 + icon 8）。
   继续下降需要把聊天视图图 / Markdown 渲染移出首屏路径——那是渲染与产品行为改动（首条消息会先出纯文本再重排），
   超出“最小优化”，本轮没做。
2. 即点即看的 history 在弱网下 +326 ms（总时长略降），见 §7.3。
3. 服务端启动期一次性 ~1.3 s 卡顿未定位、未修（§8.3）。
4. Inter 仍是第三方依赖（现为非阻塞；冷加载仍会下载 49 KB woff2）。彻底解决要自托管字体（新增资源与许可处理），未做。
   “字体域名完全不可达”这一场景**未成功测量**：尝试用 Chromium `--host-resolver-rules` 把字体域名指向一个只 accept 不响应的
   本地端口，实测该参数在 Playwright 1.63 + chromium-1243 下未生效（0 个连接到达桩服务器），因此该结论只有推理、没有数据。
5. `warm-fast` 列表 +16 ms（样本 73–112 与 82–92），判为噪声级，未采信为退化。
6. 仍为 HTTP/1.1（uvicorn），连接数是请求数敏感的前提；若将来上 HTTP/2，本轮“合并小 chunk”的收益会缩小。
7. `dist` 仍产出 9 MB sourcemap（非关键路径）——不在本轮范围。

## 11. 清理

- 每个测量实例由 `measure.py` 启停；`OwnedServer.__exit__` 在端口仍被监听时会抛错，本轮所有运行均正常退出。
- Chromium 由 Playwright 启停，context/browser 在 `finally` 中关闭。
- 临时 data root 在 `finally` 中 `shutil.rmtree`；仓库内只保留 `audit/session-history-load-perf/` 下的证据 JSON 与脚本。
- 未触碰 8767/8768、未操作真实服务/真实数据、未动 main/practical、未 push。
