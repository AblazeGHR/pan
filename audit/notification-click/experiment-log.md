# 通知点击深链实验日志（research/notification-click-session-20261011）

- 日期：2026-10-11（UTC 时间戳见各产物）
- worktree：`D:/project/pan-worktrees/notification-click-session-20261011`
- base：`b80b272c`；分支：`research/notification-click-session-20261011`
- 约束：未合并 main/practical、未 push、未部署、未重启真实服务、未改用户配置、未发真实 QQ 通知、未弹任何系统通知（原生路线仅模拟）

## 环境准备

1. 依赖哈希比对（worktree vs `D:/project/Pan`(practical) vs `D:/project/Pan-main`(main)）：
   - `packages/web/package.json` sha256 三方一致 `c7199021…`
   - `packages/web/pnpm-lock.yaml` sha256 三方一致 `249d2fc3…`
   - `packages/web/vite.config.ts` sha256 三方一致 `b819756b…`
   - 记录：`dependency-hashes.txt`
2. `tools/prepare_validation_env.ps1`（脚本自带安全模型：fail-closed / 幂等 / 不递归删除 / 不装依赖）：
   - 首次指向默认 canonical（Pan-main）发现其 node_modules 陈旧：缺 `@xterm/*`（tsc 报 TS2307，vitest 不受影响）。
   - `-Undo` 后重跑并显式 `-CanonicalNodeModules "D:\project\Pan\packages\web\node_modules"`（practical 的完整依赖，含 @xterm；与 worktree lock 哈希一致）。**只读复用，未执行任何 install**。
   - 日志：`validation-env-output.txt`（`*.log` 被仓库 .gitignore 忽略，故以 .txt 留存）；脚本 canonical Python 模块检查全 OK（Pan-main venv）。
3. 未提交本地标记 `packages/web/.pan-validation-node-modules.junction`（脚本 ownership marker，留在磁盘、不入 git）。

## 构建与静态检查

- `./node_modules/.bin/tsc -b` → 干净通过（切换到 Pan 依赖后）。
- `./node_modules/.bin/vite build` + `node e2e/verify-precompressed-assets.mjs` → `BUILD_OK`，66 个压缩产物校验通过。

## 测试

| 轮次 | 命令 | 结果 |
|---|---|---|
| 定向单测 | `vitest run src/utils/session-deeplink.test.tsx src/hooks/useWebSocket.test.tsx` | 95/95 通过 |
| 全量（改动后） | `vitest run` | 134 文件通过 / 2 文件失败；用例 1442 过 / 2 失（日志 `vitest-full-output.txt`，VITEST_EXIT=1） |
| 全量（基线 b80b272c 临时 worktree，同环境同依赖） | `vitest run` | 133 文件通过 / 2 文件失败；用例 1436 过 / 2 失（日志 `vitest-baseline-full-output.txt`） |

失败对照：两轮失败的**同名同因**——`App.test.tsx`（responsive WorkspaceRail / deferred detail panel 用例，全量并行下的 flake；单独运行 3/3 通过）与 `components/editor/EditorPane.test.tsx`（Monaco 预览 heading，环境相关）。基线复现 → **预存在，与本次改动无关**。改动后多出的 6 个用例（4 hook + 2 websocket 点击）全部通过。

## 隔离浏览器 E2E（真实 Chromium）

- 脚本：`packages/web/e2e/notification-deeplink.mock.mjs`
- 方式：`vite preview`（**生产构建**、真实 basename `/react/`）+ 动态端口（本轮 `127.0.0.1:61750`）+ `?mock=1`（patch fetch、跳过 WS，不触真实后端）+ headless Chromium `153.0.8010.12`
- 结果（`e2e-notification-deeplink.json`）：6/6 通过
  1. 对照：无参数不自动选中任何会话
  2. `?session=mock-alpha` 命中并选中、参数被消费、无降级 toast、历史消息渲染
  3. 未知会话 → toast 降级、不选中、参数被消费
  4. 非法引用 → "会话链接无效" toast、不选中
  5. 双 tab 各自解析各自 `?session=`，互不串台
  6. 全程 0 pageerror、0 真实后端（8768/8767）请求

## Windows 原生路线（安全模拟，无 UI）

- 原型：`notifyicon-click-prototype.ps1`（`-Mode simulate` 不建 NotifyIcon、不弹 balloon、不开浏览器；`-Mode live` 保留供评审，本次未执行）
- 运行：
  - loopback origin：`SIMULATE_OK url=http://127.0.0.1:8768/react/?session=ses_0123456789abcdef`（`native-simulate-result.txt`）
  - tunnel origin：`SIMULATE_OK url=https://pan.example.ts.net/react/?session=ses_0123456789abcdef`（`native-simulate-result-tunnel.txt`）
  - 非法 sessionId（`bad id!`）→ 拒绝，exit 1（allowlist 生效）
- 明确声明：这不是真机点击验证。balloon/toast 的实际点击行为、焦点、系统通知中心留存均未真机验证（按约束不打扰用户），仅有 MS Learn 源码级依据 + 本源码证明现状无回调链路。

## 产物清单

- `dependency-hashes.txt`、`validation-env-output.txt`、`vitest-full-output.txt`、`vitest-baseline-full-output.txt`、`e2e-notification-deeplink.json`、`native-simulate-result*.txt`
- 原型源码变更见研究分支提交；`notifyicon-click-prototype.ps1`
