# Pan rewind 功能：PTY 方案调研与实现报告

> 范围：一期只做后端、只支持 cbc adapter、不动前端。
> 工作树：`E:\Pan_worktree\pan_dev`，分支 `dev`。
> 报告日期：2026-09-29。

---

## 1. 目标

用户要的 rewind 语义是：**对话 + 文件一起回滚到某个检查点，用户重新编辑那条消息，之后对话从那个点分叉继续**。

用户拍板的六项决策：

1. 文件回滚走 **Pan 内建 PTY + 虚拟终端驱动 CLI 原生 rewind**，不搞 Pan 自研快照/git 版本管理；
2. 尽量**保留原会话**；
3. 先做**可行性探针**；即便可行，PTY 也**只在 rewind 时临时启用**，正常任务仍走现有 pipe 通道；
4. 检查点粒度：**每条 user 消息自动一个检查点**；
5. 一期只做后端；
6. 先只做 cbc，跑通再扩。

硬约束：**不许修改 `packages/core/session.py` 与 `data/sessions/<id>.json(.history.jsonl)` 的既有数据结构与字段**（新增方法/模块可以，改既有字段不行）。

---

## 2. 结论速览

| 问题 | 结论 |
|---|---|
| PTY 能否驱动 cbc TUI | 能；spawn ~5.2s，两种启动方式均可 |
| cbc 有没有原生 rewind | 有：`/rewind` 斜杠命令，或空输入框连按两次 `Esc` |
| **文件是否真回滚** | **是**（3/3 实测） |
| 对话回滚能否交给 cbc | **不能**（分叉产物不落盘，见 §6） |
| 最终架构 | **混合方案**：cbc 原生 rewind 管文件，Pan 自己截对话 |
| 端到端耗时 | 混合流程 ≈11.4s（fork 5.0s + PTY 回滚 6.3s + 截断 0.035s） |
| 是否异步 | 是，后台任务 + WS 进度 |

---

## 3. 改造前的现状（调研结论）

### 3.1 Pan 侧：零 PTY 基础

- 正常任务全部是 `asyncio` subprocess + pipe：
  - `packages/core/worker.py:5722` stream 模式 spawn（stdin/stdout=PIPE、stderr=STDOUT、cwd=s.workdir，常驻 `--input-format stream-json`）
  - `packages/core/worker.py:4824` oneshot 模式 spawn（无 stdin、prompt 作末参）
  - `packages/core/worker.py:4650-4655` `adapter.encode_user_message` → stdin.write + drain
  - `packages/core/worker.py:4939` `parse_event` 结构化解析
- history 落盘、taskId 幂等、usage 统计**全部依赖这条结构化事件链** ⇒ PTY 绝不能介入它。
- 全仓库 `pty` / `winpty` / `conpty` / `pexpect` / `pyte` / `tmux` 零命中，依赖里也没有。

### 3.2 Pan 侧：可复用的现成能力

| 能力 | 位置 |
|---|---|
| 锚点解析（支持 `msg_*` 与 `legacy:{sid}:{epoch}:{index}`，legacy 强制校验 epoch） | `packages/web/server.py:5685-5708` |
| busy 门禁（worker running/queued 时拒绝） | `packages/web/server.py:5714-5719` |
| branch 模板（fork → parse_history → get_raw_usage → create → broadcast） | `packages/web/server.py:6354-6438` |
| fork provider 协议 | `packages/core/adapters/base.py:202` |
| 命名去重（要求调用方持 `_STORE_LOCK`） | `packages/core/session.py:1506` `_available_name` |
| 锁装饰器 | `packages/core/session.py:174` `_store_serialized` |
| 旁路落盘先例 | `packages/core/background_jobs.py:57-68, 231-256`、`packages/core/workspace.py:78-85` |

### 3.3 cbc 侧：原生 rewind 确实存在（官方文档实证）

取自本机安装包 `<npm-root>/node_modules/@tencent-ai/codebuddy-code/dist/web-ui/docs/`
（**注意：文件名含字面量 `%2F`，是扁平文件不是目录**，如 `cn%2Fcli%2Fcheckpointing.md`）：

- `/rewind`：「回退对话到之前的某个消息点，可选择**仅回退对话 / 仅回退代码 / 同时回退两者**」⇒ 用户要的「对话+文件一起回滚」是原生支持的第三种。
- 触发方式：TUI 内 `/rewind`，**或输入框为空时连按两次 `Esc`**（后者对 PTY 驱动远比模拟菜单点击可靠）。
- `/fork [name]`（`interactive-mode.md` / `slash-commands.md`）：在当前对话位置创建分支、复制历史到新 session。
- 限制（`checkpointing.md`）：只跟踪 **cbc 自己的文件编辑工具**造成的改动；**bash 命令（`rm`/`mv`/`cp`）改的文件不被跟踪**；外部手动修改不捕获；它是「本地撤销」，不是版本控制替代。

### 3.4 一个 Windows 特有的坑

`cbc` 在 Windows 上是 npm 的 **PowerShell shim**（`cbc.ps1`）。实测两种 spawn 都能起 TUI（各约 5.2s），但直接 spawn `node.exe + bin/codebuddy` 时命令面板输入有竞态，**cbc shim 更稳定**。

---

## 4. PTY 探针：三阶段实测

探针脚本：`scripts/rewind_pty_probe.py`；证据落在 `evidence/rewind_probe_*.json`。

### Stage 1：可达性 ✅

`pywinpty 3.0.5` + `pyte 0.8.2`（+ `psutil 7.2.2`）全部可用；cbc shim 与直接 node 两种方式均能启动 TUI，约 5.2s；`Ctrl-C → terminate(force=True)` 可正常回收。

### Stage 2：TUI 内确有 rewind 入口 ✅

发送 `/rewind` 后屏幕显示：

```
/rewind     Restore the code and/or conversation to a previous point (Beta)
```

### Stage 3：真实 rewind ✅（中途卡过一次，绕过方式见下）

**卡点**：把 prompt 文本**提交**给 TUI 不通（CR / LF / Ctrl+J 三种都不行，文本停在输入框）。
**排查**：`/terminal-setup` 只给 iTerm2 / VSCode / Ghostty / Windows Terminal 等真实终端装 Shift+Enter 绑定（改的是终端配置），**对裸 ConPTY 无效**。
**绕过**：Stage 2 已证明「打字」链路通（`/rewind` 能进输入框并触发命令面板过滤），坏的只有提交 ⇒ **改为完全不在 PTY 里提交 prompt**：用 headless `-p --session-id <uuid>` 非交互造料，PTY 只负责 resume 之后的操作；而 `Esc Esc` 的触发条件正是**输入框为空**，天然不需要提交。

