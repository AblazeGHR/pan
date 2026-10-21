# Codex branch / paginated lineage 修复候选

工作树 `D:/project/pan-worktrees/codex-branch-lineage-20261021`，分支
`fix/codex-branch-lineage-20261021`，基线
`18261dabce8981c4854c87c7898756d4e81a7c7b`。本交付等待 MA 独立验收；未移动
main/practical，未 push、release、构建前端或操作既有服务。

## 根因与复现

用户报错线程 `b871d594-6da5-44d8-a876-e755daf129f6` 仅用于只读元数据定位。
没有复制、输出或提交真实 rollout 正文、auth/config、用户凭据或用户数据库。
其父线程的当前 rollout 文件名以 `_01a158b5-9775-7690-86c2-9ccaf70f21c4`
结尾，逻辑 thread ID 是 `01a15527-346e-7271-b949-1c1d97f0da4a`。
父 metadata 的 history_base 指向后一个 ID。这种逻辑上的同 ID 引用合法：
它引用的是旧的物理 rollout，并非当前选中的物理 rollout。

本机 `@openai/codex 0.163.0` 原生 `thread/revert` 可以稳定生成这一状态：
逻辑 ID 不变、物理 rollout ID 改变、history_base 保留旧物理前缀。
原 Pan fork 全文 `replace(parent_id, new_id)` 将 history_base 一起改成新 ID，
且新文件物理 ID 恰好也为新 ID，于是原生 CLI 解析物理谱系时发现真正自循环。
该错误字符串来自 CLI 的 thread-store，而非 Pan 自己构造的启动错误。
旧全文替换还会改变 session-tree root、正文中的 UUID 或其他同值标识。

