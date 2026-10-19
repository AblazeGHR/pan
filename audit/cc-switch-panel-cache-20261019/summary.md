# CC Switch 面板缓存与自动额度查询（2026-10-19）

## 审计：原实现的实际阻塞

- 面板目录（`pluginPanelStore`）在 catalog 未加载时整页显示加载文案；catalog 加载有 `loading` 去重，不是主要瓶颈。
- 账号列表：`PluginPanelView` 在应用切换时 `setSnapshot(null)` + `setBusy(true)`，列表返回前整块内容被卸载，导致白屏等待；`list` 本身要串行调用 `provider list`（Codex 还有 `auth status`），是最慢的一步。
- 单一的 `busy` 同时禁用应用页签、刷新、额度与切换；任何一次 CLI 读取都会锁住全部交互。
- 请求没有 `AbortController`，卸载/切应用后的旧响应仍会回写；没有请求代次，旧响应可能覆盖新状态。
- 后端 bridge 每次调用持插件级 `asyncio.Lock` 和跨进程文件锁，并发额度查询会在服务端排队，因此前端必须限制并发。

## 本次实现

- `src/views/pluginPanelRuntime.ts`
  - 严格过期判定：`now - queriedAt > 300000`；`queriedAt` 未知时查询，未来时间（时钟偏差）按新鲜处理、不编造年龄。
  - 按 `plugin/panel/app` 隔离的内存快照缓存（仅渲染所需字段，不存凭据、不落盘）。
  - list/quota 进行中请求去重，覆盖 StrictMode 双执行、重复 mount、快速切回同一配置。
  - 共享快照代次与额度版本位于 runtime：跨卸载/重进共享的旧 list 不能覆盖已完成的新 switch；旧 quota 回包只把对应额度 patch 到最新共享快照。
  - 并发上限 2，单个失败不影响同批其他配置。
  - 快照合并保留“请求开始后才落地的额度”，避免旧列表回退新额度。
- `src/views/PluginPanelView.tsx`
  - 进入/切换应用时先渲染缓存快照，再后台刷新列表；仅对过期/未知额度自动查询。
  - 自动刷新不设置全局 `busy`；只有写操作仍锁应用页签，手动额度只锁自身按钮。
  - 显示“正在刷新 N 个过期额度… / 正在后台更新列表…”，首次无缓存保持正常 loading。
  - 自动查询失败保留旧额度并显示“可重试”，每次访问只自动尝试一次；手动额度可再次触发。
  - 应用切换使用独立状态与缓存；列表/写操作使用视图代次，额度使用代次标记，迟到的旧响应不会回退新切换或新额度。
  - 保留手动列表/额度刷新、`expectedCurrentId` 防重入与 `uncertain` 语义。

## 验证

- 单元测试（受控时钟）：`pnpm exec vitest run src/views/PluginPanelView.test.tsx --no-cache`
  - 24→26 passed；覆盖 299999/300000/300001ms 边界、未知/未来时间、缓存复入、StrictMode 去重、应用隔离、乱序响应、失败保留与手动重试、并发上限、自动查询期间切应用、不自动 switch、跨卸载旧 quota 回包与跨组件共享旧 list 覆盖新 switch。
- 全量前端测试（锁文件一致的完整验证树 `.validation/web`，依赖 junction 只读）：
  - `pnpm exec vitest run --no-cache` → 165 files / 1995 tests passed。
- 类型与构建（同一完整验证树）：
  - `pnpm exec tsc -b` → exit 0。
  - `pnpm build` → exit 0（含 72 个预压缩资源校验）。canonical 依赖 junction 缺少 `@xterm/*`，因此未写穿 canonical，也未安装包。
- Lint：改动文件 0 error（`PluginPanelView.tsx` 保留既有的 react-refresh warning）；仓库 `eslint .` 的 e2e Node 全局报错为 main 既有基线（同一文件在 `D:\project\Pan-main` 同样报 8 个）。
- 后端定向：`python scripts/run_tests_isolated.py -- python -m pytest tests/test_plugin_panels.py -q` → 17 passed。
- 真实 Chromium（`e2e/cc-switch-panel-cache.e2e.mjs`，全部 API 为隔离 mock，未调用真实 cc-switch）：
  - 首次进入仅查询过期 `p-b` 与未知 `p-c`，新鲜 `p-a` 不查询；StrictMode 下 list 仅 1 次。
  - 离开再进入：列表被延迟 450ms 时缓存内容已可见，且没有新增额度请求。
  - 切到 Claude 只查询 `c-a`；返回 Codex 立即显示缓存且不重复查询。
  - 手动额度总会查询；手动刷新列表不触发自动额度；手动切换携带 `expectedCurrentId` 与 requestId。
  - 1.2s 无轮询新增请求；390×844 无横向溢出。
  - 证据：`browser-result.json`、`first-load.png`、`reentry-cached.png`、`app-switch.png`、`mobile.png`。

## 未验证 / 边界

- 未使用真实 cc-switch CLI 或真实账号数据；Windows CLI 行为沿用后端既有测试夹具。
- 未触碰 8767/8768、未重启服务、未 push/合入 main。
- 真实浏览器仅验证 SPA 内重进缓存；整页 reload 会丢失内存缓存（按设计不引入持久账号存储）。
