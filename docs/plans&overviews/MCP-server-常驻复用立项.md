# MCP Server 常驻复用 — 立项

> 基于 2026-08-16 MCP 链路改造（移除 `-d`、去 `.mcp.json` fallback、manifest 绝对路径）后暴露的性能问题立项。
> 状态：立项阶段（仅记录考量与待决策项，**不改代码**） | 创建：2026-08-16

---

## 一、立项背景

当前 MCP 模式的进程模型是**每个任务全量冷启动**：

```
meta-agent worker（一次 handoff 任务）
  └─ 新起 cbc 进程（one-shot，-p）
       └─ 新起 MCP server 子进程（stdio）
  → 任务完成，cbc 退出 → MCP server 子进程随之退出
```

每次任务都要重复冷启动 cbc + MCP server。已知性能记录（`cbc-mcp-system-prompt-注入与CMD转义.md`）：

- MCP one-shot 首次任务：~107s（含启动 pan MCP server）
- 后续任务（`--resume`）：~19s

**痛点**：meta-agent 编排多步任务（多次 handoff）时，每步都付出一次 cbc + MCP server 的冷启动成本，且 server 每步重新建立与后端（Pan API / COC 规则库）的连接。

---

## 二、事实调查

### 2.1 当前进程模型（2026-08-16 实测确认）

| 层 | 生命周期 | 原因 |
|----|---------|------|
| cbc 进程 | 每任务一个，one-shot | `--mcp-config` + `--input-format stream-json` **不兼容**（踩坑 #5），MCP 模式无法走 stream 长连接 |
| MCP server 子进程 | 每任务一个，随 cbc 退出 | stdio transport：server 通过 stdin/stdout 与**恰好一个** cbc 通信，cbc 退出即 stdin 关闭，server 结束 |

### 2.2 复用被两层死结卡住

1. **cbc 无法复用**：one-shot 模式下 cbc 处理完 prompt 即退出；MCP 与 stream-json 不兼容，无法改成长连接。
2. **stdio server 无法复用**：server 绑定单一客户端进程生命周期，无法"常驻等待下一个客户端"。

### 2.3 已有的可用基础

`packages/mcp/server.py` 的 `main()` 已支持三种 transport：

```python
parser.add_argument("--transport", default="stdio",
                    choices=["stdio", "sse", "streamable-http"])
parser.add_argument("--port", type=int, default=9740)
```

即 **SSE / streamable-http transport 的能力已存在**，只差把它作为常驻进程跑起来 + cbc 侧配置。

---

## 三、目标

让 MCP server 作为**独立常驻进程**运行（随 Pan 服务启停），多个 cbc 客户端连接**同一个** server，消除每次任务的 server 冷启动成本。

预期收益：
- MCP server 只冷启动一次；后续所有任务零 server 冷启动
- cbc 的 per-task 限制保留（cbc 不可复用是 cbc 本身限制），但大头成本（server 启动 + 后端连接重建）消除

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

1. **做不做**：冷启动（~19s/任务）是否为当前主要瓶颈？若是 → 推进；若任务频率低 → 可暂缓。
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
