# Terminal error → durable report 修复候选

TA 交付供独立验收，未声明已合并或部署。

## 基线与隔离

- 独立 worktree：`D:/project/pan-worktrees/terminal-report-diagnostic-20261023`；分支 `fix/terminal-report-diagnostic-20261023`。
- 只读核对生产 main/practical：均为 `140455976b7c60097fafdd4dc3f8e3b18a699ccc`。main checkout 有其他任务的既有未提交文件，未纳入、修改或清理；本树通过 git worktree add 从 main ref 创建，创建后 clean。
- 精确受测源码：**`3ba59d258b08665557befc207c70d443e1af591b`**。后续仅提交本目录证据/报告。相对 main 的产品改动仅 `packages/core/worker.py`、`packages/core/session_error_log.py`；不混入任何 ETA/Job/Inbox 候选。
- 未移动 main/practical 或原 ETA/组合树；无 push/release，无既有服务、用户 Worker、真实账号/provider 或 8767/8768 操作。

## 复现与最小证据

只读核对指定 `D:/project/Pan/data/logs/session-errors.jsonl` 第 740 行：`2026-10-22T18:57:28.401777+00:00`，Session `ses_07abd0450c2a28e4`，taskSeq 8 / executionSeq 2。原生 error.message/error_text 为 `Selected model is at capacity. Please try a different model.`；codexErrorInfo 为 `serverOverloaded`；原生 source 为 `codex_error_payload`。没有打印或复制历史正文、usage、凭据或其他完整 raw 字段。

最小 fixture：`tests/fixtures/terminal_capacity_report.json`。只保留错误相关字段，result 替换为明确的合成助手正文，另保留证据位置/时间/运行身份说明。原始物理行 SHA256 为 `975cd39860e299d5fc50016f1353b7aa92b8098b1ccdd9874c2c3f9ac92b3a6b`，已按二进制行重新核对一致。fixture 不作为运行时数据源。

main 基线上新增回归先红：`red.log` / `red.xml`，pytest **exit 1，1 failed**，已持久 report 的 status 为 error，但读取 errorText 得到 KeyError。fixture 后续补充了 source 和更多断言/参数化，原红测保留最初 trace，不伪称最终整个集合在基线上运行。

## 产品修复与契约

1. 日志模块继续作为统一原生错误提取/脱敏入口。`cli_error_fields(..., text_only=True)` 为 report 提供严格文本投影，不将整个 opaque error 对象 JSON dump 到报告。新增共享 provenance helper 复用原日志 error_source 优先级，避免日志显示 codex_error_payload、报告却矛盾地显示其他来源。日志归档仍保留自身既有 raw 证据契约。
2. `_persist_terminal_state_locked` 在日志/持久化 await 前冻结本次 `reportDiagnostic`，与同次 terminal record / last_result 一起提交。stream、保留的 oneshot、preflight 自动 report 及手动 report 复用该冻结记录，后续不从 Worker 可变 lastMessage 状态或 JSONL 倒查诊断。原有 owner/generation/去重及 base commit/rollback 路径保留。
3. report 兼容 result 保留原值；新增 errorText / errorCode / errorSource / errorTextTruncated，与 lastMessage / role / truncation 分开。仅 error 终态添加；无原生诊断为 null，状态仍是 error，不把助手正文变成原生诊断或自动分类 capacity。code 仅取原生 error.code 或标量 codexErrorInfo，复杂对象不复制。MA 恢复策略完全独立，未改动。
4. errorText 经共享脱敏后上限 1200 字符，code/source 字符串上限 128；按头尾截断并保留标记。新增常见 Bearer/Basic、带引号 token/password、URL userinfo 的共享裁剪；先裁剪再截断，避免仅截断一部分凭据。lastMessage 也复用该脱敏入口，保留其既有 run-scoped 截断契约。此为已知凭据格式的 best-effort 脱敏，不宣称任意自然语言秘密检测。
5. 批量、Force、Steer 均使用 `_format_report_batch` 展示独立 error 字段；新增字段不参与原 report delivery key / legacy receipt identity，避免升级重投。done/cancelled 和无新字段的旧报告维持原渲染。普通 Queue 列表仍是既有摘要投影，没有为了测试暴露完整 report body。

源码/契约：上述两个 core 文件及 `docs/skills/pan/references/http-api.md`。测试：`tests/test_terminal_report_diagnostic.py`、`tests/test_terminal_report_http.py`、fixture，以及既有 fakeCLI 新增的单个合成场景。不改 Codex adapter 的 result 兼容抽取，不通过替换 result 掩盖诊断；真实 Codex adapter 的 stream 路径在定向单测覆盖，未启动真实 CLI/provider。

## 精确验证

可执行复验入口（本 worktree）：

```powershell
E:/software/miniforge/python.exe audit/terminal-report/verify.py
```

完整 argv/cwd/受测 SHA/退出码见 `commands.json`。驱动 exit **0**，没有设置全局 PYTHONUTF8/encoding；所有新增 fixture、日志和子进程 decode/write 明确 UTF-8。

