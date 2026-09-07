# 共享依赖准备与本地验证脚本

> 目标：让多个 worktree **一处安装、处处复用验证依赖**，而不是给每个 worktree 各下载一份。
> 配套脚本：`tools/prepare_validation_env.ps1`（含 `tools/prepare_validation_env.tests.ps1`）。

## 基线事实（已只读核实）

- 集成分支 `integration/pan-exit-options-20260907` 当前 HEAD = `c617e49`（merge: integrate frontend lint fixes）。
- **canonical Python 解释器**：`D:\project\Pan\.venv\Scripts\python.exe`（Python 3.14.5）。
  - 已验证导入：dotenv、mcp、pytest、fastapi、httpx、pydantic 全部 OK。
  - **严禁**把 `D:\project\Pan-main\.venv` 当作完整 Pan Python 环境。
- **canonical 前端依赖**：`D:\project\Pan-main\packages\web\node_modules`（Vitest / tsc / eslint 均可用）。
  - 主仓库 `D:\project\Pan\packages\web\node_modules` 本身已是**指向该路径的 JUNCTION**，本脚本复刻这一模式。
- **浏览器运行时当前不存在**：`playwright` 未安装、无 `ms-playwright` 缓存。**不要**把“浏览器未安装”伪装成已解决；E2E 边界见下文。

## 脚本做了什么

对指定 worktree 的 `packages/web/node_modules` 建立 **junction**（目录联接），目标固定为 canonical 前端 `node_modules`。

- `-Worktree <path>`：必填，worktree 根目录。
- 目标不存在 / 不是目录 → **fail-closed**（直接报错退出，绝不静默跳过）。
- 已存在正确 junction → **复用**（幂等）。
- 已存在错误目标的 junction 或真实目录 → **报错退出，绝不删除**（需人工处理后重试）。
- 校验 canonical Python 并以**报告**方式验证依赖导入；**不在任何 worktree 内创建 `.venv`**。
- 提供 `-DryRun` / `-Check` 检查模式；**默认绝不递归删除任何目录**。
- `-Check` 是严格只读模式；即使同时传入 `-FixPython` 或 `-InstallPlaywright`，也不会安装、联网或写入。

## 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `-Worktree` | （必填） | worktree 根目录 |
| `-CanonicalNodeModules` | `D:\project\Pan-main\packages\web\node_modules` | 共享前端依赖目录 |
| `-CanonicalPython` | `D:\project\Pan\.venv\Scripts\python.exe` | 共享 Python 解释器 |
| `-PythonModules` | dotenv, mcp, pytest, fastapi, httpx, pydantic | 需验证导入的模块 |
| `-DryRun` | 关 | 只打印将要执行的操作，不改动文件系统 |
| `-Check` | 关 | 只读审计：不建 junction、不修 Python |
| `-FixPython` | 关 | **显式 opt-in**；仅作用于 canonical `.venv`，记录安装前后 `pip freeze` 证据 |
| `-FixPackages` | 空 | 与 `-FixPython` 配合，指定要装的包；**默认只检查不安装** |
| `-PlaywrightCache` | 空 | 报告/建议共享浏览器缓存路径（`PLAYWRIGHT_BROWSERS_PATH`） |
| `-InstallPlaywright` | 关 | **显式 opt-in**；会触发联网下载并占用本机缓存，**不**声称 E2E 通过 |
| `-Undo` | 关 | 仅移除本脚本创建且带 ownership marker 的 worktree junction（不删 canonical）；陌生 junction、symlink、真实目录均拒绝 |
| `-LogPath` | 空 | 追加可读报告 |

## 常用命令

```powershell
# 1) 准备（安全、幂等；只在 worktree 内建一个 junction）
powershell -NoProfile -ExecutionPolicy Bypass -File tools/prepare_validation_env.ps1 `
  -Worktree "D:\project\Pan\data\workdirs\my-ta"

# 2) 只读检查（不改任何东西）
powershell -NoProfile -ExecutionPolicy Bypass -File tools/prepare_validation_env.ps1 `
  -Worktree "D:\project\Pan\data\workdirs\my-ta" -Check

# 3) 仅撤销脚本创建的 junction
powershell -NoProfile -ExecutionPolicy Bypass -File tools/prepare_validation_env.ps1 `
  -Worktree "D:\project\Pan\data\workdirs\my-ta" -Undo
```

## 幂等 / 安全保证

- **重复执行幂等**：正确 junction 直接复用，退出码 0。
- **错误 junction fail-closed**：指向错误目标或真实目录时拒绝修改，退出码 2。
- **只动 worktree 内 junction**：创建后在 `packages/web/.pan-validation-node-modules.junction` 记录 ownership marker；`-Undo` 必须核对 marker、junction 类型和目标后才移除 `<Worktree>/packages/web/node_modules` 这个联接本身。canonical 依赖、真实目录、其它 worktree 一律不动。
- **绝不递归删除**：脚本唯一会删除的是单个 junction（联接点），不会 `rm -rf` 任何目录。

## 运行自带测试（PowerShell parser + 行为）

`tools/prepare_validation_env.tests.ps1` 在隔离临时 worktree（路径含空格）中验证：

- 脚本可编译（PowerShell parser 校验）；
- 含空格路径下创建 junction；
- 重复运行幂等复用；
- 错误目标 / 缺失目标 fail-closed；
- `-Check` / `-DryRun` 不创建 junction；
- 真实目录、symlink、非目录 canonical 目标均 fail-closed 且保留原物；
- `-Undo` 只移除 junction、canonical 完好；
- 重复 `-Undo` 安全返回；
- canonical Python 导入报告。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/prepare_validation_env.tests.ps1
```

## 关于浏览器的诚实说明（未覆盖边界）

- 当前 `playwright` 未安装、无浏览器缓存，因此 **browser E2E 无法运行**，脚本不会声称其通过。
- `-PlaywrightCache` 仅**报告**建议的共享缓存路径；`-InstallPlaywright` 是显式 opt-in，会**联网下载**并占用本机缓存，且下载后仍需真实浏览器测试才算 E2E 通过。
- `-Check` 会屏蔽 `-InstallPlaywright` 的联网/写入请求；脚本只报告 Python 包状态，不宣称浏览器二进制已安装，也不宣称 E2E 完成。
- 未覆盖：Playwright 浏览器下载、真实浏览器 E2E、跨卷 junction 的边界情况（如 worktree 与 canonical 不在同一卷时 junction 行为）。

## 撤销

只需对原 worktree 执行 `-Undo`，脚本仅移除其内的 `packages/web/node_modules` junction；canonical 依赖保持不动。无需手动清理。
