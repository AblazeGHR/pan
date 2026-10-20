# CBC 模型额度倍率显示 — 真实 HTTP + 生产浏览器验收（2026-10-21）

隔离实例：worktree `cbc-model-multiplier-integration-20261021`（基于 main
20473b41 + 本任务 cherry-pick 1bb38147），端口 18765，数据目录为该 worktree
的全新 `data/`（未复用任何实用数据；QQ 子进程经 `PAN_URL` 指向本实例，未连接
8767/8768）。前端为生产构建（vite build）。浏览器：Playwright + Edge。

## 验收项与结果

| 项 | 结果 | 证据 |
|---|---|---|
| `/api/adapter/config?adapter=cbc` | 20 个模型（纯 ID 字符串）+ 33 条倍率；step-5-preview=0.0、glm-5.3-flash=0.08、kimi-k3-1=1.62、deepseek-v4-pro=0.51 | HTTP 响应实测 |
| `/api/adapter/config?adapter=kimi` | `modelCreditMultipliers={}`，其他字段形状不变 | HTTP 响应实测 |
| `/api/models?adapter=cbc` | 附加 `modelCreditMultipliers`，`models`/`default` 不变 | HTTP 响应实测 |
| 并发响应性 | reload 清缓存后，未缓存的 config 请求 5.2ms，并发廉价请求 2.8ms，互不阻塞 | curl 并发计时 |
| New Session 模型下拉 | 20 个 cbc 选项全部带真实倍率后缀；option value 均为纯 ID | 浏览器快照 + DOM 检查 |
| 切 adapter（kimi） | 模型选项只显原名（无倍率能力） | 浏览器 DOM 检查 |
| 聊天 ModelPill 当前值 | `glm-5.3-flash (x0.08)` → 切换后 `step-5-preview (x0)`，后缀居右 | 浏览器快照 |
| 设置弹窗 Model 按钮 | `step-5-preview (x0)` | 浏览器快照 |
| 会话列表项 | `glm-5.3-flash (x0.08)` → `step-5-preview (x0)` | 浏览器快照 |
| 0 倍率 | hy3 / step-5-preview 显示 `(x0)` | 浏览器实测 |
| 搜索 | 原始 ID（`kimi-k3` → `kimi-k3-1 (x1.62)`）与倍率文本（`x0.08` → 3 个模型）均可检索 | 浏览器实测 |
| 提交/磁盘纯度 | 切换模型后 API `session.model='step-5-preview'`，磁盘 `ses_*.json` `model` 同为纯 ID | API + 磁盘文件实测 |
| 未调用收费模型 | 全程未发送任何消息，worker 保持 offline | 实例日志 |

截图：`model-label-acceptance.png`（设置弹窗 + ModelPill + 会话列表同屏）。

已知无关噪音：console 一条 404（`/api/sessions/__pending_session-1/native-control`），
为新建会话 pending 占位的既有行为，与本任务改动无关。
