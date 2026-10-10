# Codex Session reimport origin 修复 — TA 验收交付

日期：2026-10-10。实现分支 `fix/codex-reimport-origins-20261010`。
基线 `c237311902d6af5913ef0e8382f90e44efe7110c`。
源码提交 `ae36fe1d3a05d2722797e155179a6063cc7163d8`。
报告和证据随后独立提交；交付 HEAD 以 TA 最终消息的完整 SHA 为准。

## 根因与调用链

前端 Session reimport → 通用 `/api/adapters/codex/sessions/import` →
`api_adapter_sessions_import` → `_import_session` → `_import_session_locked` →
Codex provider `parse_history` / `parse_codex_history`（rollout 优先）→
`assign_pan_message_ids(previous_history=完整持久历史)` → queue origin 无损保护 →
history/usage 全量持久化。

错误原文：`Native reimport cannot match all Pan queue origins losslessly; existing history retained`。
这条保护判断保留，未将来源统一改为 user。

Pan 投递时由 `project_message_parts()` 将附件 parts 中的内部 `__serverPath`
转换成 `@"绝对路径"`；Codex rollout 记录收到的这段正文。Pan 自身的 history
`content` 则使用浏览器可读的 `/api/attachments/...` Markdown 链接；完整 parts
另行保留路径和附件信息。原匹配器只比较 role/content，无法匹配两种等价投影。
FIFO `_deliver_queue_unit` 和 queue steer 均使用附件投影，历史 receipt 均保存
展示正文及来源元数据，所以两种路径都受影响。

只读检查使用 `.history.jsonl` 作为 Pan 完整历史来源，不能用主 JSON 的 20 行
尾部代替完整账本。实际附件快照有 10 条 `response_item/message` 和 38 条
`event_msg/item_completed`，是同一消息的并行表示；现有 parser 优先 native
completed 表示并保持其顺序，未在本次改动中
重写 parser。发现的某些重复/缺失来源仍无法证明，本修复不强行配对。

## 修复范围

- `packages/core/attachment_projection.py`：历史重建允许跳过文件存在性检查；
  实际投递缺省仍要求附件文件存在。历史匹配不会读取附件内容。
- `packages/core/session.py`：仅对有 queue envelope 且 parts 完整的记录，重建
  精确投递正文作为指纹。路径必须是绝对路径；未知/缺损 parts 不推测。
  原正文、native 顺序不改。匹配后保留 Pan messageId、taskId、来源、完整
  queueEnvelopes、delivery keys、channel/envelope 和附件 parts。
  稳定 native ID 也不能覆盖已编辑 origin 正文；重复正文仍需等量、有序证据。
- 同文件持久化 helper：history、usage、cursor、lastResult 在同一 Session ticket
  中提交。执行前校验 history revision；过期候选明确失败。保存异常回滚内存和
  原 JSON/JSONL 字节；取消等待者不误回滚已经成功的落盘。历史 replacement 持有
  summary lock，防止保存/回滚期间 append 穿越，普通 usage 保存不增加此锁。
- `packages/web/server.py`：保留 queue origin 保护，只将有效 Pan ID 计入匹配集；
  history revision 冲突返回明确错误。保存 OSError 继续抛出，HTTP 为 500。
- `tests/test_codex_reimport_origins.py`：23 个新增判别用例。

## 实测证据

1. 基线 `git archive` 私有目录运行同一新增测试文件：**10 failed / 13 passed**，
   退出 1，0 skip。`baseline.log` / `baseline.xml`。
   失败覆盖附件 user/agent/automation、重复附件正文、已编辑 origin、保存失败、
   并发追加、取消，以及实际 FIFO/steer receipt 到 reimport 的路径。
2. 候选新增回归：**23 passed**。涵盖上述修复及 report 批次、system notice、
   QQ/WeChat channel 元数据、多文本块/结构化正文、普通旧历史、新 native 消息、
   缺失 origin、native ID 不符、缺少路径/ID、重复 reimport 幂等。
