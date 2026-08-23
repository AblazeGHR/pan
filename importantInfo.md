# 端口信息

| 服务 | 端口 | 说明 |
|------|------|------|
| Pan | **8767**（test）/ **8768**（main） | FastAPI 主服务（HTTP + WebSocket），由 `config.json` 的 `port` 字段或环境变量 `PAN_PORT` 控制 |
| Remote 状态 | **8769** | Remote 通道状态 HTTP 服务，由 `config.json` 的 `remote.status_port` 控制 |
| NoneBot2 / QQ Bridge | **8080** | NoneBot2 自带的 uvicorn HTTP 服务（仅框架用，不对外）|
| NapCat (QQ) | **3001** | NapCat 正向 WebSocket 服务端，供 NoneBot2 连接 |

> **Remote Tunnel URL 机制**：公网域名来自 `config.json` → `remote.config_path` 指向的 cloudflared yml 的 `ingress.hostname`；tunnel 暴露的是 Pan 主端口（`config.port`）。`scripts/start_cf.ps1` 会读取 `config.json` 并注入临时 yml，**不依赖 `remote.enabled` 字段**。

### Pan

- `http://127.0.0.1:{port}` — Dashboard 页面（legacy 前端）
- `http://127.0.0.1:{port}/react/` — React Dashboard（需 `frontend: coexist` 或 `react`）
- `ws://127.0.0.1:{port}/ws` — Dashboard WebSocket
- `ws://127.0.0.1:{port}/ws/agent` — Meta-Agent WebSocket

### QQ Bridge

- NapCat WebSocket 服务地址：`ws://127.0.0.1:3001`
- 对应 `.env` 配置：`ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]`
- 对应 NapCat WebSocket 配置：主机 `127.0.0.1`、端口 `3001`

### 启动顺序

1. NapCat（先启动，保持后台）
2. Pan：`python main.py`（或 `scripts/start_pan.bat`）

> **QQ Bridge 无需手动启动**：main.py 按 `config.json` 的 `qq.enabled`（默认 true）自动 spawn `packages/qq/bot.py` 子进程（PID 写 `data/qq_bot.pid`），随主服务一起停止。QQ bot 运行在 miniforge 解释器（NoneBot 未装在项目 .venv），可用环境变量 `PAN_QQ_PYTHON` 覆盖。
>
> QQ Bridge 默认端口从 `config.json` 的 `port` 字段读取，可用环境变量 `PAN_URL` 覆盖。