**最终跑通的按键序列与界面原文**（`evidence/rewind_probe_capture.txt`）：

```
Esc Esc  →  ArrowUp  →  Enter  →  Enter  →  1
```

```
Rewind
Restore and fork the conversation to a checkpoint:
  rewind_probe_file.txt +1 -0 · 23s ago
Confirm you want to restore to this checkpoint:
│ <锚点处那条用户消息的原文>
The code will be restored +1 -0 in rewind_probe_file.txt.
❯ 1. Restore code and conversation
  2. Restore conversation
  3. Restore code
⚠ Rewinding does not affect files edited manually or via bash.
```

**锚点可精确定位**：界面显示该条用户消息原文 + 改动文件与增删行 + 相对时间 ⇒ 可按消息文本匹配定位，不是盲按方向键。

---

## 5. 耗时剖析与优化

`scripts/rewind_timing_probe.py`，证据 `evidence/rewind_timing_*.json`。

**优化前：约 47.5s。优化后：完整流程 14.7–17.7s，纯 PTY rewind 路径 6.4–6.6s。**

| 阶段 | 优化后耗时 |
|---|---|
| Headless 造料 | 8.3–11.3s |
| PTY spawn → TUI 首屏（**真实瓶颈，不可压缩**） | ~5.5s |
| 首屏 → 输入框可用 | ~0.01s |
| `Esc Esc` → rewind 菜单 | ~0.1s |
| `ArrowUp` → 定位检查点 | ~0.01s |
| `Enter` → 确认页 | ~0.06s |
| 选 `1` → restore 完成 | ~0.15s |
| PTY 回收 | ~0.3s |

**原来的 47 秒大头是探针自己的固定等待，不是 cbc**。生效的优化点：

- 固定 sleep 改为 **50ms 轮询 + 屏幕特征串判定**；
- 去掉「连续多次屏幕完全相同」的稳定检测；
- restore 完成改为「目标文件状态 + 主界面恢复」判定，不再等超时；
- PTY 回收独立计时并保留强杀兜底；
- 全部结果写 UTF-8 JSON（Windows 控制台是 GBK，print 含 `❯` 的屏幕会 `UnicodeEncodeError` 直接崩）。

---

## 6. 关键阻塞：rewind 的分叉产物不落盘

E2E 出现矛盾：`file_rolled_back = true`（文件回滚成功），但副本 transcript 前后 sha1 完全相同、无新 session 文件。屏幕却显示 TUI 已回到全新会话（`Tips for getting started` / `No recent activity`）⇒ **对话回滚在内存里发生了，但没落盘**。

随后专门验证（证据 `evidence/rewind_fork_semantics.json`、`evidence/rewind_persist_result.json`、`evidence/rewind_probe_fork_result.json`）：

1. **`--fork-session` 命令行成立 ✅**：原 session A（`pan_fork_verify_1790654468`）sha1 未变、4→4 行；新 session B（`01a0eb52-…`）7 行，前 4 行与 A 完全一致（历史前缀完整复制）。
2. **rewind 自带的分叉不落盘 ❌**：四组实验副本 B 均 13→13 行、sha1 不变、`new_session_files=[]`。
3. **进程无法优雅退出 ❌**：`/exit`、`Ctrl+D`、PTY EOF 都不能让进程自然退出；且 `/exit`/`/quit` 会被 rewind 预填的锚点消息拼成新的用户输入（危险）。

⇒ **对话回滚不能交给 cbc**。

---

## 7. 最终方案：混合架构

```
                ┌─ 原 Pan session / 原 cbc session A：全程不动 ─┐
                │                                              │
① fork          cbc --resume A --fork-session  ──►  持久化副本 B（新 cli_session_id）
② 文件回滚      PTY 启动 B 的 TUI，Esc Esc → 定位锚点 → 选 1（cbc 原生 rewind）
                └─► 磁盘文件回到锚点状态；TUI 内存里的 fork 丢弃（反正不落盘）
③ 对话截断      Pan 重写 B 的 transcript JSONL，只保留锚点及其之前的行
④ 重建          parse_history(B) + get_raw_usage(B)（按截断后重算）
⑤ 落库          单段式 create 新 Pan session（cli_session_id = B）→ broadcast session.created
```

要点：

- **分工**：cbc 原生 rewind 只负责**文件回滚**；**对话截断由 Pan 自己完成**。
- **保留原会话**：① 保证 A 的 transcript 与 Pan 侧数据全程不被改动；产物是新 Pan session。
- **PTY 只在 ② 临时启用**，不碰 `worker.py` 的 stream/oneshot/stdin/`parse_event` 任何一处。
- **检查点不用 Pan 自己做**：cbc transcript 里自带 `file-history-snapshot` / `trackedFileBackups`，天然是「每条 user 消息一个检查点」；Pan 侧只旁路维护「Pan 消息 ↔ cbc 检查点」映射。

---

## 8. 实现清单

### 8.1 新增模块 `packages/core/rewind/`

| 文件 | 职责 |
|---|---|
| `driver.py` | PTY 驱动：spawn TUI（pywinpty + pyte）、等待可交互、`Esc Esc`、按锚点文本匹配导航检查点、进入确认页选 `1`、完成判定、`\x03` → `terminate(force=True)` → `psutil._kill_tree()` 三段式回收 |
| `transcript.py` | `truncate_transcript()`：锚点优先级 `message_id → message_text → absolute_index`；保留锚点及之前的行、丢弃其后全部（含 tool/thinking/snapshot，避免孤儿行）；只允许写 `~/.codebuddy/projects/**/<expected_session_id>.jsonl`；临时备份 + tmp + `os.replace` 原子写，失败回滚备份；找不到/歧义/越界/非 user 消息一律明确抛错 |
| `hybrid.py` | `run_hybrid_rewind()`：串联 fork → PTY 文件回滚 → 截断 → `parse_history` → `get_raw_usage`；阶段状态 `starting / resuming / rewinding-files / truncating / completed / failed`；只支持 cbc，其他 adapter 抛 `NotImplementedError` |
| `storage.py` | rewind 记录旁路落盘（一 job 一 json + 文件锁 + 原子写），目录 `<DATA_DIR>/rewind` |

