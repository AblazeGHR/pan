# Pan 启动器与 Windows 生命周期架构

状态：设计提案，待分阶段实现

日期：2026-09-06

## 1. 结论

Pan 不应继续让 `start_pan.bat` 承担完整的启动业务，也不应为了启动速度立即打包成单文件 `.exe`。

推荐采用三层结构：

```text
Python launcher / lifecycle supervisor  业务单一事实源
        ↓
BAT / PowerShell                      Windows 兼容与脱离进程树辅助层
        ↓
main.py                               Pan Core / Uvicorn 服务本体
```

具体方向：

- 新增或扩展 `packages/core/launcher.py`，承载一次启动、停止和附属进程编排业务。
- 保留 `packages/core/main_lifecycle.py`，负责 restart / exit 的持久任务状态、监督和跨进程生命周期编排。
- `main.py` 只负责 Pan Core 服务本身，不成为完整的 Windows 服务管理器。
- `start_pan.bat` 暂时保留为兼容入口，逐步缩减为调用本目录 `.venv` 的薄封装。
- PowerShell 或 `cmd.exe /c start` 只在 Windows 需要脱离当前 Pan 进程树时使用。
- 暂不把 Pan Core 打包成 `.exe`；exe 作为未来分发形态单独评估，不作为当前启动性能方案。

## 2. 背景与当前问题

当前 Windows 启动链包含多个层次：

```text
start_pan.bat
    ├─ 检查重复进程
    ├─ 读取配置和端口
    ├─ 启动 start_main.ps1
    ├─ 等待 /api/sessions?summary=1
    ├─ 启动 cloudflared
    └─ 记录 PID

main.py
    ├─ 初始化日志和 CLI preflight
    ├─ 启动 QQ bot 子进程
    └─ 启动 Uvicorn / FastAPI

main_lifecycle.py
    └─ 通过 stop_pan.bat / start_pan.bat 执行 restart
```

这种结构存在以下问题：

1. 启动业务分散在 BAT、PowerShell 和 Python 三处。
2. CMD 变量展开、括号和退出码规则容易造成 Windows 特有错误。
3. 重启由 Python 发起，但实际又绕回 BAT，职责边界不清晰。
4. BAT 不容易做单元测试；静态断言不能完全证明真实进程树行为。
5. 外部组件 QQ、Cloudflare Tunnel、Python CLI 和 Node CLI 仍然存在，单独生成 Pan exe 并不能消除这些运行时依赖。

此前启动脚本中的全仓库 `__pycache__` / `.pyc` 清理已经取消。readiness 检查也应保持在一个 PowerShell probe 进程内循环，避免每秒创建新的 PowerShell。

## 3. 目标与非目标

### 3.1 目标

- Pan Core 启动、停止、重启的业务逻辑由 Python 统一管理。
- 所有 Python 启动都显式使用当前 checkout 的 `.venv\\Scripts\\python.exe`。
- readiness 以真实 HTTP 成功为准，而不是 PID、端口或控制台文本。
- 保留 Windows 双击启动和旧快捷方式兼容性。
- 保留 Windows 进程树隔离和安全停止能力。
- 让启动和生命周期逻辑可被 Python 单元测试、临时端口 E2E 测试和真实 Windows E2E 分层验证。
- QQ 和 Cloudflare Tunnel 的进程归属清晰，不重复启动、不误杀其他实例。

### 3.2 非目标

- 不把 Pan 自己实现成通用 Windows 服务管理器。
- 不在 `main.py` 中实现“杀死自己后再启动自己”的不可靠流程。
- 不因为启动优化立即引入 PyInstaller、Nuitka 或其他 exe 打包链。
- 不改变 Session、Worker、MCP、queue_pending 的持久化和编排语义。
- 不让 launcher 重复接管 QQ bot；QQ 的生命周期必须保持单一所有者。

## 4. 目标模块职责

### 4.1 `main.py`：Core 服务本体

`main.py` 负责：

