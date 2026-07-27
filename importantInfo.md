# 端口信息

| 服务 | 端口 | 说明 |
|------|------|------|
| Pan | **8767**（test）/ **8768**（main） | FastAPI 主服务（HTTP + WebSocket），由 `config.json` 的 `port` 字段或环境变量 `PAN_PORT` 控制 |
| Remote 状态 | **8769** | Remote 通道状态 HTTP 服务，由 `config.json` 的 `remote.status_port` 控制 |
| NoneBot2 / QQ Bridge | **8080** | NoneBot2 自带的 uvicorn HTTP 服务（仅框架用，不对外）|
| NapCat (QQ) | **3001** | NapCat 正向 WebSocket 服务端，供 NoneBot2 连接 |

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
2. Pan：`python main.py`
3. QQ Bridge：`cd packages/qq && python bot.py`