### 8.2 HTTP 与 WS（`packages/web/server.py`）

- `POST /api/sessions/{session_id}/history/{message_id}/rewind`
  - 复用 `server.py:5685-5708` 的锚点解析（含 epoch 校验），不新写；
  - 复用 `server.py:5714-5719` busy 门禁（running/queued 拒绝，返回 `message_not_rewindable`）；
  - role 门禁：只允许 user 消息作锚点；
  - **异步**：返回 `job_id`（`rewind_<hex>`）+ `stage: starting`，不阻塞 11 秒。
- WS 事件 `session.rewind.progress`，携带 `jobId / stage / status / error / newSessionId / limitation`。
- 完成后按 branch 模板**单段式** `create` 新 Pan session：`cli_session_id = B`、history 取自 `parse_history(B)`、usage 用 `get_raw_usage(B)` 重算（不沿用父级）。

### 8.3 两个实现细节（坑）

1. **重名**：branch 只在名字为空时兜底 `f"{s.name}-branch"` 后直接 `_check_session_name`，重名是**报错而非去重** ⇒ 同一锚点回滚两次会撞名。修法：`session.py` 新增
   ```python
   @_store_serialized
   def create_with_available_name(name: str, **kwargs) -> Session:
       '''Create a Session after atomically resolving a duplicate-free name.'''
       kwargs['name'] = _available_name(name)
       return create(**kwargs)
   ```
   用 `@_store_serialized` 持 `_STORE_LOCK`，避免在 `server.py` 里抓私有锁（锁序契约 `session.py:63-70`：`_STORE_LOCK → per-session`，反向会死锁）。**只新增方法，未改任何既有字段。**
2. **provider 调用**：走 `asyncio.to_thread`（照抄 `worker.py:6019-6031`），不照抄 `server.py:6383` 的裸阻塞调用。

### 8.4 数据与依赖

- 新落盘位置已登记 `packages/core/data_catalog.py`，删除级联已加入 `packages/web/server.py:6592`。
- `minimal-requirements.txt` 新增运行时依赖 `pywinpty==3.0.5`、`pyte==0.8.2`。
- 血缘一期用**命名约定**承载（如 `rewind-api-parent@0`、二次回滚 `rewind-api-parent@0-1`），**未新增** `forkOf`/`parent` 字段。

---

## 9. 改动清单

```
修改（5 个文件，328 insertions / 13 deletions）
  packages/web/server.py        +319   端点 / WS / 建会话 / 删除级联
  packages/core/session.py       + 7   仅新增 create_with_available_name
  packages/core/data_catalog.py  + 3   新存储登记
  minimal-requirements.txt       + 2   pywinpty / pyte
  manifest.json                 10±    用户 9/27 的既有改动（本次未触碰）

新增（未跟踪）
  packages/core/rewind/                driver.py / transcript.py / hybrid.py / storage.py / __init__.py
  scripts/rewind_pty_probe.py          阶段 A 探针
  scripts/rewind_timing_probe.py       耗时剖析与优化
  scripts/rewind_fork_semantics.py     --fork-session 验证
  scripts/rewind_persist_probe.py      分叉是否落盘验证
  scripts/verify_cbc_fork_session.py
  scripts/verify_rewind_driver.py
  scripts/verify_rewind_hybrid.py
  scripts/verify_rewind_api.py
  tests/test_rewind_transcript.py
  tests/test_rewind_api.py
  evidence/*.json                      40 个证据文件
```

**未触碰（硬约束）**：`packages/core/session.py` 既有数据结构与字段、`data/sessions/<id>.json(.history.jsonl)` 既有结构、worker 通信链、`packages/web/src/**`（前端）、`manifest.json`。

---

## 10. 验收结果

### 10.1 混合流程 E2E（`evidence/rewind_hybrid_e2e.json`）

| 断言 | 结果 |
|---|---|
| 文件回滚（目标文件 rewind 后不存在） | ✅ |
| 原 session A 未变（sha1 `82060b07…`，10→10 行） | ✅ |
| 副本 B 被截断（13→1 行） | ✅ |
| B 止于锚点（末行是含 `PAN_REWIND_HYBRID_MARKER` 的 user message） | ✅ |
| `parse_history(B)` 返回截断后历史（1 条） | ✅ |
| `get_raw_usage(B)` 重算（B=0 条，父 A=3 条，未沿用） | ✅ |
| PTY 干净回收、无残留 | ✅ |

耗时：fork 5.012s + PTY 文件回滚 6.309s + 截断与重算 0.035s ≈ **11.361s**（含 headless 造料的脚本总计 22.212s）。

### 10.2 API 端到端（`evidence/rewind_api_e2e.json`，`status: success`，66.566s）

```
file_rolled_back: true              parent_history_unchanged: true
new_sessions_created: true          parent_files_unchanged: true
cli_ids_are_new: true               names_deduplicated: true
histories_end_at_anchor: true       original_cbc_transcript_unchanged: true
usage_recalculated: true
```

两次回滚分别产出 `ses_8aac9d358d47bb06`（`rewind-api-parent@0`）与 `ses_23d46991ef5076c9`（`rewind-api-parent@0-1`），`cliSessionId` 各自不同；父会话 `data/sessions/ses_44631e6d05d9d58e.json`（sha1 `58d26c7b…`）与 `.history.jsonl`（sha1 `465c4ca8…`）前后完全一致；原始 cbc transcript（`253a019d…`，11 行）未变。

### 10.3 测试

`python -m pytest tests/test_rewind_api.py tests/test_rewind_transcript.py -q` → **6 passed**。

### 10.4 复现命令

```bash
py scripts/verify_cbc_fork_session.py    # --fork-session 可行性
py scripts/verify_rewind_driver.py       # PTY 驱动核心
py scripts/verify_rewind_hybrid.py       # 混合流程（fork → 文件回滚 → 截断）
py scripts/verify_rewind_api.py          # HTTP 端点 + WS + 建新 Pan session
python -m pytest tests/test_rewind_api.py tests/test_rewind_transcript.py -q
```

---

## 11. 已知限制与后续

### 11.1 能力边界（必须让用户看见）

