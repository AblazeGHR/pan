# Kimi Terminal TUI 文件夹信任启动失败 — TA 候选报告

- 工作树：`D:/project/pan-worktrees/kimi-terminal-trust-20261027`
- 分支：`fix/kimi-terminal-trust-20261027`
- 基线：`56cf4a9dca88b4d5847a306f7db432f55161661f`
- 范围：Kimi 原生 PTY 桥（`packages/core/adapters/kimi/native_bridge.py`）及其专属测试；未改前端、未改 `worker.py` / `server.py` / Terminal runner、未改现有 Session、真实 `~/.kimi-code` 或全局信任配置。
- 平台：Windows 11，ConPTY；实际 CLI：`C:/Users/14709/.kimi-code/bin/kimi.exe` **2.3.3**。
- 结论：根因是桥在 **会话创建前** 用 20s 固定门禁等待新会话，并把“未信任目录仍停留在信任提示”当作启动失败；期间 stdin/控制通道尚未接线，随后 `finally` 终止 TUI。另有一个 30s 信任等待阻塞单线程输入循环、以及超时异常被外层 `except: continue` 吞掉导致任务无结果的缺陷。

## 根因（基线 56cf4a9）

`kimi` 新会话在文件夹未信任时 **不会创建 session**，TUI 先显示：

```
Trust this folder?
↑↓ navigate · Enter select · Esc exit
...
❯ Trust and continue
  Exit
```

基线桥的顺序（`packages/core/adapters/kimi/native_bridge.py`）：

1. `500-507`：spawn 后同步等待 `discover_session_id`，20s 截止；未找到即 `pan.native_error: "Kimi TUI session was not created"` 并 `return`。
2. `526`：stdin 读取线程在等待之后才启动 → 这 20s 内用户按键完全不接线。
3. `517`：`ready_watch_loop` 也在等待之后才启动 → 新会话路径不会发出 `pan.native_trust_required`。
4. `542`：`bridge.trusted.wait(30)` 在唯一的输入循环里同步等待 → 一条 Pan 任务会把 `control_mode` / `native_input`（也就是用户回答信任的按键）阻塞最多 30s。
5. `557`：上述等待超时抛出的异常被外层 `except Exception: continue` 吞掉，**不产生 result** → 任务悬挂。
6. `559-561`：`run()` 返回后 `terminate(force=True)` + `close()`，TUI 随之退出。

初始失败证据（真实 CLI、隔离 `KIMI_CODE_HOME`、临时未信任目录）：

- `before-fresh-untrusted/`：20.593s 时 `pan.native_error: Kimi TUI session was not created`，进程结束；全程 0 个 `pan.native_trust_required`；`rendered.txt` 中可见完整信任提示。`summary.json` 记录 argv 与 exit。
- `before-resume-untrusted/`：已恢复会话 + 移除信任标记。t=4.0 发送 Pan 任务，t=5.0/6.0 发送 `control_mode`/Enter；回执直到 **t=34.008/34.009** 才出现（30s 阻塞），且没有 result；信任标记在 ~34.17 才写入。

## 修复

只改 `packages/core/adapters/kimi/native_bridge.py`：

- 启动顺序：spawn 后先接线 drain、`ready_watch_loop`、stdin 控制循环；未信任提示从第一帧起可交互。
- 会话发现改为后台线程：按 TUI 生命周期轮询，不再有 20s 固定启动截止；新会话的 wire 从 offset 0 开始（历史会话仍从现有文件尾开始）。
- Pan 任务在信任标记出现前保持 pending，不粘贴进信任提示；等待期间控制通道持续处理 `control_mode` / `native_input` / `native_resize`。
- Terminal 所有权优先：用户处于 native 模式时任务继续 pending，不抢占用户输入框；切回 pan 后再投递。
- 任务在 TUI 退出且仍未投递时产生 `kimi_stop_reason: "tui-closed"` 的真实 result，避免任务无回执。
- 不预信任、不自动应答、不跳过信任；`terminate(force=True)` + `close()` 保留。

## 真实 CLI 证据（修复后）

均使用真实 `kimi.exe 2.3.3` + ConPTY + 隔离 home（无真实账号/付费模型；模型为本地 loopback 假端点）。

