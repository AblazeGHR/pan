# Session 列表 / History 冷加载与弱网优化（TA 交付 · 第二轮：合并最新 main + 定点消融）

- 最新 main 基线：`f03e7c652c45e207486d2ee33643b95ae915985b`
- 合并提交（`--no-ff`，保留历史，未 rebase）：`dea32a43b573eaaba83cb91eb0e773a491f39475`
- 本轮候选（仅字体非阻塞）：见 §11「提交与一致性」的实际 SHA
- 分支 `feature/session-history-load-perf-20261024`，隔离树 `D:/project/pan-worktrees/session-history-load-perf-20261024`
- 未 push，未改动 main / practical，未重启或访问真实服务（8767/8768），未触碰其他授权/Job/CI 工作树

## 1. 结论（选定：仅“字体非阻塞”）

在相同数据集、相同弱网参数、相同浏览器缓存状态下，三次运行取中位数：

| 场景 | 指标 | 基线 | 仅字体 | 变化 |
|---|---|---:|---:|---:|
| 空缓存 · 快网 | 导航→列表可见 | 284.5 ms | **191.1 ms** | **-33%** |
| 空缓存 · 快网 | FCP | 188 ms | **88 ms** | **-53%** |
| 空缓存 · 快网 | 导航→首屏 history 可读 | 361 ms | **273 ms** | **-24%** |
| 空缓存 · 快网 | 导航请求 / 字节 | 41 / 552.4 KB | 42 / 554.3 KB | +1 / +0.3% |
| 空缓存 · 弱网 | 导航→列表可见 | 2739.6 ms | 2714.5 ms | -0.9% |
| 空缓存 · 弱网 | 点击→首屏 history 可读 | 988.0 ms | 985.5 ms | -0.3% |
| 空缓存 · 弱网 | 导航→首屏 history 可读 | 3751 ms | 3729 ms | -0.6% |
| 空缓存 · 弱网 | 导航请求 / 字节 | 35 / 538.5 KB | 36 / 540.2 KB | +1 / +0.3% |
| 空缓存 · 弱网 + 1.2 s 思考 | 列表 / 点击→history | 2719.0 / 225.4 ms | 2723.5 / 223.1 ms | +0.2% / -1.0% |
| 热缓存 · 弱网 | 列表 / 点击→history | 548.6 / 329.7 ms | 434.2 / 340.3 ms | 区间重叠，不采信 |
| 字体请求失败（route.abort） | 列表 / 点击→history | 177.2 / 59.8 ms | 191.3 / 56.2 ms | 区间重叠，不采信 |

**只交付字体改动**：它在快网保留了全部改善、在弱网与热缓存下都不引入 history 退化；
弱网列表的改善只能来自图标合并，而图标合并会带来“即点即看”history 退化（§4、§5），按 MA 的取舍不交付。

## 2. 合并最新 main

`git merge --no-ff --no-edit main`（main = `f03e7c65`，领先本分支 10 个提交）→ 合并提交 `dea32a43`，无冲突，
历史保留，未 rebase。新 main 带来 Authorization / Locker 相关视图与资产，构建产物已变化：

| 构建 | js/css 资源 | < 3 KB 的 js chunk |
|---|---:|---:|
| 合并后基线（v0） | 61 | 24 |
| 仅字体（v1，交付） | 61 | 24 |
| 字体 + icon 合并（v2，不交付） | 49 | 11 |

因此本轮所有数字都在合并后的树上重新测量，未沿用上一轮 chunk 大小或结论。

## 3. 测量方法

```bash
# 三个变体先各自构建，再把对应 dist 交给测量台（--dist 会复制进 packages/web/dist 并记录指纹）
E:/software/miniforge/python.exe audit/session-history-load-perf/measure.py \
  --label {baseline|candidate} --out audit/session-history-load-perf/{baseline|candidate}.json \
  --repeat 3 [--skip-server] [--dist <built-dist-dir>]
```

- 数据：临时 data root，真实 store 写入 31 个 Session / 76 000 行 / 13.7 MB（30×1 200 + 1×40 000），
  每个变体独立播种，不用真实用户数据。