- **只回滚 cbc 自己文件编辑工具造成的改动**；bash 命令（`rm`/`mv`/`cp`）与手动编辑的文件**不会回滚**。该限制已作为 `limitation` 字段写进 rewind 记录与每条 WS 事件。
- rewind 只支持 **cbc**；claude / codex / kimi / opencode 目前抛 `NotImplementedError`。
- 回滚产物**不会立即拉起 worker**：与 branch 行为一致，首次发送时 spawn 会因 `cli_session_id` 非空自动带 resume 参数续接新原生会话，这是预期行为。

### 11.2 既有失败测试（与本次改动无关，未修）

全量 `python -m pytest tests/ -q` 仍有 17 个失败，集中在 `test_backend_perf_opt.py`、`test_config_reload.py`、`test_launcher.py`、`test_main_lifecycle_jobs.py`、`test_main_service_exit.py`、`test_main_service_restart.py`、`test_real_history_coldload_e2e.py`。

抽验 `test_config_reload.py::test_refresh_codex_official_models_replaces_whitelist`：断言 codex 官方模型白名单应含 `codex`/`debug`/`models`，实际只剩 `models`——本机 codex 模型列表变动导致，与 rewind 无关。**未擅自修改，交由用户决定。**

### 11.3 前端（下一期）

- `components/chat/MessageBubble.tsx`：复用 `:69` 的 `sessionBusy` 置灰与 `:73` 的 `canDelete` role 门禁；文案需与「删除消息」区分（rewind 是新建会话，不是改当前会话）。
- 确认弹窗照抄 `components/editor/EditorConfirmationModal.tsx`，列出受影响文件清单（唯一数据来源是 `ToolGroup.tsx:71-82 / 110-124` 的 `FileChange → args.changes[].path`）。
- 订阅新 WS 事件 `session.rewind.progress` 展示阶段进度。
- 成功后：**先** `setInputDraft(newId, anchorText)`，**再** `selectSession(newId)`（该顺序反了会静默失效，因为 `InputRow.tsx:463-500` 的恢复 effect 只在 `currentSessionId` 变化时触发），最后 `composerRef.current?.focus()`。
- **预填只支持纯文本**：`setInputDraft(id, draft: string)` 只收字符串；存富文本的 `draftsBySessionRef` 是 `InputRow.tsx` 的组件内私有 ref，无对外暴露 ⇒ 富文本保真属独立重构项，不要挂在 rewind 任务下。
- 分叉视图（结构化血缘 `forkOf`）为二期议题。

### 11.4 工程教训（写给下一次）

- PTY / 交互式任务极易撞 Pan 的 **5400s task timeout**，且被杀后**无任何报告**。本次共挂了 4 次（2 次超时、1 次 codex `402 余额不足`、1 次 `stream disconnected`）。
- 派此类任务必须强制：每条命令显式 timeout ≤240s、交互式进程包在有界 subprocess、边跑边把证据落盘到 `evidence/`、**分阶段汇报**，并明确禁止通读仓库（`packages/web/server.py` 极大，让它通读必死）。
- Windows 控制台是 GBK，不要把含 `❯` 的屏幕 dump 直接 print。


---

## 12. 增补（2026-09-29）：回滚范围 scope 1/2/3

- **worker report 锚点核查**：report 经 `_enqueue_report` → manager `queue_pending` → `_reserve_queue_unit` 落 history 时 **role=`user`**、`source=` `report`、正文抬头 `@@@@by agent : {sourceSessionId} | {name}`（`worker.py:1997-2023, 2405-2425`；`data/sessions/*.history.jsonl` 实证）。⇒ 现有 role 门禁（仅 `user`）**无需放宽**；busy 门禁保留。
- **回滚范围参数**：HTTP body `scope`（1=代码+对话 / 2=仅对话 / 3=仅代码，缺省 1；非法值 → `invalid_scope`）→ `_run_rewind_job` → `run_hybrid_rewind(scope=)` → `RewindDriver.rewind(scope=)`。
- **实测按键序列**（菜单默认高亮 1）：scope 1 = `Enter`；scope 2 = `↓, Enter`；scope 3 = `↓, ↓, Enter`。判定串：`❯` 行按归一化文本匹配，三项互斥（`Restore code and conversation` / 连续子串 `Restore conversation` / `Restore code` 且不含前者）。
- **cbc 菜单是按检查点动态生成的**：若锚点处代码无变化，确认页只有 `Restore conversation` / `Never Mind`（无三项菜单），此时请求 scope 3 会报 `scope option 3 could not be selected`——属预期。
- scope 2 不做文件门控（文件应保持改动后状态）；scope 3 跳过 transcript 截断（对话保留完整）；scope 语义、按键、判定均落到 `HybridRewindResult.scope` 与 rewind 记录。
- 验证：`scripts/verify_rewind_scope.py <1|2|3>` 三个 scope 端到端全部 success（`evidence/rewind_scope_{1,2,3}.json`），父会话与原始 transcript 未变；`pytest tests/test_rewind_api.py tests/test_rewind_transcript.py tests/test_rewind_scope.py -q` 27 passed；既有 `verify_rewind_api.py` 回归 success。

---

## 13. 增补（2026-09-29）：前端「撤回」按钮与交互

本期只实现「保留原分支」（行为固定：产物是新 Pan session，原会话不动），未提供就地截断模式。

### 13.1 锚点范围

- 撤回按钮只对 **`role === 'user'`** 的消息显示。
- **不要用现成的 `canDelete`** 控制显示：它放行 `user` **和 `assistant`**，而 rewind 锚点只支持 `user`。
- worker report 消息本身 `role='user'`、正文抬头 `@@@@by agent : {sourceSessionId} | {name}`（见 §12）⇒ 天然被覆盖，前端无需特殊处理。

### 13.2 改动文件（全部 React 源码，未碰 `packages/web/dist/`）

