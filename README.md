# Pan

> 中间层，让 Meta-Agent 调度管理多个 CLI Agent 进程，同时人类可随时观察、插话、接管任意进程。

**技术栈**：Python 3.14 + FastAPI + WebSocket

---

## 当前进度

| Phase | 目标 | 状态 |
|:-----|------|:----:|
| Phase 1 | Core 内部清洁化 + 稳定 API + 跨平台适配 | 完成 |
| Phase 2 | 逐个抽离通道模块（QQ / Web / Remote / Memory / SDK） | **Phase 2 进行中** — QQ/Web/Remote/MCP 已抽离；Memory 已落地（`packages/core/memory/`，feature/memory 已合入 main）；SDK 规划中 |

### 前端说明

Pan 支持三套前端共存：

- **React SPA**（当前主力）：`src/` 目录，Vite + React 19 + Tailwind CSS 4 + Zustand
- **Legacy TS 前端**：`ts/` → `static/` 编译而来，纯 TypeScript
- **前端模式**由 `config.json` 的 `frontend` 字段控制：
  - `coexist`（默认）：`/` 旧前端 + `/react/` React SPA
  - `react`：React SPA 接管 `/`
  - `legacy`：仅旧前端

### 项目架构 (Phase 2)

```
Pan/
├── main.py                    入口
├── config.json                配置文件（gitignored）
├── config.example.json        配置模板
├── manifest.json              内置 profiles / mcp_servers 声明
├── packages/
│   ├── core/                  Core 模块（进程管理 + 消息路由 + Memory）
│   │   ├── worker.py          Worker 生命周期管理（stream / one-shot MCP 双模式）
│   │   ├── session.py         Session 存储（JSON）
│   │   ├── config.py          配置加载
│   │   ├── character.py       Character 框架（profile → character → memory）
│   │   ├── manifest_loader.py 插件 manifest 加载器（${PLUGIN_DIR} 解析、合并 profiles/mcp_servers）
│   │   ├── memory_context.py  记忆上下文注入（search_and_format）
│   │   ├── memory/            Memory 子系统（SQLite + FTS5 + embedding 混合检索）
│   │   │   ├── __init__.py    MemoryManager 统一入口
│   │   │   ├── schema.sql / store.py / chunker.py / embedder.py / search.py / watcher.py / session_indexer.py
│   │   └── adapters/          CLI Adapter（cbc/kimi）
│   │       ├── base.py / registry.py
│   │       ├── cbc/           CBC Adapter（含 session 解析/分支）
│   │       └── kimi/          Kimi Adapter
│   ├── web/                  Web 通道（Dashboard + HTTP API）
│   │   ├── server.py          FastAPI 路由 + WebSocket
│   │   ├── index.html         Dashboard 桌面版（legacy）
│   │   ├── mobile.html        Dashboard 移动版（legacy）
│   │   ├── ts/                Legacy TypeScript 源码（编译为 static/）
│   │   ├── static/            Legacy 编译产物 + CSS
│   │   ├── src/               React + Vite 前端源码（当前主力）
│   │   ├── dist/              Vite 构建产物
│   │   ├── package.json
│   │   └── vite.config.ts
│   ├── qq/                   QQ Bot 通道（NoneBot2 桥接）
│   │   ├── plugin.py          QQ 消息处理（user/group scope、轮询结果）
│   │   ├── bot.py             QQ Bot 入口
│   │   └── requirements.txt
│   ├── remote/               远程访问通道（Cloudflare Tunnel）
│   │   ├── tunnel.py          cloudflared 进程管理
│   │   ├── api.py             状态 HTTP 服务
│   │   ├── main.py            独立入口
│   │   └── __main__.py        `python -m packages.remote` 入口
│   └── mcp/                  Meta-Agent MCP Server（session/worker 管理工具）
│       ├── server.py          标准 MCP 服务端
│       └── manifest.json      mcp 插件声明
├── scripts/                   启动/停止脚本（start_pan.bat / start_main.ps1 / start_cf.ps1 / pre-commit）
├── docs/                      文档（全部纳入 git 跟踪）
├── tests/                     测试（6 个文件，88 passed）
├── data/                      运行时数据（gitignored：sessions / characters / memory / workdirs）
└── requirements.txt
```

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动（默认端口见 config.json 的 port 字段；main 分支 8768，test 分支 8767）
python main.py
# → http://localhost:8767