- 导入并创建 FastAPI 应用；
- 读取 Core 必需的 host / port 配置；
- 初始化 Pan Core 日志；
- 启动 Uvicorn；
- 在约定的位置触发 QQ bot 生命周期，或调用专门的 QQ 管理接口；
- 在 Core 退出时完成自身资源收尾。

`main.py` 不负责：

- 查找并停止其他 Pan 实例；
- 启动替代自己的新进程；
- 实现 restart supervisor；
- 解析复杂的 Windows launcher 参数；
- 决定 BAT/PowerShell 如何脱离当前进程树。

### 4.2 `packages/core/launcher.py`：一次启动/停止业务

拟新增的 launcher 是启动业务的单一事实源。它应提供可测试的 Python 函数和 CLI 子命令，例如：

```text
python -m packages.core.launcher start
python -m packages.core.launcher stop
python -m packages.core.launcher status
```

推荐职责：

- 解析并校验项目根目录；
- 解析 `config.json`；
- 解析 `PAN_PORT` → `config.json.port` → `8768` 的端口优先级；
- 固定选择 `<root>\\.venv\\Scripts\\python.exe`；
- 必要时校验 `fastapi` / `uvicorn` 等 Core 依赖；
- 检查当前 checkout 是否已有属于自己的 Pan 进程；
- 启动 `main.py`；
- 保存并校验 PID、进程创建时间和命令行归属；
- 等待 `GET /api/sessions?summary=1` 返回 HTTP 200；
- 记录阶段耗时和失败原因；
- 按配置启动 Cloudflare Tunnel；
- 在不重复接管 QQ 所有权的前提下协调附属组件状态；
- 启动失败时只清理自己确认拥有的进程和临时文件。

launcher 不应把业务状态只存在内存中。restart / exit 任务的持久状态仍由 `background_jobs` 和 `main_lifecycle.py` 管理。

### 4.3 `packages/core/main_lifecycle.py`：持久生命周期监督

`main_lifecycle.py` 负责高级生命周期：

- 创建和推进 restart / exit durable job；
- 保存 `requested → stopping → stopped → starting → ready` 等阶段；
- 校验旧服务的 PID、创建时间、命令行和监听端口；
- 启动脱离当前 Pan 进程树的 supervisor；
- 调用 launcher 的 Python API 或模块命令；
- 等待并记录最终 ready / failed / timed_out 状态；
- 防止同一 checkout、端口和操作的重复生命周期任务。

它不应继续把 `start_pan.bat` 当作唯一的业务实现。迁移完成后，BAT 只作为兼容路径，Python supervisor 直接调用 launcher。

### 4.4 `start_pan.bat`：兼容入口

过渡阶段保留 BAT，避免旧快捷方式和用户习惯立即失效。

最终应缩减为类似：

```bat
@echo off
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m packages.core.launcher start
```

BAT 不再负责：

- 全仓库递归缓存清理；
- 复杂进程扫描；
- 多轮 readiness 逻辑；
- 配置重复解析；
- QQ / Tunnel 业务判断；
- restart / exit 状态机。

如果 Windows 双击场景需要隐藏窗口，隐藏行为应由一个明确的 launcher 参数或极薄的 PowerShell 跳板实现，而不是重新把业务塞回 BAT。

### 4.5 `start_main.ps1` / `start_pan_probe.ps1`：Windows 辅助层

PowerShell 可以保留以下低层能力：

- `Start-Process` 的窗口和标准输出重定向；
- `Get-CimInstance` / PID 进程身份探测；
- 单个持续运行的 HTTP readiness probe；
- Windows 进程树安全检查。

复杂的启动决策和错误处理应迁移到 Python。`start_pan_probe.ps1` 中的 `WaitReady` 可以继续作为轻量 Windows 探测器使用。

## 5. 启动与重启流程

### 5.1 前台开发启动

开发者可以直接使用：

```powershell
& 'D:\project\Pan\.venv\Scripts\python.exe' main.py
```

此模式保留可见控制台和实时日志，适合调试，不承担自重启职责。

### 5.2 Windows 兼容启动