| 文件 | 改动 |
|---|---|
| `types/index.ts` | `StreamEvent` 增加 `jobId / stage / newSessionId / error / limitation`；新增 `RewindScope`、`ApiRewindResponse` |
| `services/api.ts` | `rewindSessionHistory(sessionId, messageId, scope)`（`POST` body `{scope}`） |
| `stores/sessionStore.ts` | `activeRewind` 状态 + `rewindCurrentMessage` / `applyRewindProgress` / `dismissRewind` + `friendlyRewindError` |
| `stores/uiStore.ts` | `composerFocusToken` + `requestComposerFocus()`（composer 句柄是 `InputRow` 私有 ref，用一次性 token 传焦点请求） |
| `components/chat/MessageBubble.tsx` | 撤回按钮（复用 `sessionBusy` 置灰，文案与删除区分：「任务运行中，无法撤回」） |
| `components/chat/RewindConfirmModal.tsx` | 新增弹窗，样式照抄 `EditorConfirmationModal` |
| `hooks/useWebSocket.ts` | 订阅 `session.rewind.progress` → `applyRewindProgress` |
| `components/chat/InputRow.tsx` | 响应 focus token 调 `composerRef.current?.focus()` |
| `stores/sessionStore.rewind.test.ts` | 新增 5 个测试 |

### 13.3 弹窗内容

- **回滚范围三选一**（单选卡，默认 1）：`对话 + 代码` / `仅对话`（文件保持当前状态）/ `仅代码`（对话保持完整）。
- 列出**锚点消息之后被修改的文件清单**：解析 `FileChange` 工具调用的 `args.changes[].path`（与 `ToolGroup.tsx:71-82 / 110-124` 同一数据来源）。
- 固定说明「将创建新的分支会话，原会话保持不变」。
- 展示 limitation：**仅回滚 cbc 自己文件编辑工具造成的改动，bash 命令与手动编辑的文件不回滚**（取自后端 WS 事件的 `limitation` 字段）。

### 13.4 「锚点无代码变更」的两层呈现

cbc 确认页按检查点动态生成（§12），锚点无代码变化时无三选项菜单。因此：

1. **前置**：选「仅代码」且文件清单为空时，弹窗内联警告「该检查点没有代码变更…可能被拒绝」；
2. **后端失败**：WS `failed` 且 error 含 `could not be selected` → 映射为「该检查点没有代码变更，无法仅回滚代码」，显示在弹窗内 + error toast，**不暴露原始报错**。

### 13.5 成功后的跳转与预填（顺序是硬要求）

写死在 `sessionStore.ts` 的 `applyRewindProgress` → `completed` 分支：

1. **`setInputDraft(newSessionId, 锚点消息原文)`**
2. `loadSessions`（新分支可能不在当前列表，`selectSession` 对不在列表的 id 会早退）
3. `selectSession(newSessionId)`
4. `requestComposerFocus()`

代码内有 `ORDER MATTERS — do not reorder` 注释，并有单测断言 draft 写入是所有副作用的第一步。

⚠️ **顺序陷阱**：`InputRow.tsx:463-500` 的恢复 effect 只在 `currentSessionId` 变化时读 draft ⇒ `setInputDraft` 必须在 `selectSession` **之前**，反了会**静默失效、不报错**。
⚠️ **只能预填纯文本**：`setInputDraft(id, draft: string)` 只收字符串；存富文本的 `draftsBySessionRef` 是 `InputRow.tsx` 组件内私有 ref、无对外暴露 ⇒ 富文本保真属独立重构项。

### 13.6 构建与测试

- `pnpm build`（corepack，`tsc -b` + `vite`）exit=0 通过。
- `InputRow.test.tsx` + `useWebSocket.test.tsx` **133 passed**；新增 rewind 测试 **5 passed**。
- eslint 改动文件 0 error（3 个 warning 是 `MessageBubble.tsx` 既有的 `react-refresh` 提示）。
- 后端 schema 未改：`session.py` 仍只有 §8.3 那 7 行新增方法，`data/sessions/**` 零改动。



---

## 13. 增补（2026-09-30）：实测 Bug 修复

### Bug 1：长/多行锚点 `LookupError: checkpoint anchor not found after 64 ArrowUp steps`
- **根因**（`evidence/rewind_bugfix_anchor_probe.json` 实证）：(a) cbc 检查点预览**只显示消息首行**且单行硬截断（~112 字符 + `…`），旧 `_anchor_terms` 把整段归一化匹配，多行消息永不命中；(b) 列表顶检测用整屏 diff，但相对时间戳（`37s ago`）每秒跳动 ⇒ 永不判定到头，烧满 64 步。另外发现 cbc transcript 对多行 user 消息**只落首行**，`truncate_transcript` 全文本匹配也会失败。
- **修复**：`_anchor_terms` 增加「首行」候选词；`navigate_to_anchor` 改为跟踪**选中行**（`❯` 行）变化、到头即停；新增 `AnchorOutOfRangeError`（业务限制，中文提示）与「无法识别检查点列表」（实现问题）区分；`transcript.find_anchor_line` 增加首行/prefix/suffix 逐级回退匹配；`_format_exc` + server 端去重，消除 `RuntimeError: RuntimeError:` 叠加；前端 `friendlyRewindError` 剥掉异常类型前缀只留中文。
- **复现用例**：`scripts/verify_rewind_anchor_wrap.py`（长多行锚点正例 + 越界锚点负例），修复后全过（`evidence/rewind_bugfix_anchor_wrap.json`）；单测 `tests/test_rewind_anchor.py`。
- 顺手修：TUI 慢启动时双击 Esc 开菜单偶发失败 ⇒ 菜单开启加有界重试（每次发送前先检查菜单是否已开，避免把已开的菜单按关）。

### Bug 2：rewind 产物不继承 workspace`n- `_create_rewound_session` 增加 `workspace_ids=list(parent.workspace_ids or [])`（既有字段赋值，非结构变更；`create_with_available_name` 透传 `create`）。`tests/test_rewind_api.py` 断言新会话 `workspace_ids == ['ws-main']`。

### Bug 3：第一次点确认无进度`n- **根因**（双重）：(a) 弹窗 effect `submitting && !activeRewind → onClose()` 在 POST 未 resolve 的窗口期立即关弹窗；(b) 服务端在 POST 返回**之前**就广播 `starting`，store 的 `activeRewind`（含 jobId）要等 POST resolve ⇒ 早期 WS 事件被 jobId 过滤丢弃。
- **修复**：`rewindCurrentMessage` 在 POST 前**同步**写入 pending 状态（`jobId: ''`）；`applyRewindProgress` 对 pending 状态按 `sessionId` 采纳事件并认领 jobId，POST resolve 只补 jobId、不回退 stage；弹窗改为 `sawActiveRef` 守卫（见过活动任务后才允许自动关闭）。单测 `sessionStore.rewind.test.ts` 新增「事件先于 POST resolve」时序复现，7/7 过。

