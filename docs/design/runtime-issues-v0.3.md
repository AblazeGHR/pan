# RuntimeIssue/Event v0.3 第一阶段契约

本阶段把原生 provider error 观察统一成可扩展的 `RuntimeIssue` 和
`runtime.issue.v1` Event。它是诊断事实，不是重试、账号切换、重启、恢复或额度调度策略。

每条结果分为三层：`observation`（adapter 原始类型、实际存在的 code/status/text
和受限脱敏 raw）、`classification`（分类及 `confidence`）和 `delivery`
（observed/classified/persisted/delivered/deduped）。缺失的 session、worker、
generation、task、execution、queue、Codex thread/turn 字段保持缺失，不猜测。
`issueId` 对相同 adapter/raw/关联事实稳定，用于去重。

结构化 provider code/status 优先于文本。只有 adapter 自己的严格词表才可作为
次级判断；普通 tool failure、generic 429 或冲突证据保持 `unknown`。

| 分类 | 证据 |
|---|---|
| `codex_quota_exhausted` | Codex code `usage_limit_reached`、`quota_exceeded`、`quota_exhausted`、`insufficient_quota`，或明确 usage/plan limit reached/exhausted |
| `codex_capacity` | Codex code `capacity_exceeded`、`model_capacity_exceeded`、`server_overloaded`、`overloaded`，或明确 model/capacity reached/exceeded |
| `cbc_rate_limited` | CBC code/status 明确 429/rate-limit，或严格 rate limited/too many requests |
| `provider_rate_limited` | 其他 adapter 的结构化 rate-limit code/status |
| `unknown` | 证据不足、普通 tool failure、generic 429 或无法确定 |

关联字段来自 provider/wrapper 和宿主白名单：`sessionId`、`workerId`、`generation`、
`taskId`、`executionId`、`queueItemId`、`threadId`、`turnId`。Raw、文本、key 数量
有界并裁剪敏感字段。只读 `GET /api/diagnostics/runtime-issues/schema` 返回版本、
分类、关联字段和 delivery 状态；事件 envelope 类型为 `runtime.issue.v1`。

最新外部 `data/logs/session-errors.jsonl` 未随本隔离树提供，故回归保留既有
Codex wrapper 的真实 `codex.turn_error` 结构，并在交付报告标明该证据缺口，
不伪称已验证该文件的具体文本。该投影不触发任何 retry/account/restart/recovery。

## Follow-up: quota policy foundation and real evidence

The previously missing source file is now explicitly supplied and read-only
fixtures preserve real `usageLimitExceeded` and `serverOverloaded` envelopes.
See [quota-policy-v0.3.md](quota-policy-v0.3.md) for original line hashes,
conservative parsing, persistent quota query Jobs, policy receipts and the
fail-closed production adapter boundary. This supersedes the earlier missing-file
note; no synthetic fixture is presented as a real provider capture.
