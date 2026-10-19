# CC Switch CLI 安装指引（2026-10-19）

## 任务与环境

- 分支：`fix/cc-switch-cli-guidance-20261019`
- base：`e4078c186c7e89afc007fd28f24ec6973d2ef117`
- 实现提交：`742d7371933a2a9e60793d758a3392e6db70280e`
  `feat(cc-switch): guide missing or incompatible CLI`
- 工作树：`D:/project/pan-worktrees/cc-switch-cli-guidance-20261019`
- 未追 main、未合并、未 push/tag/release；未接触 8767/8768，未启动或修改生产数据与服务。

## 实现

### 1. 后端稳定错误码（最小改动）

`packages/cc_switch_bridge/provider.py`

- 新增 `CliError(ValueError)`，携带稳定 `code`。
- 仅以下情况产生“CLI 可用性”错误码：
  - `cli_not_found`：`settings.json.executable` / `PAN_CC_SWITCH_CLI` / `PATH` 均未找到入口；
  - `cli_unavailable`：入口存在但进程无法启动（`OSError`）；
  - `cli_incompatible`：`--version` 非 `pan-cc-switch-gui/v1` 契约、旧 CLI、或返回非兼容 JSON。
- 普通执行失败使用 `cli_failed`（退出码错误，仍不回传 stdout/stderr，避免凭据泄漏）；超时/预算仍返回原有“不确定”文字语义。
- `main()` 通过 `error_text()` 将 `CliError` 序列化为
  `{"ok":false,"error":{"code","message"}}`；其他 `ValueError` 与未知异常保持原有文本。
- 未改变 `switch` / `default_account` 的 `expectedCurrentId` 校验、CLI 返回后二次确认、文件锁、防重放与缓存写入。

### 2. Panel 结构化错误与安装指引

`packages/web/src/services/api.ts`

- 新增 `PluginPanelError`（保留 `code`）与 `pluginPanelFailure()`。
- `invokePluginPanel` 遇到 `isError` 时解析 host 的结构化 JSON，保留 code；纯文本错误仍按普通失败处理。
- 未改变请求体、`requestId`、写操作响应或缓存的行为。

`packages/web/src/views/PluginPanelView.tsx`

- 列表失败状态由纯字符串改为 `{ message, code? }`，继续按 app 隔离，`expectedCurrentId`、`uncertain`、快照/额度版本与缓存语义不变。
- 仅 `cli_not_found` / `cli_unavailable` / `cli_incompatible` 展示 `<CliGuidance>`：
  - 明确说明该面板依赖带兼容 CLI 的 cc-switch 分支，Pan 不捆绑、不自动下载或安装；
  - 指引按仓库说明安装/构建控制台入口，配置插件 `settings.json.executable`，或让 `cc-switch` 位于 Pan 进程 PATH；
  - 展示 `https://github.com/AblazeGHR/cc-switch-pan` 可点击链接，使用 `target="_blank" rel="noopener noreferrer"`。
- `cli_failed`、普通读取失败与额度/网络错误仍走原有错误提示，不会误判为缺少 CLI。
- 已有快照时提示“上次读取的数据已保留”；修复后点击“刷新列表”重试。

### 3. README 索引

- `README.md` / `README.en.md` 增加 CC Switch 外部 CLI 说明：
  - `cc-switch-bridge` 依赖带兼容 CLI 的 `AblazeGHR/cc-switch-pan` fork；
  - Pan 不捆绑、不自动下载或安装；
  - 给出可点击仓库入口及 PATH / `settings.json.executable` 配置方式。

### 4. 测试与真实浏览器

- `tests/test_plugin_panels.py`
  - 缺 CLI、不可启动、旧/不兼容 CLI、普通执行失败、成功路径的错误码与不泄漏凭据断言；
  - 结构化 `error_text` JSON code 断言；
  - README 中英文链接与“不捆绑/does not bundle”断言。
- `packages/web/src/services/api.test.ts`
  - 结构化 code 解析、纯文本无 code、`isError` 响应抛出具 code 的错误。