### 验证`n- `pytest tests/test_rewind_api.py tests/test_rewind_transcript.py tests/test_rewind_scope.py tests/test_rewind_anchor.py -q` 全过；`verify_rewind_scope.py 1/2/3` + `verify_rewind_anchor_wrap.py` 端到端全 success；前端 `pnpm build` exit=0。


## 14. 增补（2026-09-30 下午）：真实会话「代码未回滚」紧急诊断与修复

真实会话 ses_404f4c6fd9eda79a 实测：两次 scope 1 撤回 job 都报 completed、对话确实截断，但代码修改完全没有回滚。定位出两个叠加的后端 bug 与一个前端显示 bug。

### Bug A-1：恢复进程被提前杀死（代码未回滚的直接原因）
- 服务端 `_run_rewind_job` 传 `expected_files=None`，旧驱动完成门 `_files_match(None)` 恒真，确认 Enter 后微秒级判定「完成」；`close()` 0.15s 发 Ctrl+C、约 1.5s 强杀 PTY——cbc 尚未把快照写回磁盘即被杀。scope e2e 之所以通过，是因为它传了 `expected_files`，驱动会一直等到文件真的变化。
- **修复**：新增 watched_files 完成门——确认前对「锚点之后被文件工具改过的文件」拍基线快照，确认后等到任一文件实际变化（有界超时）才算完成；观察不到变化则报错中止。新增 `packages/core/rewind/filetools.py` 从 Pan history 提取 Write/Edit/FileChange 路径；服务端提取并透传。scope 2 走「确认页关闭 + 屏幕静置」门。无文件信息时的兜底也从「立即关闭」改为静置门。

### Bug A-2：导航竞态与相同预览误判「到头」
- 实测按键语义（evidence/rewind_bugfix2_keymap.json）：菜单打开瞬间选中 `(current)` 是瞬态，很快自动稳定到最顶（最旧）检查点；ArrowUp/ArrowDown 单步移动；不等渲染完成就连发会被吞（8 次 Up 只生效 1 次）。旧实现从瞬态 `(current)` 向上走是碰巧能对；读到稳定态则 ArrowUp 一步不动、直接误判「到头」。
- 列表超过一屏后跟随选中滚动；相邻两个检查点预览文本相同（重复消息）时 ❯ 行文本不变，旧「选中行是否变化」判定把「移动到相同文本的下一项」误判为「到底」，提前停止（evidence/rewind_bugfix2_nochange_scope1_tail.txt）。
- **修复**：`navigate_to_anchor` 重写——先等选中行稳定；向上归一到顶；向下逐步走，每步等待并重验；移动判定改用「选中项签名」（预览行 + 元信息行 + 后续两行，相对时间戳已剥离，避免秒级跳动造成假变化）。

### Bug A-3：锚点歧义的序号消歧
- worker report 消息共享同一抬头，首行预览必然撞车；「取第一个匹配」会选错检查点。
- **修复**：服务端 `compute_match_ordinal` 按「之前有多少条 user 消息的首行与锚点首行冲突」计算 0 基序号，经 `AnchorSpec.match_ordinal` 传入；驱动选第 N+1 个匹配项，数量对不上报明确错误；transcript 截断侧 `TranscriptAnchor.match_ordinal` 同样支持序号消歧（缺省 None 保持严格报错）。

### Bug A-4：弹窗「无代码变更」误报
- 弹窗受影响文件扫描只认 `FileChange` 工具，cbc 会话实际是 Write/Edit 工具调用，导致任何锚点都显示「这条消息之后未检测到代码变更」。已支持 cbc 的 Write/Edit `file_path` 提取。

### Bug 3 复发：第一次点确认无进度
- 结构性修复：弹窗开闭状态从 MessageBubble 局部 state 上移到 store（`rewindTarget`），由 ChatMessages 顶层挂载——消息列表重挂载不再能杀掉弹窗；新增 `GET /api/sessions/{id}/rewind/{jobId}` 轮询端点，弹窗在任务运行期间每 2s 轮询兜底，WS 事件丢失也能推进进度。组件测试覆盖「首次点击即显示进度」与「WS 全丢靠轮询推进」。

### cbc 确认页语义澄清
- 检查点谱系内有被跟踪文件时，确认页总是给全部 4 个选项（无变更时显示 +0 -0，恢复为 no-op）；「只有 Restore conversation / Never Mind 两项」只在该检查点谱系完全无被跟踪文件时出现。驱动对 2 项菜单保留 `NoCodeChangesAtCheckpointError` 守卫。

### 验证
- `pytest tests/test_rewind_*.py` 43 项全过（含 match_ordinal、签名导航、filetools、transcript 序号新用例）。
- 多轮场景 e2e（4+ 轮、同一文件 3 个版本）：中间锚点 scope 1，文件真实回滚到 MT_VERSION_ONE、对话截断（evidence/rewind_bugfix2_mt_rewind.json）；尾部无编辑锚点 scope 1/2 均成功且文件不变（evidence/rewind_bugfix2_nochange.json）。
- 前端 `pnpm build` exit=0；组件测试 4/4、store 测试 8/8。


## 15. 增补（2026-09-30 傍晚）：cbc 2.160.0 崩溃与多信号完成门

用户实测 scope 1 连简单场景也失败（超时被杀）。逐层定位：

### 根因：cbc 2.160.0 对「工作区外文件」恢复直接崩溃
- 失败记录的 failure_screen_tail 显示 Node 崩溃栈：`CheckpointRestoreValidationError: Checkpoint file destination is outside the workspace`（`assertCheckpointRestoreSafe` → `revertToCheckpoint`）。
- 用户会话的 workdir 是仓库目录，但 cbc 写入的文件在**桌面**（world.py / hallo.py）和 `.codebuddy` 内存目录——都在工作区外。cbc 2.160.0 新增的安全检查在确认恢复瞬间抛未处理 Promise 拒绝，TUI 卡死（进程不死、确认页不关闭），restore 永不发生。旧完成门死等文件变化 → 35s 超时 → 强杀 → 报「未观察到文件变化」。
- 崩溃探针（evidence/rewind_bugfix3_crash.json）：Enter 后 **0.02s** 即崩，确定性复现（4/4）。