```text
用户双击 start_pan.bat
        ↓
薄 BAT 入口
        ↓
本目录 .venv\\Scripts\\python.exe -m packages.core.launcher start
        ↓
launcher 读取配置、启动 main.py
        ↓
launcher 等待 /api/sessions?summary=1 = 200
        ↓
launcher 记录 ready，并按配置启动 Tunnel
```

### 5.3 App Settings 重启

```text
POST /api/main/restart
        ↓
Pan API 创建 durable lifecycle job
        ↓
先启动脱离当前 Pan 进程树的 supervisor
        ↓
supervisor 使用本目录 .venv 启动 main_lifecycle / launcher
        ↓
验证旧服务身份并停止旧进程树
        ↓
启动新的 main.py
        ↓
等待 HTTP readiness
        ↓
验证 listener、进程身份和 /api/health
        ↓
持久化 ready 或失败状态
```

不能在 HTTP 请求处理函数中直接停止当前服务后再期待该请求继续返回成功。脱离进程树的 supervisor 必须先建立，再停止旧服务。

## 6. readiness 契约

readiness 的唯一成功条件是：

```text
GET http://127.0.0.1:<port>/api/sessions?summary=1
返回 HTTP 200
```

以下信号单独都不能代表 ready：

- PID 文件存在；
- Python 进程存在；
- 端口已经监听；
- 控制台输出了 started；
- `Start-Process` 返回了进程对象。

原因是 Python 进程可能仍在导入模块、FastAPI lifespan 尚未完成，或服务刚监听但还不能处理业务请求。

readiness probe 应满足：

- 只启动一个 probe 进程；
- 在进程内部循环请求；
- 设置明确的总超时；
- 请求间隔可从 250ms 开始，必要时采用有限退避；
- 超时必须返回非零退出码；
- 输出可选地写入阶段日志，但不能用输出文本替代 HTTP 判断。

## 7. `.venv` 与依赖契约

### 7.1 Core 依赖

Pan Core 使用：

```text
<root>\\.venv\\Scripts\\python.exe
```

核心依赖清单为 `minimal-requirements.txt`，当前包含：

```text
fastapi
uvicorn
websockets
psutil
mcp==1.28.1
pytest
```

其中 `pytest` 是开发/测试依赖，不是生产运行依赖。

### 7.2 可选依赖

以下依赖属于 memory 或可选能力，不应在每次 Pan 启动时安装：

```text
sentence-transformers
watchdog
openai
llama-cpp-python
jieba
numpy
tiktoken
```

依赖安装只应通过 `scripts/setup.bat` 或用户主动执行的安装命令完成，不能放入 `start_pan.bat` / launcher 的正常启动路径。

### 7.3 QQ 依赖

QQ 当前使用独立解释器和依赖清单：

```text
packages/qq/requirements.txt
```

QQ 的 `nonebot2` 依赖不应强行并入 Pan Core minimal 环境。launcher 可以协调 QQ 状态，但必须保持 QQ bot 的单一生命周期所有者，不能与 `main.py` 重复 spawn。

## 8. 为什么暂不生成 exe

exe 解决的主要是分发体验，不是当前启动慢问题。

### 8.1 外部依赖仍然存在

即使 Pan Core 变成 exe，运行时仍可能需要：

- cbc / kimi / opencode / claude / codex CLI；
- Node.js；
- QQ 的 NoneBot 独立环境；
- cloudflared；
- `config.json`；
- 根 manifest 和外部 plugin manifest；
- `data` 目录；
- 前端 dist 资源。

因此 Pan 不是只打包一个 Python 文件就能变成完整单文件应用。

### 8.2 动态加载和 MCP 路径

Pan 启动时会注册多个 adapter，manifest 还使用 `${PAN_PYTHON}`、`${PLUGIN_DIR}` 等运行时替换。打包后需要重新定义：

- `sys.executable` 是否代表 exe 还是 Python 解释器；
- MCP stdio server 使用哪个解释器；
- 外部插件路径如何解析；
- manifest 和前端资源如何随 exe 分发；
- 用户数据与升级版本如何分离。

### 8.3 单文件 exe 可能增加启动成本

