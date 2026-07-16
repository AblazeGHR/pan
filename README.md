# Pan

> 中间层，让 Meta-Agent 调度管理多个 CLI Agent 进程，同时人类可随时观察、插话、接管任意进程。

**技术栈**：Python 3.14 + FastAPI + WebSocket

---

## 当前进度

| Phase | 目标 | 状态 |
|:-----|------|:----:|
| Phase 1 | Core 内部清洁化，稳定 API | 完成 |
| Phase 2 | 逐个抽离通道模块 | 进行中 |

### 项目架构 (Phase 1)

```
Pan/
├── main.py                    入口
├── packages/
│   ├── core/                  Core 模块（进程管理 + 消息路由）
│   │   ├── worker.py          Worker 生命周期管理
│   │   ├── session.py         Session 存储（JSON）
│   │   ├── config.py          配置加载
│   │   └── adapters/          CLI Adapter（cbc/claude/...）
│   ├── web/                  Web 通道（Dashboard + HTTP API）
│   │   ├── server.py          FastAPI 路由 + WebSocket
│   │   ├── index.html         Dashboard 桌面版
│   │   ├── mobile.html        Dashboard 移动版
│   │   ├── ts/                TypeScript 源码
│   │   └── static/            编译产物 + CSS
│   └── qq/                   QQ Bot 通道（NoneBot2 桥接）
│       ├── plugin.py          QQ 消息处理
│       └── bot.py             QQ Bot 入口
├── scripts/                   启动/停止脚本
├── docs/                      文档
├── data/                      运行时数据（gitignored）
└── config.json                配置文件（gitignored）
```

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动
python main.py
# → http://localhost:8767
```

## 架构

```
         Meta-Agent                   人类
    (CodeBuddy 等)              (Dashboard)
          │                          │
    /ws/agent 通道              /ws + HTTP
    （事件流 + 命令）          （观察 + 注入 + 接管）
          │                          │
          └──────────┬───────────────┘
                     │
            ┌────────▼────────┐
            │  Pan Core         │
            │  (FastAPI 服务)    │
            │                   │
            │  Session Manager │
            │  ├─ Worker-1     │── cbc (stream-json)
            │  ├─ Worker-2     │── cbc (stream-json)
            │  └─ Worker-N     │── ...
            │                   │
            │  Event Bus       │─── WS 广播
            │  Session Store   │─── JSON 持久化
            └──────────────────┘
```

## 关键设计

### Worker 与 Session 分离

- **Worker** — 运行时 CLI 子进程，持有 session_id 引用。kill 后 Worker 消失。
- **Session** — 持久化数据（UUID `ses_<16hex>`），独立于 Worker 生命周期。

### CLI Adapter 协议

通过 `CliAdapter` 协议抽象 CLI 工具差异：
- `cbc` — CodeBuddy CLI（stream-json 协议）
- 未来：`claude-cli`、`gemini-cli` 等

### 模块通信

所有通道通过 Core 暴露的 HTTP/WS 通信，**不 import Core 内部实现**：

```
POST /api/sessions          → CRUD Session
POST /api/spawn             → 启动 Worker
POST /api/task              → 发任务
WS   /ws                    → Dashboard 事件流
WS   /ws/agent              → Meta-Agent 专用
```