### 修复：多信号完成门 `_await_restore`（driver.py）
1. 任一信号即完成：被监视文件实际变化 / 期望文件状态命中 / 屏幕进入恢复后的交互视图（rewind UI 关闭 + 状态行出现）。
2. **崩溃快速失败**：轮询中检测崩溃签名（`CheckpointRestoreValidationError` / `UnhandledPromiseRejection` / `<rejected>`）或进程死亡，立即报错，不再等超时；工作区外崩溃翻译为「该检查点涉及的文件不在工作区内……可改用『仅对话』」。为防误报，屏幕已是恢复后视图时签名视为对话内容。
3. **超时不盲杀**：超时后先核验——崩溃/确认页未关闭 = 可证明未恢复 → 失败；已是恢复后视图 = 内容 no-op 恢复 → 按完成处理并记录 `restore_verification='unverified-timeout-screen'`；模棱两可同样按完成+警告处理。
4. watched_files 为空时显式降级为屏幕信号，降级写入 stage 事件与结果字段（`restore_verification`），服务端 rewind 记录同步落盘该字段。
5. 成功后保留 ≤2s 静置再关闭 PTY（文件落盘信号比 cbc 收尾早一帧）。

### 验证（cbc 2.160.0）
- 用户真实会话服务器等价路径：scope 1 **14s 内快速失败**并给出工作区外提示（原为先超时后被杀）；scope 2 成功（对话截断，`restore_verification=screen-settle`）——evidence/rewind_bugfix3_server_path.json。
- 多轮场景 mt 探针 fix_works=True（文件回滚 MT_VERSION_ONE）；scope 1/2/3 e2e 全过；nochange 探针全过。
- `pytest tests/test_rewind_*.py` 51 项全过（新增 test_rewind_restore_gate.py 8 项）。
- cbc 2.160.0 菜单/按键语义与旧版一致，恢复落盘延迟约 0.08s，等待参数无需调整；唯一行为变化就是上述工作区外文件安全检查。

---

## 16. 增补（2026-09-30）：工作区外文件回滚的根因与 `--add-dir` 方案；代码回滚与 git 的关系

### 16.1 `assertCheckpointRestoreSafe` 的确切规则（读 2.160.0 实现源码得出）

- **允许恢复的根目录 = 会话工作区 + `--add-dir` 指定的额外目录**：实现里明确遍历 `session.options.addDir`，把每个绝对路径 `canonicalDirectory` 后加入允许根集合。
- fileSnapshots 的目标路径必须**严格落在**这些根内（`isStrictlyInside`），否则抛 `CheckpointRestoreValidationError` → 未处理 Promise 拒绝 → TUI 卡死（§15 的超时根源）。
- **「仅对话」模式整个跳过该校验**（`"Conversation"!==scope && assertCheckpointRestoreSafe(...)`）——这就是 scope 2 从不失败的原因。
- 修改与恢复走的是**不同关卡**：修改走运行时权限系统（bypass 模式全放行，所以 cbc「能改」工作区外文件）；恢复是独立的硬路径包含检查（防「把旧快照写进任意位置」），所以「能改不能回滚」。

### 16.2 解决方案：`--add-dir`（官方参数，非 hack）

cbc CLI 自带 `--add-dir <directories...>`（"Additional directories to allow tool access"），语义与校验规则完全对口：

> rewind 驱动 spawn PTY 前已能从 Pan history 提取受影响文件清单 → 计算所在目录集合 → spawn 时逐个 `--add-dir` ⇒ scope 1/3 对工作区外文件直接合法。

待实现（验收标准：**工作区外的文件（如桌面上的 `world.py`）+ scope 1 ⇒ 文件真实回滚**，工作区内场景不回归）。前端预检置灰方案降级为兜底。

### 16.3 代码回滚会不会动 git？（证据：读 2.160.0 `revertFiles` 实现）

**不会。** 恢复路径与 git 完全无关：

1. `revertFiles(sessionId, snapshots)` 遍历 `fileSnapshots`，恢复来源是 cbc **自己的备份存储**：`resolveCheckpointBackupPath(sessionId, backupFileName)` → `fileVersionStore.equalsFile(...)` → `restoreFile(backupPath, destPath)` / `deleteFile(destPath)`（配合 `existedAtTrack=false` 语义；备份缺失时还刻意保守：日志 `[cbc-kw=CHECKPOINT] Skip revert-delete ... to avoid data loss` 并记错误，**不碰当前文件**）。
2. 整个 bundle 里 git 命令只出现在两处，**均不在恢复路径**：
   - Bash 工具沙箱的只读命令白名单（`git remote` / `git rev-parse` / `git blame` / `git config --get`，`decision:"allow", source:"builtin"`）；
   - 系统信息探测 `isInGitRepository()` / `getGitBranch()`（只读执行 `git rev-parse --is-inside-work-tree`、`git branch --show-current`，1s 超时）。
3. 官方文档（§3.3）也定位为「本地撤销」「不能替代版本控制」；跟踪机制是 cbc 自有的 `file-history-snapshot` / `trackedFileBackups`（存 `~/.codebuddy`，不在 `.git`）。

结论：rewind 的代码回滚 = **用 cbc 私有备份直接覆写/删除文件内容**，不建 commit、不改 index、不写 `.git`。唯一副作用是：如果你的目录恰好是 git 仓库，文件内容变化会**作为普通工作区改动出现在 `git status` 里**——这与任何一次文件编辑无异，不是「修改 git」。Pan 侧的对话截断（`transcript.py`）只重写 cbc 副本 JSONL，同样与 git 无关。

---

## 17. 增补（2026-09-30）：`--add-dir` 落地——工作区外文件可真实回滚

### 17.1 实现

