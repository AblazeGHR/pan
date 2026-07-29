# cbc MCP 踩坑记录与方案选型

> 记录 2026-07-29 Pan 接入 RuleWhisper MCP 的全部试错过程与最终方案。避免后人踩重复的坑。

## 相关文档

- [cbc MCP 端到端调通记录](./cbc-mcp-e2e-调通记录.md) — 2026-07-30 生产级联调，含 prompt 设计、历史重放、model 行为分析
- [下一阶段优化计划](./下一阶段优化计划.md) — 历史截断、性能、`--resume`、前端联调

## 决策总结

| 方案 | 结论 |
|------|------|
| `--mcp-config` + `--input-format stream-json` | **不兼容**，MCP 不加载 |
| `--mcp-config` 文件路径（无 `--input-format`） | **可行**，但进程一问一答后退出 |
| `enableAllProjectMcpServers` + `.mcp.json` + `-d workdir` | **可行**，需要 `.codebuddy/` 目录让 cbc 识别为项目 |
| One-shot MCP 模式 + `--resume` | **最终方案**，每次 task 新开 cbc 进程 |

## 踩坑时间线

### 1. manifest.json 中的 MCP 配置解析

- `${PLUGIN_DIR}` 需要 `Path.resolve()` 标准化路径（不能用简单 `replace`）
- 只替换含 `${PLUGIN_DIR}` 的值，不影响其他参数（`args: ["-m", "src.server.mcp"]` 不能被路径化）

### 2. python 虚拟环境路径

- RuleWhisper 的 `.venv` 在 `ai_coc/` 根，不在 `pan_plugin/` 里
- manifest 中需用 `${PLUGIN_DIR}/../.venv` 指向正确位置

### 3. Session adapter_config 中 mcp_servers 丢失

- `if char.mcp_servers:` 对空列表 `[]` 为 `False`
- 修复：`if char.mcp_servers is not None and len(char.mcp_servers) > 0`

### 4. `--mcp-config` JSON 字符串 vs 文件路径

- `cbc --help` 说支持 `<fileOrString>`
- 测试结果：**只有文件路径有效**，JSON 字符串不生效
- 文件路径格式：`--mcp-config D:/path/to/.mcp.json`

### 5. `--mcp-config` + `--input-format stream-json` 不兼容

- `--input-format stream-json` 让 cbc 忽略 `--mcp-config`
- cbc 只读 `~/.codebuddy/mcp.json`（user-level），不读项目 `.mcp.json`
- 即使 `~/.codebuddy/mcp.json` 有 server 配置，stream-json 模式下也不加载

### 6. `enableAllProjectMcpServers` 需要项目注册

- `--settings '{"enableAllProjectMcpServers":true}'` 只在 cbc "认识的项目"中查找 `.mcp.json`
- Pan 的 workdir 是运行时创建的，cbc 不认识
- 手动 `cbc -d workdir` 后 cbc 会记录该目录，下次生效
- 但 Pan spawn 的 cbc 用 `cwd=` 而不是 `-d`，不会被记录

### 7. `--input-format stream-json` 改为纯文本

- 去掉后 MCP 正常工作
- 但 cbc 进入 one-shot 模式，处理完 prompt 就退出
- 无法维持长连接对话

### 8. `--resume` 携带过期 session ID

- Worker 重启后 `cli_session_id` 未清除
- cbc 尝试 resume 不存在的 session → 立即退出（exit 0）
- 修复：`create_worker` 中杀死旧 worker 后清除 `s.cli_session_id`

## 最终方案：双模式 Worker

```python
# worker.py _consumer
if s.adapter_config.get("mcp_servers"):
    await _consumer_mcp(w, text, source, s)   # one-shot 模式
else:
    await _consumer_stream(w, text, source, s) # stream-json 模式
```

### Stream 模式（无 MCP）

- 维持原有的 `--input-format stream-json` 长连接
- 进程常驻，stdin/stdout 双向通信
- `_read_stdout` 实时解析响应

### One-shot MCP 模式（有 MCP）

- 每次 task 新开 cbc 进程
- `--mcp-config <file>` 正常加载 MCP server
- prompt 作为 CLI 最后一个参数
- `--output-format stream-json` 解析输出（无 `--input-format`）
- `--resume <cli_session_id>` 保持对话上下文
- 阻塞等待进程完成，解析 stream-json 输出
- `w.status` 正确暴露（running → idle）

## cbc 参数速查

```
cbc --help 输出中 MCP 相关参数：

--mcp-config <fileOrString>      加载 MCP 服务器（仅在无 --input-format stream-json 时生效）
--strict-mcp-config              仅使用 --mcp-config 的服务，忽略其他 MCP 配置

cbc mcp add-json <name> <json>  添加 MCP 服务器
cbc mcp list                     列出 MCP 服务器
cbc mcp get <name>               查看服务器详情
cbc mcp remove <name>            移除服务器

--settings '{"enableAllProjectMcpServers":true}'  自动批准项目级 MCP 服务器
```

## `.mcp.json` 文件格式

```json
{
  "mcpServers": {
    "server-name": {
      "command": "path/to/python",
      "args": ["-m", "module.name"],
      "cwd": "D:/project/working/dir"
    }
  },
  "disabledMcpServers": []
}
```

- `command` 必须是可以直接执行的绝对路径
- `cwd` 是进程工作目录，MCP server 的 `python -m` 从这里解析模块
- `args` 是纯参数列表，不会被进一步解析
- **`type: "stdio"` 建议显式添加**，帮助 cbc 正确识别 server 类型（缺失时可能影响发现）
- `.codebuddy/mcp.json` 优于 `.mcp.json` — cbc 通过 `-d workdir` 自动发现前者

## MCP Server 开发注意事项

- 当前兼容版本：`mcp==1.28.1`（Anthropic SDK）
- 不要用 `mcp>=2.0`（API 已变更，`FastMCP` 导入路径不同）
- `from mcp.server.fastmcp import FastMCP`
- 同步工具函数可直接用 `@mcp.tool()` 装饰
- stdio transport：`mcp.run(transport="stdio")`
- SSE transport：`mcp.run(transport="sse")` + `--port`

## 关键教训

1. **`--input-format stream-json` 是 MCP 杀手** — 有它就不要指望 `--mcp-config`
2. **先手动测试后集成** — 用 `cbc -p --no-input-format ...` 验证 MCP 连接
3. **`bool([])` = `False`** — 检查列表时用 `len(list) > 0` 或 `list is not None`
4. **`--resume` 的 session ID 要清理** — 杀死旧 worker 时同步清除 `cli_session_id`
5. **`${PLUGIN_DIR}` 解析需要 `Path.resolve()`** — 简单的 `replace` 会留下 `../` 路径