3. 相关组合回归：**290 passed / 0 failed / 0 skipped**，退出 0，22.72 秒。
   `final-related.log` / `final-related.xml` / `final-exit.json`，源码锚定上述 SHA。
4. 真实症状私有快照：只读复制某实际 Session 的 rollout 和完整 Pan 历史到
   系统临时目录，所有 reimport 写入新的临时 Session 目录。基线匹配缺少 1 条
   origin；候选 HTTP handler 返回 200 / reimported=true，37 行旧历史更新到
   38 行 native 历史；磁盘重读仍为 38 行，2 条 origin 的原 ID 全部保留，所有
   role/content 顺序与 native 相同。私有 rollout 字节未变。
   无正文的证据见 `private-http-result.json`；原始用户正文和快照未提交。

## 可复现命令

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_codex_reimport_origins.py -o addopts= -q
python scripts/run_tests_isolated.py -- python -m pytest tests/test_codex_reimport_origins.py tests/test_session_import.py tests/test_history_delivery_identity.py tests/test_attachment_parts_protocol.py tests/test_unified_server_queue.py tests/test_usage_enrichment_commit.py tests/test_usage_enrichment_native.py tests/test_codex_adapter.py tests/test_session_incremental.py tests/test_session_summary_projection.py tests/test_delivery_semantics.py tests/test_worker_branch.py tests/test_steer_history_consistency.py -o addopts= -q
git diff --check
git merge-base --is-ancestor c237311902d6af5913ef0e8382f90e44efe7110c HEAD
git merge-base --is-ancestor main HEAD
```

完整实际命令、源码 SHA、退出码存于 `final-exit.json`。另按依赖隔离要求运行：

```powershell
python scripts/run_tests_isolated.py -- uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest tests/test_codex_reimport_origins.py tests/test_session_import.py tests/test_history_delivery_identity.py tests/test_attachment_parts_protocol.py tests/test_unified_server_queue.py tests/test_usage_enrichment_commit.py tests/test_usage_enrichment_native.py tests/test_codex_adapter.py tests/test_session_incremental.py tests/test_session_summary_projection.py tests/test_delivery_semantics.py tests/test_worker_branch.py tests/test_steer_history_consistency.py -o addopts= -q --junitxml=audit/codex-reimport-origins/isolated-related.xml
```

**290 passed / 0 failed / 0 skipped，退出 0，31.99 秒**。依赖在 uv 私有环境安装，
共享安装未修改。证据为 `isolated-related.log` / `isolated-related.xml` /
`isolated-exit.json`；仅余既有 pydantic warning。

测试入口去除继承 PAN
环境，conftest 隔离应用配置和数据目录；ASGI HTTP 集成没有监听真实端口。
Python 3.14 已安装依赖仅用于读取/执行，没有改共享安装。其 pytest-timeout 缺失
导致配置警告，另有既有 pydantic lifespan forward-reference 警告；没有 skip。

## 边界和 MA 接手

未访问写入式真实 reimport，未测试/重启 8767/8768，未修改 native 用户数据，
未 push/发布/推进 main/practical，未 stash/reset。未运行前端 build；build 与
快进由 MA 独立验收后进行。最终检查 main/practical 均仍为指定基线，基线及
main 是 feature 祖先，无需合入更新 main。所有修复保留在原 feature 分支。

HTTP 集成为实际 ASGI 路由加私有 provider 数据，没有调用真实 Codex 内核或模型；
FIFO/steer 测试模拟权威 receipt，不声称真实 provider 收到消息。未验证浏览器、
长稳、大历史性能、跨平台附件路径或掉电/进程崩溃时两个文件的事务原子性。
本次证明的是进程内保存异常恢复与取消/并发保护；磁盘自身无法写入 rollback
时会继续抛错，不能声称损坏磁盘也可恢复。缺少必要证据的旧 origin 仍明确拒绝。

完成后以本 TA 的正常终态报告走已配置 report 订阅回 MA；无额外消息投递。