# 运行测试
python -m pytest tests/ -q
```

## 远程访问（Remote 通道）

Pan 通过 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) 将本地服务暴露到公网。

前置条件：已安装 `cloudflared` 并添加到 PATH（`where cloudflared` 可找到）。

1. 在 `config.json` 中配置：

```json
{
  "remote": {
    "enabled": true,
    "provider": "cloudflare",
    "quick_tunnel": true,
    "config_path": "C:/Users/<you>/.cloudflared/config-test.yml",
    "binary_path": "cloudflared",
    "status_port": 8769
  }
}
```

2. 启动 Core 后，另起终端启动 Remote 通道：

```bash
python -m packages.remote
# 或直接运行脚本：scripts/start_cf.ps1
```

quick tunnel 会自动输出一个 `*.trycloudflare.com` 公网 URL；named tunnel 需设置 `quick_tunnel: false` 并指定 `config_path`。

3. 查看状态：

```bash
curl http://127.0.0.1:8769/status
```

> 公网域名与端口由 `config.json` 决定：tunnel 暴露的是 Pan 主端口（`remote.config_path` 指向的 yml 由 `scripts/start_cf.ps1` 注入 `ingress.hostname` + `config.port`）。`remote.status_port` 是本地状态服务端口。

## 架构

```
         Meta-Agent                   人类                    远程访问
    (CodeBuddy / MCP)           (Dashboard)            (Cloudflare Tunnel)
          │                          │                          │
   /ws/agent + MCP tools       /ws + HTTP              公网 URL + WS
    （事件流 + 命令）          （观察 + 注入 + 接管）      （Dashboard / QQ Bot 外部接入）
          │                          │                          │
          └──────────┬───────────────┘                          │
                     │                                          │
            ┌────────▼────────┐                                 │
            │  Pan Core         │◄──────────────────────────────┘
            │  (FastAPI 服务)    │        HTTP / WebSocket
            │                   │
            │  Session Manager │
            │  ├─ Worker-1     │── cbc / kimi（stream / one-shot MCP）
            │  ├─ Worker-2     │── ...
            │  └─ Worker-N     │
            │                   │
            │  Character 框架   │── profile → character → memory
            │  Memory 子系统    │── SQLite + FTS5 + embedding 检索
            │  Event Bus       │─── WS 广播
            │  Session Store   │─── JSON 持久化
            └──────────────────┘
```

## 关键设计

### Worker 与 Session 分离

- **Worker** — 运行时 CLI 子进程，持有 session_id 引用。kill 后 Worker 消失。
- **Session** — 持久化数据（UUID `ses_<16hex>`），独立于 Worker 生命周期。

### Character / Profile 框架

- **Profile** — manifest.json 声明的创建模板（adapter / model / mcp_servers / system_prompt / memory_dir）。
- **Character** — 由 profile 创建的机器人实例，持有独立记忆库（`data/characters/{id}/memory/` → `data/memory/{id}.sqlite`）。
- **Memory** — 每个 character 一个 SQLite 库，支持知识文件索引（`/api/memory/index`）、混合检索（向量 + FTS5）、运行中注入（`/api/memory/inject`）。

### CLI Adapter 协议

通过 `CliAdapter` 协议抽象 CLI 工具差异：
- `cbc` — CodeBuddy CLI（stream-json 协议 + one-shot MCP 模式）
- `kimi` — Kimi CLI（kimi-code，支持高速/付费模型）
- 未来：`claude-cli`、`gemini-cli` 等

### 模块通信

所有通道通过 Core 暴露的 HTTP/WS 通信，**不 import Core 内部实现**：

```
# Session 管理
GET    /api/sessions                   → 列举所有 Session
POST   /api/sessions                   → 创建 Session
GET    /api/sessions/{id}              → 获取 Session 详情
GET    /api/sessions/{id}/history      → 获取历史消息
PATCH  /api/sessions/{id}              → 更新 Session
POST   /api/sessions/{id}/rename       → 重命名 Session
POST   /api/sessions/{id}/branch       → 分支 Session
DELETE /api/sessions/{id}              → 删除 Session
POST   /api/sessions/batch-delete      → 批量删除

# Worker 管理
POST   /api/spawn                      → 启动新 Worker
POST   /api/task                       → 向 Worker 发送任务
POST   /api/kill/{worker_id}           → 停止 Worker
GET    /api/list                        → 列举活跃 Worker
POST   /api/worker/{id}/restart        → 重启 Worker
POST   /api/worker/{id}/settings       → 更新 Worker 配置
POST   /api/worker/{id}/rename         → 重命名 Worker
POST   /api/worker/{id}/branch         → Worker 分支
POST   /api/worker/{id}/interrupt      → 中断 Worker
POST   /api/worker/{id}/takeover       → 接管 Worker 终端
GET    /api/worker/{id}/takeover-command → 生成接管命令

# Character / Memory
GET    /api/characters/profiles        → 列出可用 Profile
POST   /api/characters                 → 创建 Character
GET    /api/characters                 → 列出 Character
GET    /api/characters/{id}            → 获取 Character 详情
DELETE /api/characters/{id}            → 删除 Character
POST   /api/memory/index               → 索引记忆目录（.md → SQLite）
GET    /api/memory/search              → 混合检索记忆
GET    /api/memory/stats               → 记忆库统计
POST   /api/memory/inject              → 手动注入记忆

# 文件系统（session workdir 内）
GET    /api/fs/list                    → 列出目录
GET    /api/fs/read                    → 读取文件
POST   /api/fs/write                   → 写入文件
POST   /api/fs/rename                  → 重命名
POST   /api/fs/delete                  → 删除

# Adapter / 模型
GET    /api/adapters                   → 列举可用 Adapter
GET    /api/models?adapter=cbc         → 获取模型列表
GET    /api/adapter/config?adapter=cbc → Adapter 配置

# CBC 导入
GET    /api/cbc/projects               → CBC 项目列表
GET    /api/cbc/sessions               → CBC Session 列表
GET    /api/cbc/browse                 → 浏览 CBC Session 文件
POST   /api/cbc/sessions/import        → 导入 CBC Session

# Kimi 导入
GET    /api/kimi/workspaces            → Kimi Workspace 列表
GET    /api/kimi/sessions              → Kimi Session 列表
POST   /api/kimi/sessions/import       → 导入 Kimi Session

# WebSocket 事件流
WS   /ws                               → Dashboard 事件流（广播）
WS   /ws/agent                         → Meta-Agent 专用事件流
```