- 实例：`tests/support/isolated_http_server.py`，随机 loopback 端口，代码拒绝 8767/8768，fakeCLI。
- 浏览器：生产 Chromium（`ms-playwright/chromium-1243`），Playwright 1.63，headless，全新 context。
- 弱网：CDP `Network.emulateNetworkConditions`（150 ms RTT / 1.6 Mbit/s 下行 / 750 kbit/s 上行，HTTP/1.1）。
- 指标（页内 `performance.now()`，相对 navigationStart）：
  - **导航→列表可见**：首个 `[data-session-card-id]` 进入 DOM 的时刻。
  - **点击→首屏 history 可读**：卡片点击（页内打点）到渲染出该 Session 自己的、文本非空的历史行。
  - **导航→首屏 history 可读**：上面两者的绝对时间（同一时钟），即用户从打开页面到真正读到历史的耗时。
  - **请求数/字节**：CDP `loadingFinished.encodedDataLength`（压缩后线上字节）。
- 每场景 3 次，报告中位数，并给出逐次样本以判断显著性。每个变体记录 `dist` 指纹（index.html sha + 资源数）。
- 服务器先 `settle()` 跨过启动期一次性卡顿（§9），再开始测量。

## 4. 消融结果（同数据、同参数、同缓存状态，各 3 次中位数）

| 场景 | 指标 | v0 基线 | v1 仅字体 | v2 字体+icon |
|---|---|---:|---:|---:|
| 空缓存·快网 | 导航→列表 | 284.5 | **191.1** | **169.1** |
| 空缓存·快网 | FCP | 188 | **88** | **104** |
| 空缓存·快网 | 导航→history | 361 | **273** | **263** |
| 空缓存·弱网 | 导航→列表 | 2739.6 | 2714.5 | **2337.4** |
| 空缓存·弱网 | **点击→history** | 988.0 | **985.5** | **1318.3** |
| 空缓存·弱网 | 导航→history | 3751 | 3729 | 3679 |
| 空缓存·弱网 | 请求 / 字节 | 35 / 538.5 KB | 36 / 540.2 KB | 27 / 534.0 KB |
| 弱网+1.2s 思考 | 列表 / 点击→history | 2719.0 / 225.4 | 2723.5 / 223.1 | 2311.1 / 222.3 |
| 热缓存·弱网 | 列表 / 点击→history | 548.6 / 329.7 | 434.2 / 340.3 | 542.9 / 226.3 |
| 字体请求失败 | 列表 / 导航→history | 177.2 / 269 | 191.3 / 293 | 163.5 / 255 |

三个臂各自由独立构建产物服务（`dist` 指纹：v0 `7b869bb8…`/61 资源、v1 `a7411e95…`/61、v2 `b3865fd5…`/49），
播种、弱网参数、浏览器缓存状态完全相同；每格为 3 次中位数。

读数要点：

1. **仅字体（v1）**：快网三项全部改善（列表 -33%、FCP -53%、导航→history -24%），
   弱网三项持平（±1%），没有任何指标退化。
2. **字体+icon（v2）**：弱网列表 -15%，但“点击→history”从 988 ms 涨到 1318 ms（+33%），
   三次样本完全分离；导航→history 只从 3751 降到 3679（-1.9%），因为列表提前出现，
   用户点击时 163 KB 的 markdown-vendor 还没下完。热缓存下 v2 的列表也未改善（549→543 ms）。
3. 因此按 MA 的取舍选定 v1。

### 4.1 交付轮的逐次样本（中位数所用的原始值）