| 命令 / 集合 | 结果 | 证据 |
| --- | --- | --- |
| `E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_terminal_report_diagnostic.py tests/test_report_error_context.py tests/test_report_subscription.py tests/test_report_reads.py tests/test_manual_task_report.py tests/test_session_error_log.py tests/test_session_error_log_worker.py tests/test_force_steer.py tests/test_self_queue.py tests/test_auto_force_sources.py tests/test_usage_enrichment_commit.py -o addopts= -q -rs --junitxml=audit/terminal-report/python.xml` | **exit 0，332 passed，2 skipped** | `python.log/xml` |
| `E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_terminal_report_http.py -o addopts= -q --junitxml=audit/terminal-report/http.xml` | **exit 0，1 passed** | `http.log/xml`、`http/` |
| `E:/software/miniforge/python.exe -m compileall -q packages/core/worker.py packages/core/session_error_log.py tests/test_terminal_report_diagnostic.py tests/test_terminal_report_http.py tests/support/session_error_fake_cli.py` | **exit 0** | `compile.log`、`commands.json` |
| `git diff 140455976b7c60097fafdd4dc3f8e3b18a699ccc HEAD --check` | **exit 0** | `diff.log`、`commands.json` |

2 项既有 skip 均为用户已退役 Oneshot 模式的保留测试，不是本批新增 skip。2 项 warning 为既有 Pydantic unresolved forward reference、JUnit xunit2 与 record_property 不兼容提示。无新增 skip/xfail、弱化断言或忽略产品异常。

本批另保留一次实现中间失败：源码 `573841cce8148afaff9946b32461e6d7d03b0a1f` 的组合回归 **exit 1，6 failed / 326 passed / 2 skipped**。共享 provenance 函数名与日志局部变量同名，日志写入出现 TypeError；已在 `3ba59d25` 改名修正，原断言保留，同一集合最终全绿。见 `normalizer-failed-python.log/xml`、`normalizer-failed-commands.json`，不将该版本作为候选。开发期还纠正了测试 helper 名称、不同 asyncio.run 间的测试锁复用和 Queue 摘要 API 的断言路径，未改产品权限投影来迎合测试。

新增定向回归覆盖：真实样本 error+assistant result（CBC 和实际 Codex adapter）、空诊断保留 error、code-only/未知对象、前次任务错误不复用、凭据 raw 裁剪与有界文本、日志等待屏障前冻结、不同 Session 并发、迟到旧 generation 拒绝、持久 report 冷加载、delivery key 兼容，以及实际 batch/Steer/自动 Force 的格式化入口。既有自动/手动 report、未知 receipt、Pause/Illegal/lease、usage commit 回归同步通过。

## 隔离真实 HTTP/MCP/fakeCLI 闭环与清理

`http/evidence.json` 的受测源码为 `3ba59d25`，自有随机端口 **63712**，临时 profile。真实 stdio MCP report_subscribe → agent_assign → 本地 fakeCLI 发出 fixture 原生 terminal error → 真实 Worker 持久 error 终态和 manager report。manager 暂停以检查持久 report；随后停止自有实例，将其 JSONL 移到同一临时根的 .saved，再冷启动，不提供原 JSONL；report/lastResult 仍含同次 native diagnostic。

解除 manager Pause 后，真实 Queue 把报告交给 manager fakeCLI；`http/delivered.txt` 是实际接收文本，含 status:error、errorSource:codex_error_payload、errorCode:serverOverloaded、原生 capacity 文本和独立的合成 lastMessage。不是只断言格式化函数或返回入队成功。该流程不触发真实账号恢复/模型切换，也不调用真实 provider。

根进程/两代服务/自有子进程按 retained PID + 出生时间核对。finally 清理结果：`ownedProcessesRemaining=[]`、`portReleased=true`。不扫描杀死其他 Worker，不触碰 8767/8768。临时 profile 仅归 pytest 所有，不迁移或写入真实 profile。

## 未验证边界

- 未运行真实 Codex app-server/远程模型、真实容量恢复或用户 Worker；真实日志字段 + 本地 Codex adapter 回归不冒充真实 provider 新请求。
- 无 React/TypeScript 修改，本轮未跑前端 build/浏览器，也未扩展 Queue UI；验证的是 TA→MA 实际消费文本的 batch/Force/Steer 交界。
- 旧 terminal/report 不扫描日志回填；缺诊断继续明确为空，历史 report 保持兼容。
- 保留 Oneshot 路径接入共用 terminal diagnostic，但已退役模式的两项既有 skip 保留，不声称实测了退役启动链。
- 未跑仓库全量、未部署、未声明 MA/真人验收。完成 clean 候选后停止等待独立验收。

日志/JUnit 可读副本仅规范行尾空白；`raw-evidence.zip` 保留原始字节（包括红测与中间失败），断言/错误/计数均未修改。
