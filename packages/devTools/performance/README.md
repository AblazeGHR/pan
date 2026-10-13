# 前端性能验证入口

## Session 与汇报派生计算

```powershell
node packages/devTools/performance/benchmark-derived-ui.mjs
node packages/devTools/performance/benchmark-derived-ui.mjs --browser
node packages/devTools/performance/benchmark-derived-ui.mjs --browser --ref add07f49
```

脚本只构造合成数据，报告 15 次采样的中位数和 P95；`--browser` 使用临时 headless Chromium，`--ref` 从 Git 只读加载指定版本的两个算法。它不访问 Pan 服务、不改 Session 或 Git 状态。需要 `packages/web` 已安装 TypeScript 和 Playwright，以及可用的 Chromium。5000 个 Session 和超过 16 个终态的场景用于规模压力分析；当前摘要终态列表上限为 16，不能把压力场景节省量直接当作普通用户加载耗时的节省量。

这些工具用于可复现的隔离验证，不连接现有 Pan 服务，不操作 8767/8768，不使用真实 Session 数据。它们不执行 Git 集成、安装依赖或构建；浏览器场景运行前，请在目标 checkout 的 `packages/web` 中执行 `pnpm build`。

## 浏览器与草稿场景

在仓库根目录运行：

```powershell
./packages/devTools/performance/Run-Frontend-Performance.ps1 -Scenario OlderPages -Samples 3
./packages/devTools/performance/Run-Frontend-Performance.ps1 -Scenario Runtime
./packages/devTools/performance/Run-Frontend-Performance.ps1 -Scenario Draft
./packages/devTools/performance/Run-Frontend-Performance.ps1 -Scenario Navigation
./packages/devTools/performance/Run-Frontend-Performance.ps1 -Scenario Scrollbar
./packages/devTools/performance/Run-Frontend-Performance.ps1 -Scenario NetworkList -Samples 3 -PythonExecutable 'D:/project/Pan/.venv/Scripts/python.exe'
```

需要目标 checkout 的 Node、前端依赖和 Playwright Chromium。`Draft` 使用真实 store 源码、虚拟时钟和模拟 transport，不需要浏览器或构建。其名称沿用原脚本，不能将此场景视为真实 HTTP/浏览器端到端验证。

入口复用 `packages/web/e2e` 中的现有夹具，避免维护两套测试实现。结果保存到被 Git 忽略的 `packages/web/test-results/devtools-performance-<timestamp>/result.log`，浏览器场景可能另输出截图或证据目录。失败以非零退出状态报告。

- `OlderPages`：5,000 条合成消息、200 ms API 延迟、普通 CPU 和 4 倍 CPU 限速；测量向上加载耗时、逻辑锚点、预取堆占用，并验证前台抢占、Session 切换和 Worker 活动。临时静态服务使用操作系统分配的 loopback 端口。
- `Runtime`：流式更新、块内滚动、历史合并、缓存切换和受控冷加载；临时 Vite preview 使用 5184，端口被占用时失败，不终止已有进程。
- `Navigation` / `Scrollbar`：首次定位和滚动条行为；启动自己的 Python E2E 服务（8797），使用临时 runtime 和合成消息。端口被占用时失败；结束时按 checkout/端口身份清理自身服务。需要 Pan Python 依赖，可传 `-PythonExecutable <python.exe路径>`，默认使用当前仓库 `.venv` 或 PATH 上的 Python。
- `Draft`：草稿保存的请求量、限频、失败/竞态和本地处理开销。
- `NetworkList`：在自己的 5190 服务中生成 200 个合成 Session，使用真实 API、WebSocket 和生产前端。串行对比未优化响应与优化响应，覆盖本地刷新、80 ms / 1 Mbps 网络刷新、全新浏览器上下文冷加载。输出列表可见时间、每次摘要请求的起始时间、传输时间及压缩前后字节数。需要 Pan Python 依赖；不启动真实 CLI。`-Samples` 支持 1–10。

比较另一份构建时，`OlderPages` 和 `Runtime` 支持 `-Dist <dist目录>`。对尚未实现预取的旧版本运行旧页场景时加 `-Baseline`，仅适用于 `OlderPages`：

```powershell
./packages/devTools/performance/Run-Frontend-Performance.ps1 -Scenario OlderPages -Dist 'D:/path/to/baseline/packages/web/dist' -Baseline
```

比较时使用同机、同样本数、串行运行且保持后台负载一致。区分单次 smoke、重复对照和真实用户环境；这些夹具不能证明常用 Edge 或线上服务没有卡顿。

## 后端旧消息分页

```powershell
python ./packages/devTools/performance/benchmark-history-pages.py --rows 30000 --repeats 7
```

只在临时目录创建合成历史，对比兼容全量 JSON 解码路径与已知总数的分页路径，验证返回内容相同及文件哈希不变。输出 JSON 包含文件体积、样本和中位数。使用安装了 Pan Python 依赖的解释器；脚本自动定位所在仓库，不硬编码用户路径。重复读取会使 OS 文件缓存变热，不能将结果视为冷磁盘测量。