| 场景 | 指标 | 基线样本 | 仅字体样本 |
|---|---|---|---|
| 空缓存·快网 | 导航→列表 | 360 / 284 / 250 | 1654\* / 191 / 142 |
| 空缓存·快网 | FCP | 280 / 188 / 188 | 192 / 88 / 84 |
| 空缓存·快网 | 导航→history | 1750\* / 361 / 341 | 1737\* / 273 / 228 |
| 空缓存·弱网 | 导航→列表 | 2746 / 2706 / 2740 | 2706 / 2749 / 2714 |
| 空缓存·弱网 | 点击→history | 961 / 1028 / 988 | 962 / 986 / 991 |
| 空缓存·弱网 | 导航→history | 3751 / 3761 / 3751 | 3702 / 3754 / 3729 |
| 弱网+1.2s | 导航→列表 | 2735 / 2703 / 2719 | 2736 / 2724 / 2714 |
| 弱网+1.2s | 点击→history | 227 / 225 / 225 | 234 / 220 / 223 |
| 字体失败 | 导航→列表 | 177 / 214 / 138 | 165 / 200 / 191 |

\* 该样本撞上 §9 的启动期一次性卡顿；每臂各一次，中位数不受影响。

## 5. 为什么这是“最小”的改动

- 只改一行 CSS 引入方式（`index.css` 删除 `@import url(https://fonts.googleapis.com/…)`）
  和 `index.html`（preconnect + `media="print" onload` 非阻塞 + `<noscript>` 兜底），
  字体族、字重、`display=swap`、视觉都不变。
- 服务端读路径、响应字段、缓存、渲染组件、WS 协议、history 合并逻辑**一行未改**。
- 没有新增预取、没有新增存储/依赖、没有改变首条消息的渲染体验。

## 6. 字体不可达（确定性请求失败模拟）

```js
await context.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort('failed'));
```

**这是“请求立即失败”的模拟，不是 DNS 黑洞/丢 SYN 的超时模拟**，不能用来推断被墙网络的真实等待时长。
该场景（`cold-fontfail`，单独 3 次：`fontfail-v0.json` / `fontfail-v1.json`）结果：

- 请求确实失败：`https://fonts.googleapis.com/css2: net::ERR_FAILED`。
- UI 仍可用：31 张 Session 卡片全部渲染（`cards: 31`），点击后 history 可读（两臂 60–70 ms）。
- fallback 生效：`document.fonts.size === 0`（没有任何 @font-face 注册，说明 webfont 未加载），
  `bodyFontFamily = Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`，
  文本由后续系统字体族渲染。注：`document.fonts.check()` 对“完全没有注册 face 的族”返回 true，不能用作证据。
- 时间上 v0 与 v1 无显著差异（176/194 vs 194/195 ms，各含一次启动卡顿离群样本）：因为 abort 是立即失败，
  阻塞的 `@import` 只多付一次失败往返。v1 的价值是**从关键路径上移除该依赖**，而不是缩短失败耗时。

## 7. 构建期校验（不锁域名、不锁 chunk 数）

`packages/web/e2e/verify-precompressed-assets.mjs` 在每次 `pnpm build` 与 CI 中执行，只断言两类真实风险：

1. **阻塞**：任何产物 CSS 不得含外部 `@import`；`index.html`（`<noscript>` 之外）里任何跨域 `<link rel=stylesheet>`
   必须是非阻塞写法（`media="print"` / `media="not all"` / `disabled`）。与具体域名无关。
2. 不再断言图标 chunk 数量：本轮不交付合并，且固定数量会变成未来不合理约束。

红色证据（把 v0 产物放进 dist 后运行）：`exit 1`，`index-t1BeNpq3.css contains a render-blocking external @import`。
绿色：最终树 `pnpm build` → `exit 0`，`Verified 92 compressed production assets`。

## 8. 正确性与负控

- **没有新增缓存**：不涉及 localStorage/IndexedDB/内存缓存，也没有新请求；弱网导航请求 35 → 35/36，
  字节 538.5 KB → 540.2 KB（+0.3%，字体样式表从 CSS 内联 import 变为 index.html link 的请求记账差异）。
- **服务端未改**：合并后重测 `GET /api/sessions?summary=1` 26.4 ms / 2 921 B(gzip)、
  `history?before=0&limit=50` 30.9 ms / 620 B、40 000 行会话首读 36.5 ms / 623 B，首读与重复读同一量级。
