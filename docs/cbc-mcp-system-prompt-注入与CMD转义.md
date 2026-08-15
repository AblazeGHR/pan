# cbc system_prompt 注入与 Windows .CMD 参数转义

> 2026-08-15 Meta-Agent 集成时发现：profile 创建的 session 中 system_prompt 未注入
> （模型不认识自己身份），但 MCP 工具正常。定位到两个独立 bug。

## 症状

- 用 meta-agent profile 创建 session，问"你的身份是什么？" → 模型回答自己是 CodeBuddy Code，而非 Meta-Agent
- 但 MCP 工具（session_list 等）能正常调用
- 设置 `mcp_mode: always` 后出现 `(no output)` error，cbc 进程 28ms 即退出

## 根因 1：system_prompt 注入方式错误

### 尝试过的三种方式

| 方式 | 效果 |
|------|------|
| `f"{text}\n\n---\n{s.system_prompt}"`（拼接进用户消息） | **不生效**——hy3 把它当普通用户文本，不作系统指令 |
| `--append-system-prompt <prompt>` | **不生效**——hy3 忽略追加语义，模型仍认自己为 CodeBuddy |
| `--system-prompt <prompt>`（覆盖式） | **生效**——cbc 注入真实 system message，模型正确认知身份 |

### 结论

- 只有 `--system-prompt`（覆盖 cbc 默认系统提示）被模型真正当作系统指令
- `--append-system-prompt` 从 CLI help 看是"追加"，但 hy3 实际不理会
- 拼接进用户消息更不可靠——那是给 stream 模式的旧做法，MCP one-shot 不适用

### 当前实现（worker.py `_consumer_mcp`）

```python
# 只在 session 首条消息注入（cli_session_id 捕获前）；之后靠 --resume 延续
if s.system_prompt and not s.cli_session_id:
    args.extend(["--system-prompt", s.system_prompt])
args.append(text)
```

验证：首条消息注入后，`--resume` 的后续任务身份延续（system prompt 随 cbc session 持久化）。

## 根因 2：Windows 上 .CMD 批处理参数转义崩溃

### 现象

- `shutil.which("cbc")` 在 Windows 解析到 npm shim：`D:\node_npm\node_global\cbc.CMD`
- `asyncio.create_subprocess_exec` 直接执行 `.CMD` 会经过 cmd.exe
- 766 字中文 system_prompt（含 `"pan"` 引号、逗号、换行）被 cmd.exe 转义截断
- cbc 进程 28ms 退出，`(no output)`
- 但**不传 system_prompt 时** .CMD 能跑（参数短）——所以之前 meta-agent 的 session_list 测试正常

### 修复（adapter.py `_resolve_cbc_argv`）

把 `.CMD` shim 解析为 `node <entry.js>`，参数直接传给 node，绕开 cmd.exe：

```python
def _resolve_cbc_argv(self) -> list[str]:
    path = self._resolve_cbc_path()
    if path.lower().endswith((".cmd", ".bat")):
        shim_dir = os.path.dirname(os.path.abspath(path))
        node_exe = os.path.join(shim_dir, "node.exe")  # 不存在则 fallback "node"
        # npm shim 布局：<dir>/node_modules/<pkg>/bin/<name>
        candidates = [...]
        # 命中则返回 [node_exe, js_entry]
        return [node_exe, entry]
    return [path]
```

- `base_args()` / `base_args_stream()` 改用 `_resolve_cbc_argv()` 作为 argv 前缀
- `.CMD`/`.BAT` 均处理；非 Windows 或非 .CMD 路径不受影响

### 诊断方法

- 加 `print(f"[DBG spawn] argc={len(args)} ...", file=sys.stderr)` 到 spawn 前
- raw JSONL（`.pan-cbc-raw.jsonl`）里 result 的 `duration_ms` 若只有几十 ms = 进程立即退出
- 手动 `cbc -p ... --system-prompt "长中文" "prompt"` 对比能否跑通

## 附带发现：首次 MCP server 冷启动慢

- MCP one-shot 首次任务：cbc 需启动 pan MCP server（`python -m packages.mcp.server`），实测 ~107s
- 若 handoff timeout < 107s 会超时；后续任务（`--resume`）~19s 正常
- 这是**性能/冷启动**问题，非 bug；与 `memory.enabled` 关闭后的 memory 注入无超时问题不同

## 关联坑

- memory 注入无超时：`_maybe_inject_memory` 首次加载 bge 模型可阻塞数分钟（已加 15s 超时 + `memory.enabled` 开关）
- MCP server cwd 必须指向包根（`${PLUGIN_DIR}/../..`），否则 `ModuleNotFoundError`
- MCP 工具是否 deferred 取决于加载路径（2026-08-16 实测）：`--mcp-config` 显式传 → 工具**直接可见**（无需 ToolSearch）；项目级 `.mcp.json` → 需 ToolSearch 发现。system_prompt 是否要引导 ToolSearch 取决于实际路径

## 完整测试命令（手动复现）

```bash
# 验证 system-prompt 生效
cbc -p --output-format stream-json --mcp-config <workdir>/.codebuddy/mcp.json \
  -d <workdir> -y --model hy3 --system-prompt "你是 Meta-Agent" "你的身份是什么？"

# 验证长参数不被 .CMD 截断
python -c "import asyncio; from packages.core.adapters.cbc.adapter import CbcAdapter; print(CbcAdapter()._resolve_cbc_argv())"
```
