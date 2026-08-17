# MCP Server 常驻复用 — 立项（已降级为可选优化）

> 基于 2026-08-16 MCP 链路改造（移除 `-d`、去 `.mcp.json` fallback、manifest 绝对路径）后暴露的性能问题立项。
> 状态：**已部分过时**——cbc 2.137.0 的 stream+MCP 已解决"每任务冷启动 MCP server"主痛点，本方案降级为**跨 cbc 进程共享 server** 的可选优化。创建：2026-08-16

---

## 一、立项背景

~~当前 MCP 模式的进程模型是每个任务全量冷启动（one-shot cbc + 每次新 spawn MCP server，~107s 首启 / ~19s 后续）。~~

**2026-08-16 更新（cbc 2.137.0）**：stream+MCP 已实现（`adapter_config.output_mode="stream"`，长驻 cbc 进程 + `--mcp-config`），**MCP server 随长驻进程只启动一次，任务间复用**——"每任务冷启动 server"的痛点已被 stream+MCP 解决，无需本方案。

剩余价值（可选优化）：**多个 cbc 进程（多个 worker）复用同一个 MCP server**。one-shot / 非 stream 模式下每个 cbc 各自 spawn 一个 server，若有多个 worker 并发使用同一 server，SSE/HTTP 常驻可让它们共享一个进程。

---

## 二、事实调查

### 2.1 进程模型（2026-08-16 实测确认）

| 层 | 生命周期 | 说明 |
|----|---------|------|
| cbc 进程 | stream+MCP：长驻，server 随进程启动一次 | `output_mode="stream"` + `--mcp-config`，cbc **2.137.0** 起兼容（实测） |
| MCP server 子进程 | stream+MCP：随长驻 cbc 启动一次，任务间复用 | 已解决"每任务冷启动"主痛点 |
| one-shot（旧路径）| 每任务新 cbc + 新 server | `output_mode` 未设置时兜底；每任务冷启动 ~19s |

### 2.2 复用现状

- **stream+MCP（2.137.0）已消除 server 冷启动**：长驻进程内 MCP server 只启动一次，后续任务直接复用。
- **本方案（SSE 常驻）的剩余价值**：让**多个 cbc 进程**（多个 worker / one-shot 场景）共享同一个 server，而非各自 spawn。

### 2.3 已有的可用基础

`packages/mcp/server.py` 的 `main()` 已支持三种 transport：

```python
parser.add_argument("--transport", default="stdio",
                    choices=["stdio", "sse", "streamable-http"])
parser.add_argument("--port", type=int, default=9740)
```

即 **SSE / streamable-http transport 的能力已存在**，只差把它作为常驻进程跑起来 + cbc 侧配置。

---

## 三、目标（已降级）

让 MCP server 作为**独立常驻进程**运行（随 Pan 服务启停），供**多个 cbc 客户端**连接**同一个** server，避免每个 cbc 各自 spawn 一个 server 进程。

预期收益：
- one-shot / 非 stream 场景下，多个 worker 共享一个 server，省去各自的 server 进程
- server 与后端（Pan API / COC 规则库）的连接复用
- **注意**：主痛点（单 cbc 每任务冷启动）已由 stream+MCP 解决，本方案收益有限

---

## 四、方案（初拟）

### 4.1 MCP server 以 SSE/streamable-http 常驻

```bash
python -m packages.mcp.server --transport sse --port 9740 --host 127.0.0.1
```

生命周期挂到 Pan 服务（随 `main.py` 启动/停止），或独立进程管理器。

### 4.2 manifest 的 mcp_servers 配置改为远程

```json
{
  "name": "pan",
  "type": "sse",
  "url": "http://127.0.0.1:9740/sse"
}
```

cbc 的 `--mcp-config` 不再 spawn 子进程，改为连接远程 endpoint。

### 4.3 影响面

- `packages/mcp/server.py`：已支持，可能需微调（sse path、启动方式）
- `packages/mcp/manifest.json`：pan server 声明改为 sse
- `_apply_mcp_servers` / `mcp_args`：透传 `type` / `url` 字段（当前只透传 command/args/env/cwd）
- rulewhisper 插件 manifest：如需复用也改（或保持 stdio，视收益）

---

## 五、待验证点（必须先做可行性试验）

1. **cbc 是否支持 sse/http type 的 MCP server**：mcp.json 里 `"type": "sse"` + `url` 能否被 cbc 识别并连接成功。
   - 试验：起一个 SSE pan server（`--transport sse --port 9740`），cbc `--mcp-config` 指向 sse 配置，验证工具是否 direct connected、能否直接调用。
2. **多 cbc 并发连接同一 server**：两个 worker 同时用同一 SSE server，验证无冲突（FastMCP sse 是否支持多客户端）。
3. **server 异常处理**：常驻 server 崩溃/退出后，Pan 服务是否感知并重启（生命周期管理）。
4. **rulewhisper 是否也要常驻**：它是外部插件（ai_coc），是否值得同样改造取决于使用频率。

---

## 六、代价与风险

| 项 | 说明 |
|----|------|
| 常驻进程 | 多一个常驻 python 进程，内存占用；需随 Pan 服务管理生命周期 |
| 端口占用 | 9740（可配置）；需绑定 `127.0.0.1`，不暴露公网 |
| 鉴权 | Pan 无鉴权是既定姿态，SSE server 同样绑 loopback 即可（与 `/api` 同级信任模型） |
| 兼容性 | 若 cbc 对远程 MCP 支持不完善（工具列表、defer 状态、错误处理），可能引入新问题；需验证后评估 |
| stdio 保留 | 试验通过前不删除 stdio 路径，作为降级方案 |

---

## 七、决策项

1. **做不做**：stream+MCP 已解决单 cbc 冷启动；本方案只剩"多 worker 共享 server"的边际收益。**建议暂缓**，除非出现多个 worker 并发使用同一 server 且 server 资源成为瓶颈。
2. **server 生命周期管理方式**：随 `main.py` 子进程 / 独立脚本 / 手动启动？
3. **范围**：先只改 pan server，还是 rulewhisper 一并常驻？
4. **transport 选型**：sse vs streamable-http（取决于 cbc 支持度）。

---

## 八、任务拆解（若立项通过）

- [ ] 可行性试验：cbc 连接 SSE MCP server（验证 #五.1）
- [ ] server.py 启动方式调整（如需，如 sse path、优雅退出）
- [ ] manifest / `_apply_mcp_servers` / `mcp_args` 支持远程配置透传
- [ ] 生命周期管理（随 Pan 服务启停）
- [ ] 多客户端并发验证 + 回归测试（stream / meta-agent / coc-keeper 三条链路）
- [ ] 文档更新（defer 机制文档 / 踩坑记录）