官方协议说明 [thread/revert 与 thread/fork](https://learn.chatgpt.com/docs/app-server)
以及安装版本对应的
[物理谱系解析源码](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/thread-store/src/local/rollout_lineage.rs)、
[当前 rollout 解析源码](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/thread-store/src/local/thread_rollout_resolver.rs)
与本地实测一致。不能仅检查 `history_base.thread_id == thread.id` 判循环，也不能
删除 history_base 或吞掉 cycle 错误。

## 修改

- branch 改写 `session_meta.id`、设置 `forked_from_id` 为直接父线程，并改写明确属于父线程的 `event_msg.thread_id`。
  保留 history_base、session_id 根身份、正文、turn/item/window ID。未修改的记录
  保留原始 bytes；不再对正文做 UUID 替换。
- 分页 branch 不复制 SQLite 投影缓存。新文件 metadata 序列化会改变本地 byte offset，
  CLI 应从新 rollout 和冻结的祖先前缀重新投影。legacy 的 index-only fallback 保留。
- Pan history/usage 按物理 rollout ID 遍历 history_base，在原始 UTF-8 bytes 与
  ordinal 截断边界内读取祖先，然后读取本地后缀；SQLite 缓存不是第二份可叠加历史。
  相同正文但不同原生消息身份保留；原生 completed/response 两种表示仍按既有规则投影。
- 真正的物理自循环、多节点循环、缺失/歧义祖先、越界/非记录边界 cursor 明确失败。
  失败不回退到陈旧缓存；空的合法分页历史也不从缓存复活旧消息。
- fork 先验证谱系，再创建子状态；rollout 写失败回滚本次未提交状态。
  修正旧 provider 测试的 `_CODEX_DIR` 隔离，避免该测试向真实 profile 写 fork 文件。

## 证据

修前使用独立临时 CODEX_HOME、本地有限 SSE 合成 provider 和真实 CLI：
`thread/start(paginated)` → 两个完成 turn → `thread/revert` → Pan provider fork →
`thread/resume(excludeTurns=true)`。原生返回完全相同的
`invalid paginated history lineage ...: cycle detected`，pytest exit 1。
[修前 JUnit（字节无损 gzip）](../../evidence/codex-branch-lineage/before.xml.gz) 保存该失败及当时复现用例；
原始 XML 的 traceback 含行尾空格，因此压缩提交以保留原始证据并通过 diff whitespace 检查。

最终命令（exit 0）：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_codex_branch_lineage.py tests/test_codex_adapter.py tests/test_codex_session_conversion.py tests/test_codex_resumed_init.py tests/test_codex_large_resume.py tests/test_codex_reimport_origins.py tests/test_worker_branch.py tests/test_session_import.py -q -o addopts= --junitxml=evidence/codex-branch-lineage/after.xml
python -m compileall -q packages/core/adapters/codex/sessions.py tests/test_codex_branch_lineage.py tests/test_codex_adapter.py
git diff --check
```

[修后 JUnit](../../evidence/codex-branch-lineage/after.xml)：181 passed，0 failures，
0 errors，0 skipped。测试环境为 Python 3.14.5 / pytest 9.1.1。
有两条环境 warning：未安装 pytest-timeout 时的既有 timeout 配置，以及既有
pydantic `lifespan` forward reference；不将 warning 或缺失插件记作测试通过能力。

新增 10 项用例包含一个真实 CLI 集成用例：三个本地合成 turn → revert 最后一个 →
生产 import/branch handler → 原生分页（limit=1，连续 nextCursor 无重复）→ 再 branch →
原生 fork → 损坏副本拒绝/恢复 → 释放自有 writer → 实际 Pan create_worker → wrapper
原生 Resume ready → kill 自有 Worker。保留的两个相同 user 内容均包含源 UUID，
断言正文不被改写且撤回的第三条不复现；源 rollout bytes 前后不变。
本地 provider/CLI/Worker 均在 finally 关闭；未调用收费或远端模型。
测试 harness 使用随机本地端口，不监听 Pan 服务，不使用 8767/8768。

## 已有受影响分支的安全恢复

本候选不会自动修改任何已有 Session 或 CLI 文件。优先方案是：在原生父线程仍健康、
且能够证明该分支尚未新增独立 turn 的情况下，使用修复后的 branch 重新建立分支，
核对原分叉点的 turn/item 序列及内容后由用户决定切换。若父线程在分叉后已有新内容，
必须用已知分叉边界（例如原生 fork 的 lastTurnId），不能直接复制父线程最新历史。

需要保留旧分支 ID 或独立后缀时，先停止其自有 writer，并在自有临时 profile 中复制
所需 rollout/DB；不复制真实 auth/config，设置本地合成 provider。必须取得分叉时的
父物理 rollout 及其 metadata/cutoff 的可靠证据。只允许按证据恢复误改的引用和身份，
保留 child.id、祖先 cutoff、独立后缀；绝不能简单把 child UUID 全文换回 parent UUID。
旧实现可能也改过正文，缺少原始快照或可逐项核对的来源时不能声称能无损自动修复。

新增真实 CLI 用例对“仅 base 指向自身”的合成损坏副本证明了：CLI 和 Pan 都明确拒绝，
恢复已保存的正确 metadata 后可 Resume，并与原两条历史逐项相等。它证明窄恢复机制，
不证明真实用户旧分支的全部正文已经恢复。实际用户线程此次未被修改，恢复上线必须
另获单分支授权，并保留备份、变更前后差异与完整历史比对结果。

## 验证边界

实测的是本地 CLI、handler、分页协议与 Worker 进程链；不是浏览器点击/完整 HTTP 服务 E2E。
未运行全量测试、前端构建、远端模型、实际用户分支恢复、压缩 `.jsonl.zst` 祖先、并发
revert/归档/重命名期间的 fork 竞态。当前读取仅支持可定位的普通 JSONL 祖先；缺失或歧义
来源明确失败。未改变共享依赖；未操作他人进程、既有服务或受保护端口。

## MA 独立验收（2026-10-21）

已审阅 ownership 字段修改、物理谱系读取、冻结祖先边界、重复正文和损坏谱系拒绝路径。
合入 main e4f718a5 的角色文档后，在整合树使用 canonical Python 独立执行上述同一组八文件回归：181 passed、0 failed/error/skipped，exit 0，耗时 6.59 秒；仅既有 Pydantic forward-reference warning。
证据：evidence/codex-branch-lineage/ma-after.xml。包含自有本地合成 provider 与真实 Codex CLI/Worker Resume，无收费模型。
验收通过本次代码范围；已有用户受损分支未修改，恢复仍须按前述条件逐项核对。用户级 skill 按当前要求未同步。