- **既有语义未触碰**：`historyEpoch/historyRevision/summaryRevision`、跨 Session 迟到响应、WS 增量、rewind、
  Queue 原 ID / unknown、附件授权代码均未改动；前端 store 与 history 合并逻辑未改。
- 定点回归：见 §10 的 pytest 全绿（含真实 HTTP/WS 冷加载 E2E）。
- `views/PluginPanelView.test.tsx` 的既有失败保留原样，本轮不修、不掩盖；其失败与本轮改动无关
  （把本轮改动 stash 后同样失败，上一轮已记录）。

## 9. 已知未修边界

1. **启动期一次性 ~1.3 s 卡顿**：全新进程首个请求后约 1.3–1.6 s 处，400 次轮询只出现 1 次 ~1.3 s，p95 40 ms，
   可复现；本轮按 MA 要求只作为独立边界保留，不追新架构。它会让“撞上的那一次”样本变慢，
   因此每臂各有 1 个离群样本（基线 1750 ms、候选 1737 ms 的导航→history），中位数与样本区间均已单列。
2. **弱网列表无改善（仅字体方案）**：2720 → 2715 ms。根因是启动字节（~540 KB）与 HTTP/1.1 连接占用，
   与字体无关；能改善它的图标合并在 v2 中实测 -14%，但代价是即点即看 history +36%，本轮不交付。
3. **字体仍是第三方依赖**（现为非阻塞）：冷加载仍会请求 49 KB woff2；自托管字体需要新增资源与许可处理，本轮未做。
4. **字体黑洞（丢包/超时）未测**：`--host-resolver-rules` 在本机 Playwright/Chromium 组合下未生效；
   route.abort 只覆盖“立即失败”。真实被墙网络的等待时长没有数据。
5. 热缓存场景（warm-fast / warm-weak）无确定变化，样本区间重叠。

## 10. 验证与退出码

| 检查 | 命令 | 结果 |
|---|---|---|
| 构建 + 类型 + 资源校验 | `cd packages/web && pnpm build` | exit 0，`Verified 92 compressed production assets` |
| 阻塞断言红色证据 | 把 v0 产物放入 dist 后 `node e2e/verify-precompressed-assets.mjs` | exit 1，`contains a render-blocking external @import` |
| 服务端历史/列表/静态/前端单测 | `python scripts/run_tests_isolated.py -- python -m pytest tests/test_eventloop_history_offload.py tests/test_history_tail_chunked.py tests/test_react_static_assets.py tests/test_web_frontend.py tests/test_real_history_coldload_e2e.py -q` | exit 0，76 passed |
| 测量（基线 / 候选 / 三臂消融） | `measure.py --repeat 3`（见 §3） | 全部 exit 0，证据见 `baseline.json`、`candidate.json`、`ablation-v{0,1,2}.json` |
| 字体请求失败场景（单独复跑） | `measure.py --repeat 3 --only=cold-fontfail --dist …` | exit 0，`fontfail-v0.json`、`fontfail-v1.json`（`fontFaces: 0`） |

## 11. 提交与一致性

- 交付树只包含：`packages/web/src/index.css`、`packages/web/src/index.html`、
  `packages/web/e2e/verify-precompressed-assets.mjs`、测量工具（`packages/web/e2e/session-load-perf.mjs`、
  `audit/session-history-load-perf/*`）。`vite.config.ts` 已还原为 main 内容（不交付 icon 合并）。
- **测量产物 = 交付源码**：交付树 `pnpm build` 生成的 `dist` 与测量所用的 `v1` 目录逐文件 sha256 完全一致
  （`diff` 空，exit 0）。
- 未 push、未动 main/practical、未重启真实服务；8767/8768 未被访问（启动器与测量台双层拒绝）。

## 12. 清理

- 每个测量实例由 `measure.py` 启停，端口仍被监听时会抛错，本轮所有运行正常退出。
- Chromium 由 Playwright 启停并在 `finally` 关闭；临时 data root 用后删除。
- 仓库内只保留证据 JSON 与脚本；变体产物暂存目录在提交前删除。