- **目录集合来源**：`packages/core/rewind/filetools.py` 新增 `compute_add_dirs(file_paths, workdir, max_dirs=8)`。输入即服务端已传入的 `watched_files`（`extract_mutated_files` 从 Pan history 提取的锚点后 Write/Edit/FileChange 路径），取父目录后按安全规则过滤，返回 `(dirs, notes)`：
  - 只加**存在的目录**（缺失记 note）；
  - **工作区内的目录静默跳过**（cbc 已允许）；
  - **盘根/文件系统根直接拒绝**（`C:\`、`E:\`、`/`，记 note，永不整盘喂给 cbc）；
  - **上限 8 个**，超出丢弃并记 note；
  - 相对路径先按 workdir 解析并 `normpath` 归一（不 resolve 符号链接）。
- **spawn 注入**：`driver.py` 新增 `_build_resume_argv()`，`RewindDriver.rewind()` 新增 `add_dirs` 参数——显式传入优先，缺省时从 `watched_files` 自动计算；PTY spawn argv 末尾追加 `--add-dir <dirs...>`（空集则不追加）。`rewind_in_pty()` 同步透传。
- **可观测**：`RewindResult` 新增 `add_dirs` / `add_dirs_notes` 字段；`RESTORING` 阶段事件携带两者；混合层 rewind 记录经 `file_rewind.__dict__` 自动落盘；服务端 `server.py` `_run_rewind_job` 的成功/失败两条记录路径均补记 `add_dirs` / `add_dirs_notes`（含未传原因）。
- **失败兜底**：§15 的多信号完成门与快速失败原样保留；工作区外崩溃提示补「可重试（确认已附加 --add-dir）或改用『仅对话』」。
- 前端未改（`RewindConfirmModal` 无需变更；工作区外文件标注属可选项，为控制改动面未做）。

### 17.2 验证

**核心用例**（`scripts/probe_rewind_adddir_build.py` + `probe_rewind_adddir_rewind.py`，evidence/rewind_adddir_{build,rewind}.json）：

- 场景：cbc 工作区在仓库内 `pan-rewind-adddir-work/`，目标文件在**工作区外** `C:\Users\...\AppData\Local\Temp\pan-rewind-outside\outside_target.txt`；4 轮会话（T1 Write→V1、T2 Edit→V2、T3 纯问答、T4 Edit→V3）。
- 走服务端等价路径 `run_hybrid_rewind(scope=1, watched_files=[target])` 锚定 T2：
  - 文件真实回滚 **MT_VERSION_THREE → MT_VERSION_ONE**（`restore_verification=file-change`，最强信号，不再是崩溃）；
  - `add_dirs=['C:\\Users\\...\\Temp\\pan-rewind-outside']` 记录正确；
  - 对话截断（副本 history 5 条、无 T4）、父会话 transcript sha1 前后一致；
  - 全程 14.7s。

**回归**：

- 工作区内多轮 mt 探针 `fix_works=True`（文件回滚 MT_VERSION_ONE）；scope 1/2/3 e2e 全过（evidence/rewind_scope_{1,2,3}.json，各约 24s）；
- 简单场景（单轮 + 一次 Write + scope 1）`verify_rewind_hybrid.py` success，7 项断言全 true；
- API 端到端 `verify_rewind_api.py` success（两次回滚、重名去重、usage 重算均正常）；
- `pytest tests/ -k rewind`：**61 passed**（新增 `tests/test_rewind_adddir.py` 10 项：目录集合计算、去重保序、工作区过滤、相对路径解析、缺失目录跳过、盘根拒绝、上限截断、argv 拼接、空集省略、崩溃文案）。

### 17.3 顺带修复（与 `--add-dir` 无关的预存在问题）

`verify_rewind_hybrid.py` 原以末尾 marker（`PAN_REWIND_HYBRID_MARKER`）作锚点文本；cbc 2.160.0 的检查点预览行截断变短，长路径把 marker 挤出预览 ⇒ `AnchorOutOfRangeError`。已把锚点改为完整 prompt（与服务端行为一致，命中 `normalized[:64]` 项）。该失败在本次改动前即可复现（§15 未重跑此脚本），属脚本兼容性问题。

---

## 18. 增补（2026-09-30）：截断语义修正——锚点消息本身不进入新会话

### 18.1 根因与修法

用户实测：撤回消息 A 后，新会话末尾仍保留 A。根因是两边基准点不一致——cbc 原生 rewind 回滚到「处理 A 之前」的检查点（A 造成的文件改动被撤销），而 `truncate_transcript` 旧语义是「保留锚点行**及之前**」。修法：`transcript.py` 截断终点改为 `[0, anchor_line_index)`——**锚点行本身及之后全部剔除**，与 cbc 的检查点基准对齐。A 的原文由前端既有逻辑预填进新会话输入框（`setInputDraft(newId, anchorText)`，未改前端）。

`TruncationResult` 字段语义更新：`after_lines` = 保留行数 = `anchor_line_index`（锚点不含在内）；`removed_lines` 含锚点行。锚点找不到/歧义/越界/非 user 的报错行为不变；`compute_match_ordinal` 数的是锚点之前的冲突，不受影响。

### 18.2 关键风险实证：空 transcript 可以 resume

锚点为首条消息时，副本 B 的 transcript 截断为**空文件**。新探针 `scripts/probe_rewind_anchor_excl.py`（evidence/rewind_anchor_excl.json）实测：

- 文件回滚到不存在、B 的 JSONL 为 0 行、不含锚点文本、Pan 新 history 为空、父会话 sha1 不变；
- **`cbc -p --resume B` 正常回答**（exit 0，marker 命中 stdout）——空会话可续接，无需占位行或改 fork 策略。

### 18.3 验证

| 验收项 | 结果 |
|---|---|
| 锚点=首条消息：history 空 + 文件回滚 + 空会话可 resume | ✅ rewind_anchor_excl.json |
| 锚点=中间消息：新 history 只含锚点之前的轮次（T2/T4 均剔除，实测副本 history = T1 一轮 5 行） | ✅ rewind_adddir_rewind.json（工作区外场景） |
| mt 多轮探针回归 | ✅ fix_works=True |
| scope 1/2/3 e2e 回归（断言更新为 `child_excludes_anchor`，文件回滚断言不变） | ✅ rewind_scope_{1,2,3}.json |
| 简单场景 hybrid e2e（断言更新：B transcript 0 行、history 0 条） | ✅ rewind_hybrid_e2e.json |
| API e2e（断言更新为 `histories_exclude_anchor`：两个新会话 history 均为空且不含 marker） | ✅ rewind_api_e2e.json |
| 单测 | ✅ `pytest tests/ -k rewind` 62 passed（test_rewind_transcript.py 语义更新 + 新增「锚点在首行 ⇒ 空 transcript」用例） |

前端未改：预填与跳转走 `session.rewind.progress` 完成事件（`newSessionId` + `anchorText`），不依赖 history 内容；`ChatMessages` 对空消息列表有既有 `empty` 态（等同全新会话）。空 history 会话的服务端创建已被 API e2e 覆盖。