| 场景 | 证据 | 结果 |
| --- | --- | --- |
| 未信任新会话，延迟 25s 再确认 | `final-delayed/` | 25s 时提示仍可交互；Enter 后信任标记生成；resize 40x120 回执；任务 result `TRUST_TURN_OK`；36.0s 关闭 stdin，36.07s 退出 0 |
| 未信任新会话，确认后继续 | `final-trust-turn/` | t=0.18 trust_required；t=4.0 任务挂起；t=5.0 native 切换即时；t=6.0 Enter；t=9.0 切回 pan；t=9.75 收到 assistant/result；20.0s 关闭，20.07s 退出 0 |
| 明确拒绝（Esc → Exit） | `final-decline/` | 5.50s `Kimi TUI exited`；5.63s `tui-closed` result；5.66s 退出 0；未生成 `workspace-trust` |
| 已信任目录 | `final-pretrusted/` | 无信任提示；t=3.9 完成 `TRUST_TURN_OK`；12.01s 关闭，12.08s 退出 0 |
| 启动失败（不存在的 CLI） | `startup-failure.stderr.txt` | 非 0 退出（1），`SpawnDenied ... CreateProcessW winerror=2`；无 TUI/孤儿 |
| resize | `final-delayed/rendered.txt` | 分隔线宽度 108 → 118（40x120），回执 `pan.native_write ok` |

关键事件片段见 `final-trust-turn/transcript.jsonl`（非 output 事件）与 `rendered.txt`；初始失败见 `before-fresh-untrusted/`、`before-resume-untrusted/`。

## 测试与命令

| 命令 | 结果 |
| --- | --- |
| `python scripts/run_tests_isolated.py -- python -m pytest tests/test_kimi_native_bridge.py -q --junitxml=.../focused-regression.xml`（含 Kimi/CBC/Codex/native-control 回归） | **190 tests, 0 failures, 0 errors, 4 skipped**, 15.0s |
| `TEST_KIMI_NATIVE_TUI=1 python scripts/run_tests_isolated.py -- python -m pytest tests/test_kimi_native_integration.py -q --junitxml=.../real-tui.xml` | **5 tests, 0 failures**, 37.0s；覆盖未信任存活/确认/拒绝/已信任+resize/坏 CLI |
| `pnpm exec vitest run src/components/chat/NativeTerminal.restart.test.tsx` | 1 passed（重开视图，未改前端） |

新增单元测试（`tests/test_kimi_native_bridge.py`）覆盖：无 session 时信任提示仍可交互、Pan 任务不粘贴进信任屏且控制不被阻塞、TUI 退出产生 `tui-closed`。真实集成测试新增 4 条（`tests/test_kimi_native_integration.py`），其中信任存活测试刻意等待 21s，旧代码会在此前退出。

备注：既有 `test_native_bridge_real_pty_turn_and_interrupt` 原先的 busy 断言会命中启动预留的 `pan.native_busy: True`；已改为从发送 SLOW 后的事件切片等待，再发送 Ctrl+C。该修改让断言命中目标回合，不放宽中断语义。

## 清理与边界

- 所有真实 CLI 子进程在 bridge 退出后消失：`tasklist /FI "IMAGENAME eq kimi.exe"` 无输出；集成测试用 psutil PID+create_time 断言子进程已退出或 PID 已复用。
- 本地假模型 HTTP server 在 fixture/teardown 关闭；无监听残留。
- 未验证层：React/xterm 浏览器内实际绘制（未改前端；验证的是 ConPTY 原始字节与生产终端同源）；完整 Pan worker/queue/HTTP 浏览器 E2E（桥协议与既有 `test_native_control_api` 覆盖，但未起完整生产 profile）；POSIX（ConPTY 仅 Windows）；kimi 2.3.1（修复在 2.3.3 实测，2.3.1 未复跑）。
- 会话发现仍沿用既有 `discover_session_id`（cwd + 最新 `createdAt`）启发式。生产 per-Session `kimi_home` 时无歧义；无 MCP 时回退真实 home，与其他并发 kimi 进程的歧义是既有边界，本任务未扩大修复范围。
- main/practical、push/release/部署、保护服务、真实账号均未操作；本报告仅为 TA clean 候选，等待 MA 验收。
