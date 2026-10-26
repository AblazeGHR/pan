# Background Job retryIf 设计（2026-10-26）

## 目标

把“是否重试”从固定逻辑改成 Job 级别的显式策略，允许同一份执行器按错误类型恢复，例如长时间 watcher 同时重试 `failed` 与 `orphaned`。

## 定义

Job 定义新增：

```json
{
  "retryIf": ["failed", "orphaned"],
  "maxRetries": 3,
  "backoffSec": 60
}
```

- `retryIf`：终态原因命中才重试；未配置表示不自动重试。
- `maxRetries`：同一 Job 的最大自动重试次数；必须持久化，重启后继续计数。
- `backoffSec`：下一次尝试前的等待，支持上限/退避由实现决定。

## 终态原因（稳定字符串）

- `failed`：进程非零退出或执行器失败。
- `orphaned`：runner 身份丢失（关机、PID 重用、无法核实启动身份）。
- `timeout`：执行超时。
- `interrupted`：宿主/用户中断，非任务自身失败。
- `cancelled`：用户取消，**永不自动重试**，除非用户显式恢复。
- `dependency-unavailable`：依赖/服务不可用，可按策略重试。

## 语义

1. Job 到达终态时记录 `reason` 与 `attempt`，不把 `orphaned` 混同为 `failed`。
2. 宿主启动后先 reconcile：对 `recoverable=true` 且原 runner 已消失的 Job，生成 `orphaned` 终态；若 `retryIf` 包含 `orphaned`，自动创建新 attempt。
3. 重试保留原 Job ID、argv、cwd、任务身份；新 attempt 有独立 ID 与创建时间。
4. `cancelled` 优先于 `retryIf`；用户取消后即使原因命中也不重试。
5. 有副作用的 Job 默认不配置 `retryIf`；启用时必须提供幂等键或去重证明。
6. 超过 `maxRetries` 后保持终态，并记录最后一次原因。
7. UI/API 必须显示当前 attempt、上次原因、下次重试时间/剩余次数。

## 验收

- `retryIf` 命中/未命中矩阵；`cancelled` 负控。
- 重启后 `orphaned` 自动恢复，attempt 计数不丢。
- 非零退出触发 `failed` 重试；超时不误判为 failed。
- 达上限不再重试，状态可查询。
- 有副作用 Job 未显式 opt-in 时不重试。
- 文档、API schema、UI 状态与设计一致。

## 实施顺序

P10，在 P9 practical 迁移预演之后、R6 删除之前或并行（文件不冲突时）。不 cherry-pick 到 practical；只能随已验收集成树按用户命令推进。
