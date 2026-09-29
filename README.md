# Pan

Pan 是一个用于管理 CLI Agent 会话并协调多 Agent 任务的本地 Web 应用。你可以从 Dashboard 管理 Sessions、工作区和 Worker；需要并行协作时，再让 MA（meta-agent）把任务派给其他 Sessions。

**[English](./README.en.md) · 中文**

## Pan 可以做什么

- **管理 Sessions**：在一个 Dashboard 中创建、导入、重命名、分支、置顶和删除会话；使用 cbc、kimi、opencode、claude 或 codex adapter。实际可用项取决于本机安装的 CLI。
- **整理工作区**：用 Workspace 分组 Sessions、切换列表范围，并为当前 Session 的 Editor 增加工作区共享目录。Workspace 是组织元数据，不会移动或复制磁盘目录；Session 的 `workdir` 仍是 Agent 工作目录。
- **协调多个 Agent**：SMA 模板提供 MA（主管）会话。MA 可以创建或认领子 Session、异步派发任务，并通过持久化报告队列收集完成或错误结果。
- **跟进 Jobs**：Dashboard 的 Jobs 页面可查看和管理定时任务、定时 Session 消息/群发和立即运行的后台进程。
- **管理运行状态**：分别控制 Session Worker 与 Pan 主服务；重新启动后，可对上次仍记录为运行中的 Session 做恢复选择。
- **按需接入通道**：每个 Session 可配置 MCP server；QQ OneBot 网关在 App Settings → Plugin 中管理。Cloudflare Remote 与 Memory 是可选能力，Remote 默认关闭。

Pan 不会自动为每个子任务创建 Git worktree。需要隔离并行代码改动时，为各 Session 指定不同的 workdir 或 Git worktree。

## 快速开始

### 前置条件

- Python 3.10 或更新版本。
- Node.js 20 或更新版本，以及 pnpm 9.7（用于构建 Dashboard）。
- 至少一个已安装并能从 Pan 进程的 PATH 找到的 CLI Agent：`cbc`、`kimi`、`opencode`、`claude` 或 `codex`。Pan 不会替你安装这些 CLI。

### 安装与启动

在仓库根目录运行。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r minimal-requirements.txt
Copy-Item config.example.json config.json
Set-Location packages/web
pnpm install
pnpm build
Set-Location ../..
python main.py
```

macOS/Linux 先激活虚拟环境，再执行相同的依赖与前端安装步骤：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r minimal-requirements.txt
cp config.example.json config.json
cd packages/web && pnpm install && pnpm build && cd ../..
python main.py
```

默认 Dashboard 地址为 <http://127.0.0.1:8768>。`config.json` 的 `port` 默认是 `8768`，环境变量 `PAN_PORT` 可覆盖它。服务默认只监听 `127.0.0.1`；API 默认没有身份验证。

`config.example.json` 中 `qq.enabled` 默认是 `true`，但这不会安装或登录 QQ 网关。若不使用 QQ，在启动前将 `config.json` 的 `qq.enabled` 设为 `false`。启用 QQ 时，还需为配置的 `qq.python` / `PAN_QQ_PYTHON` 安装 `packages/qq/requirements.txt`，并按[QQ 与 SnowLuma 使用说明](docs/QQ_PLUGIN_SNOWLUMA.md)选择和配置网关。

已完成安装的环境也可使用仓库中的入口脚本：Windows `scripts\start_pan.bat` / `scripts\stop.bat`，macOS/Linux `bash scripts/start.sh` / `bash scripts/stop.sh`。前台运行可用 `python main.py`，按 Ctrl+C 退出。

## 第一个 Session

打开 Dashboard，点击侧栏 **New** 快速创建；点击旁边的设置按钮可指定 Adapter、Session Template、模型和 workdir。选中 Session 后点击 **Start**，或直接发送第一条消息。忙碌时追加的消息会进入发送队列。**Import** 可浏览并导入支持的 CLI 历史会话。

要从单个 Agent 开始，使用普通 Session。要进行多 Session 编排，可在新建配置中选择 `SMA(NoAdapter)`（创建后选择 adapter）或 `SMA(cbc)`（固定使用 cbc）。详细操作见[中文用户手册](docs/USER_MANUAL.md)。

## 文档

- [中文用户手册](docs/USER_MANUAL.md)：安装、Dashboard、工作区、Reports、Jobs、生命周期和排障。
- [English user manual](docs/USER_MANUAL.en.md)
- [QQ 与 SnowLuma 使用说明](docs/QQ_PLUGIN_SNOWLUMA.md)
- [Pan 编排与 MCP 手册](docs/skills/pan/SKILL.md)：供配置 MA / 外部 Agent 时参考。

## 安全提示

不要把默认无鉴权的服务监听地址改为公网或局域网地址后直接暴露。Cloudflare Remote 需显式启用，并会提供远程访问通道；启用前请先评估访问范围与网关安全。
