# opencode 真实事件流 fixtures（`opencode run --format json`）

本目录保存 **真实捕获** 的 opencode stdout 事件流（JSONL，每行一个事件），用于：

1. 作为「opencode 的 `type: text` 是完整消息还是流式片段」这一判定的证据；
2. 作为后续补测试（`_stamp_ws_event_ts` 事件矩阵、adapter `extract_assistant_blocks` 覆盖）的输入。

## 捕获来源（provenance）

- 日期：2026-09-25（本地时区 Asia/Shanghai）
- CLI：`opencode` 1.18.25（npm 全局安装）
- 模型：`opencode/big-pickle`（Pan opencode adapter 的 `_DEFAULT_MODEL`，网关免费模型，`cost: 0`）
- 工作目录：一次性临时目录（fixture 中已归一为 `<workdir>`）
- 命令（与 `packages/core/adapters/opencode/wrapper.py:_build_run_args` 的实际调用形态一致）：

```bash
# 1) 最小文本
opencode run "只回复两个字：收到" --format json --no-replay --model opencode/big-pickle
# 2) 长文本（347 字符、40 行）
opencode run "请输出 1 到 40 的平方数列表，... 不要解释。" --format json --no-replay --model opencode/big-pickle
# 3) 工具调用（--auto 允许自动批准）
opencode run "用 Read 工具读取当前目录的 probe.txt，然后只回复文件内容。" \
  --format json --no-replay --auto --model opencode/big-pickle
# 4) 思考块（--thinking）
opencode run "一个农夫有 17 只羊，除了 9 只以外都跑了，还剩几只？只回复数字。" \
  --format json --no-replay --thinking --model opencode/big-pickle
```

## 文件与事件清单

| 文件 | 事件数 | 事件序列（`type`） |
|---|---|---|
| `run_json_text_min.jsonl` | 3 | `step_start` → `text`（2 字符）→ `step_finish` |
| `run_json_text_long.jsonl` | 3 | `step_start` → `text`（347 字符，单 part）→ `step_finish` |
| `run_json_tool.jsonl` | 6 | `step_start` → `tool_use` → `step_finish` → `step_start` → `text` → `step_finish` |
| `run_json_thinking.jsonl` | 4 | `step_start` → `reasoning`（122 字符）→ `text`（1 字符）→ `step_finish` |

### 事件形状

所有事件都是 `{"type", "timestamp"(epoch ms), "sessionID", "part": {...}}`，**没有 `role` 字段**：

| 顶层 `type` | `part.type` | 含义 | Pan 历史块（`opencode/adapter.py:extract_assistant_blocks`） |
|---|---|---|---|
| `step_start` | `step-start` | 一步开始（无文本） | 无 |
| `reasoning` | `reasoning` | 已完成的思考 part（`--thinking` 才有） | `{"role": "thinking", "content": part.text}` |
| `text` | `text` | **已完成的 assistant 文本 part** | `{"role": "assistant", "content": part.text}` |
| `tool_use` | `tool` | 已完成的工具 part（`part.state.status == "completed"`） | `{"role": "tool", "content": "tool(input)\n→ output"}` |
| `step_finish` | `step-finish` | 一步结束（`reason: stop / tool-calls`，含 `tokens`/`cost`） | 无 |

`text`/`reasoning`/`tool` part 都带 `part.time = {"start": ms, "end": ms}`；顶层事件另有 `timestamp`（ms）。

## 关键结论：`type: text` 是完整消息，不是流式片段

- 2 字符、1 字符、258 字符、**347 字符（40 行列表）** 四次捕获，每次该 assistant 回复都只有 **1 个 `text` 事件、1 个 `part.id`**，且 `part.time.end` 已存在（完成时间窗）。
- `--format json` 模式下不存在 delta/`content.part` 形状的增量事件：一次 step 内只有 `step_start` → 若干「已完成 part」事件（`reasoning`/`tool_use`/`text`）→ `step_finish`。
- 结论：若给 `type: text`（`part.type == "text"`）事件打 WS ts，时间点即该 part 的完成时刻，**不会冻结在片段中间**。

> 反例防护：`reasoning`（thinking 行）与 `tool_use`（tool 行）前端不渲染时间（`MessageBubble.tsx` 只给 user/assistant 渲染），也不需要打点。

## 次要结论（本次捕获顺带发现，与本 PR 的 ts 修复互为前提）

前端 `packages/web/src/hooks/useWebSocket.ts:extractBlocks()`（约 827-905 行）只识别
`codex.*`、`role === "assistant" | "thinking"`（cbc/kimi 形状）与 `content.part`（kimi/codex 增量），
**不识别 opencode 的 `type: text|reasoning|tool_use` + `part` 形状** → 对 opencode 事件返回 0 个块
（`role = event.role ?? event.type` 判不中，`content.part` 分支不匹配），事件在 `appendEventToMessages`
里成为 no-op。因此：

- opencode 的实时内容目前不进入 live transcript（只在历史刷新后出现，属既有前端缺口）；
- 在补上前端形状支持之前，**后端即使给 `type: text` 打 ts 也不会有任何可见效果**。

## 在测试里使用

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "support" / "opencode_events"

def load_opencode_events(name: str) -> list[dict]:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]

events = load_opencode_events("run_json_text_min.jsonl")
assert [e["type"] for e in events] == ["step_start", "text", "step_finish"]
text_event = next(e for e in events if e["type"] == "text")
assert text_event["part"]["type"] == "text" and text_event["part"]["text"] == "收到"
```

## 归一化说明（唯一偏离逐字节原文之处）

为可复用与去个人化，只做了以下替换；其余字段（`timestamp`、`part.time`、`tokens`、`cost`、`reason`、`text`、`state` 结构）保持原文：

- `sessionID` → `ses_fixture_000N`；`part.id` → `prt_fixture_NNNN`；
  `part.messageID` → `msg_fixture_NNNN`；`part.callID` → `call_fixture_NNNN`。
- 工具 part 的 `state`（`input.filePath`、`output`、`metadata.display.path` 等）中一次性临时目录前缀 → `<workdir>`。

重新捕获（如需更新 CLI 版本后的形状）：按上面的命令再跑一次，把 stdout 重定向为 `.jsonl` 即可。