- `packages/web/src/views/PluginPanelView.test.tsx`
  - 缺 CLI 指引、链接 `href/target/rel`；
  - 不兼容与普通失败区分；
  - 成功列表和普通额度错误不展示安装指引。
- `packages/web/e2e/cc-switch-cli-guidance.e2e.mjs`
  - 真实 Chromium + 生产组件 + Vite fixture；
  - 全部 API 为拦截的 `isError` fixture，外部导航（含仓库链接）被 context route 拦截，未访问网络、未执行真实 CLI；
  - 点击链接后核对 popup 的目标 URL 为仓库地址；
  - 390×844 窄屏无横向溢出。

## 验证结果

| 检查 | 命令（工作目录） | 结果 |
|---|---|---|
| 后端 bridge / 插件回归 | `E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_plugin_panels.py tests/test_plugin_developer_help.py -q` | 21 passed |
| 定向前端测试 | `.validation/web`：`pnpm exec vitest run src/views/PluginPanelView.test.tsx src/services/api.test.ts --no-cache` | 2 files / 59 tests passed |
| 全量前端测试 | `.validation/web`：`pnpm exec vitest run --no-cache` | 169 files / 2053 tests passed |
| 类型检查 | `.validation/web`：`pnpm exec tsc -b` | exit 0 |
| 构建 | `.validation/web`：`pnpm build` | exit 0，72 个预压缩资源校验通过 |
| 改动 lint | `.validation/web`：`pnpm exec eslint src/views/PluginPanelView.tsx src/views/PluginPanelView.test.tsx src/services/api.ts src/services/api.test.ts` | 0 error；`PluginPanelView.tsx` 保留既有 react-refresh warning |
| 真实 Chromium | `.validation/web`：`node e2e/cc-switch-cli-guidance.e2e.mjs` | passed；`realCliExecuted:false` |

依赖环境：`.validation/web` 为 gitignored 的隔离验证树，`node_modules` 指向
`D:/project/pan-dependency-cache/249D2FC3A1A0FD51/node_modules/`，与
`packages/web/pnpm-lock.yaml` 一致且包含 `@xterm/*`。未安装包、未写穿
canonical junction。提交 hook 需要 canonical 树具备完整依赖时，仅在本次工作树临时
把 `packages/web/node_modules` junction 指向同一缓存，hook 通过后立即恢复为
`D:/project/Pan-main/packages/web/node_modules`；`Pan-main` 与其他 TA 的工作树未修改。

## 证据 hash

实现提交：`742d7371933a2a9e60793d758a3392e6db70280e`

| git blob | 文件 |
|---|---|
| `400d8d06f2153d1bdf730664e1a9bf518d47a016` | `audit/cc-switch-cli-guidance-20261019/browser-result.json` |
| `2e67d29d8c270b2eceed4f651e76bac5da9c3f7b` | `audit/cc-switch-cli-guidance-20261019/missing-cli.png` |
| `9210db7b5c0e3fc4e22efa5f12c289a71f75b1d3` | `audit/cc-switch-cli-guidance-20261019/missing-cli-mobile.png` |
| `55b34cdb721301300ff3ea38f3ad4689246b627f` | `packages/web/e2e/cc-switch-cli-guidance.e2e.mjs` |

## 边界与未验证

- 未使用真实 cc-switch CLI、账号、数据库或凭据；未执行真实换号，未下载或安装外部 CLI。
- 浏览器测试的外部页面只被拦截并核对 URL；未加载 GitHub 内容。
- 未覆盖“列表成功后才移除 CLI，随后写操作失败”的 UI 指引；当前指引由列表读取的 CLI 可用性错误触发，写操作仍保留原错误与 `uncertain` 语义。
- 未修改 `expectedCurrentId`、写操作幂等/防重、`uncertain` 阻塞、额度缓存与自动刷新逻辑。
- 未 push、未合并、未追 main、未创建 tag/release。
