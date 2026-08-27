# 端口信息

| 服务 | 端口 | 说明 |
|------|------|------|
| Pan | **8767**（test）/ **8768**（main） | FastAPI 主服务（HTTP + WebSocket），由 `config.json` 的 `port` 字段或环境变量 `PAN_PORT` 控制 |
| Remote 状态 | **8769** | Remote 通道状态 HTTP 服务，由 `config.json` 的 `remote.status_port` 控制 |
| NoneBot2 / QQ Bridge | **8080** | NoneBot2 自带的 uvicorn HTTP 服务（仅框架用，不对外）|
| NapCat (QQ) | **3001** | NapCat 正向 WebSocket 服务端，供 NoneBot2 连接 |
| LLOneBot (QQ) | **3002** | LLOneBot 通道（`qq.channel: llonebot` 时使用），OneBot 11 网关插件化的另一通道实现 |

> **Remote Tunnel URL 机制**：公网域名来自 `config.json` → `remote.config_path` 指向的 cloudflared yml 的 `ingress.hostname`；tunnel 暴露的是 Pan 主端口（`config.port`）。`scripts/start_cf.ps1` 会读取 `config.json` 并注入临时 yml，**不依赖 `remote.enabled` 字段**。

### Pan

- `http://127.0.0.1:{port}` — 默认 307 重定向到 React Dashboard `/react/`（`frontend: coexist` / `react` 时）
- `http://127.0.0.1:{port}/vanilla` — 旧版 Vanilla Dashboard
- `ws://127.0.0.1:{port}/ws` — Dashboard WebSocket
- `ws://127.0.0.1:{port}/ws/agent` — Meta-Agent WebSocket

### QQ Bridge

QQ 通道已插件化，`config.json` 的 `qq.channel` 选择通道实现（默认 `napcat`，可选 `llonebot`）：

- NapCat 通道：WebSocket 服务地址 `ws://127.0.0.1:3001`；对应 `.env` 配置 `ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]`；NapCat WebSocket 配置主机 `127.0.0.1`、端口 `3001`
- LLOneBot 通道：WebSocket 端口 `3002`，配置位于 LLOneBot 自身数据目录，需在 manager 侧绑定账号

### 启动顺序

1. QQ 通道网关（NapCat 或 LLOneBot，与 `qq.channel` 对应，先启动保持后台）
2. Pan：`python main.py`（或 `scripts/start_pan.bat`）

> **QQ Bridge 无需手动启动**：main.py 按 `config.json` 的 `qq.enabled`（默认 true）自动 spawn `packages/qq/bot.py` 子进程（PID 写 `data/qq_bot.pid`），随主服务一起停止。QQ bot 运行在 miniforge 解释器（NoneBot 未装在项目 .venv），可用环境变量 `PAN_QQ_PYTHON` 覆盖。
>
> QQ Bridge 默认端口从 `config.json` 的 `port` 字段读取，可用环境变量 `PAN_URL` 覆盖。