PyInstaller `onefile` 通常需要启动时解压资源，未必比现有 `.venv` 直接启动更快。`onedir` 虽然可以避免部分解压开销，但本质上仍是一组打包文件，不能消除外部 CLI、QQ 和 Tunnel 依赖。

因此 exe 应作为后续产品分发项目评估，而不是当前性能优化方案。

## 9. 分阶段迁移方案

### 阶段一：当前已完成/进行中

- 删除启动前全仓库缓存清理；
- readiness 改为单个持续 probe；
- 依赖安装使用本目录 `.venv` 和 `minimal-requirements.txt`；
- 保留 `start_pan.bat` 作为现有入口。

### 阶段二：建立 Python launcher

- 新增 `packages/core/launcher.py`；
- 把端口解析、`.venv` 解析、PID 检查、main 启动和 readiness 迁移进去；
- 为 launcher 增加 `start` / `status` / `stop` 命令；
- 启动阶段写入结构化耗时日志；
- 增加临时目录和临时端口的 Python 测试。

### 阶段三：切换 main lifecycle 到 launcher

- `main_lifecycle.py` 直接调用 launcher API 或模块命令；
- 不再把 `start_pan.bat` 当作 restart 的核心实现；
- 保留独立 supervisor 和 Windows 进程树隔离；
- 对 restart / exit 做真实 Windows 进程树验证。

### 阶段四：缩减兼容脚本

- 将 `start_pan.bat` 缩减为薄入口；
- 保留必要的 PowerShell 低层能力；
- 更新 README、快捷方式和启动诊断文档；
- 记录旧入口仍可用的兼容期限。

### 阶段五：独立评估 exe

只有在明确需要以下能力时才启动 exe 评估：

- 面向非开发用户发布；
- 不希望用户安装 Python；
- 需要安装器、签名、自动升级；
- 已经确定外部 CLI、QQ、Tunnel 和 MCP 的分发方案。

## 10. 验收标准

### 静态和单元验证

- `.bat` 不再包含全仓库递归缓存清理；
- 启动逻辑的端口优先级和 `.venv` 路径有测试；
- readiness 成功、超时和服务返回非 200 均有测试；
- launcher 失败时不会误杀非 Pan 进程；
- `git diff --check` 和 Python 编译检查通过。

### 隔离 Windows E2E

- 使用独立 checkout 或测试端口，优先 8767/8765；
- 使用 `D:\project\Pan\\.venv\\Scripts\\python.exe`；
- 启动脚本返回成功；
- `/api/sessions?summary=1` 返回 200；
- `/api/health` 返回 200；
- 监听端口的进程路径、命令行、创建时间属于目标 checkout；
- 服务保持监听至少 15 秒；
- 重复启动被拒绝；
- stop / restart 不影响 8768、QQ、Tunnel 或其他 worktree。

### 发布前验证

- 再决定是否删除 BAT 兼容入口；
- 确认所有用户快捷方式和文档已迁移；
- 确认 MCP 的 `PAN_PYTHON` 指向预期解释器；
- 确认 QQ 和 cloudflared 的生命周期没有重复所有者；
- 明确记录未验证的真实浏览器、Adapter 和外部服务 E2E。

## 11. 最终决策摘要

| 方案 | 当前决策 | 原因 |
|---|---|---|
| BAT 承担完整启动业务 | 否 | Windows 语法和多层脚本边界复杂，难测试 |
| 完全删除 BAT | 暂不 | 会破坏双击入口和既有快捷方式，收益不如迁移业务逻辑明确 |
| Python launcher 作为启动业务单一事实源 | 是 | 可测试、可复用、便于统一 `.venv`、PID、readiness 和错误处理 |
| `main.py` 直接管理自身重启 | 否 | 无法可靠处理自杀、进程树和 HTTP 请求生命周期 |
| 保留独立 supervisor | 是 | Windows 需要脱离当前 Pan 进程树后再停止/启动 |
| 立即生成单文件 exe | 否 | 不能消除外部 CLI/QQ/Tunnel/MCP 依赖，也未必更快 |
| 未来评估 onedir/安装器 | 可以 | 这是分发产品需求，不是当前启动性能修复 |

