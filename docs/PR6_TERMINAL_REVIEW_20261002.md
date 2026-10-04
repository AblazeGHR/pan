# PR #6 的 PTY 审查与 Pan 终端管理设计建议

日期：2026-10-02。状态：审查完成，以下终端管理方案为待实现设计。

## 结论

PR #6 的 PTY 能完成短期 CBC TUI 自动化；本次实测中文读写和 ANSI 解析正常。
它的职责是回滚，现有 62 项回滚测试全部通过。不能据此判断回滚功能整体质量差。

2026-10-03 用户澄清：主要探索部分 Adapter 能否在 Pan 运行时切换到网页交互式 TUI，
尽量不打断原进程；没有要求首版提供本机终端 attach。PR 没有提供运行中 Adapter 的
TUI 切换，也没有终端 Session、用户输入输出通道、动态尺寸、重连或终端前端。
建议新建独立的 Pan Terminal 管理模块，可复用底层 PTY 库和 PR 的验证经验，
不把 `_PtySession` 原样扩充成长期终端管理器。

## 审查对象与隔离

- 主线起点：`6f66a230a8e581dfa25a237b0d7f11998ae5d6f4`。
- 审查分支：`audit/pr6-terminal-20261002`。
- 审查与文档工作树：`D:/project/pan-worktrees/pr6-terminal-20261002`。
- PR：<https://github.com/AblazeGHR/pan/pull/6>。
- PR HEAD：`387a43ec4dfe699498bb0b95d7ceef212f905b99`。
- PR 源码独立工作树：`D:/project/pan-worktrees/pr6-terminal-source-20261002`，detached HEAD。
- 未修改 main/practical，未合并、推送或启动/重启 Pan 服务。
- 本次只启动测试自行创建的短期 Python/PTY 进程；测试结束清理它们。

PR 文档 `docs/REWIND_PTY_REPORT.md` 第 3 条设计决定明确说明：PTY 只在 rewind 时临时启用，
正常任务继续使用 pipe。PR 的 GUI 是回滚确认和回滚进度，并非终端界面。

## 能力对照

| 能力 | PR #6 当前实现 | 对 Pan 终端的含义 |
|---|---|---|
| 启动真实 PTY | `driver.py:477`，pywinpty | 可借鉴，真实 PTY 烟测通过 |
| 中文/ANSI | 读写字符串，pyte 解析屏幕 | 本次简单中文往返与颜色序列解析通过 |
| 通用命令 | `_PtySession` 接收 argv；上层绑定 CBC resume | 底层可启动其它命令，上层不适合通用终端 |
| 用户键盘输入 | 仅 `send(str)`，调用者是回滚 driver | 缺少浏览器输入、粘贴、快捷键协议 |
| 动态尺寸 | 只在 spawn 传入 rows/cols | 无 resize/setwinsize；不能适应面板尺寸变化 |
| 输出与历史 | pyte 固定尺寸 Screen；单个消费者 queue | 无原始输出订阅、滚动历史或重连恢复 |
| 生命周期 | `rewind()` 的 finally 调用 close | 回滚结束即销毁，无法 detach/reattach |
| 多客户端 | 无 | 无输入控制权和尺寸控制权 |
| 运行中 Adapter 切换网页 TUI | 无 | 创建新的交互式进程不等于保留原进程 |
| 服务重启续接 | PTY 位于当前 Python 进程 | 默认随 Pan 结束；显式 detach 的终端需要独立所有者 |
| 跨平台 | `_PtySession` 无条件 import winpty | 仅 Windows；需要平台 backend 和依赖标记 |

## 应避免直接继承的问题

### 1. 无界输出队列，不适合长期运行（高优先级）

`driver.py:484` 使用 `queue.Queue()`，`_read()` 始终 put。屏幕只在 `wait_for()`
从队列取数据时更新。消费者暂停或用户离线时，输出持续留在内存中。

确定性探针连续生产 2,048 个 4,096 字符块、没有消费者，实际排队 8,388,608 字符，
`maxsize == 0`。这是队列行为复现，不是对生产环境内存耗尽时间的测量。

长期终端需要持续 drain PTY、有界输出日志和每客户端容量限制。慢客户端应断开并
从受限历史/屏幕快照恢复，不能无限排队，也不能简单阻塞整个 PTY reader。
浏览器消费确认应在终端渲染完成后发送，避免把网络接收速度当作显示速度。

### 2. 根进程退出前的输出 drain 不完整（中优先级）

`driver.py:495` 在 read 之前检查 `isalive()`；发现 false 就停止，不再读取可能已缓冲的
末尾输出。短命命令和自然退出容易触发这种顺序风险。

确定性假 PTY 在 `isalive() == False` 时仍提供可读的 `FINAL_OUTPUT`，实际 `_read()`
调用 read 的次数为 0。此测试证明包装器的控制流程，未测得真实 CBC 的尾输出丢失率。

应持续读取到通道 EOF，再公布 output-complete/exit；区分根进程退出、通道关闭和
终端完成。Microsoft 也要求持续 drain 输出，包括 ClosePseudoConsole 产生的最后一帧。

### 3. close 缺少可靠、可验证的整树所有权（中优先级）

`driver.py:537-550` 先在线程中 `terminate(force=True)`，随后 `_kill_tree(pid)` 才
查询 descendants。父进程已经消失时，`psutil.Process(pid)` 失败，直接返回空数组。

本次证据应分开理解：

- 实际普通 Windows 父子进程 + 替代 PTY transport，保留原 PR 的 close/_kill_tree：
  父进程先退出，`killed_pids == []`，子进程仍活着；探针随后自行清理子进程。
- 实际 pywinpty PTY + 忽略 Ctrl-C 的子进程：本次 close 成功，子进程也消失。
  ConPTY 本身关闭时会终止仍附着在该伪控制台的相关进程，这与实测一致。

因此不能声称真实 PTY 测试已证明普遍遗留子进程。可以确认的是：psutil fallback 本身
不能覆盖根 PID 消失后的脱离控制台后代，返回的 `killed_pids` 也不是退出成功证明。
`close()` 未 join reader，终止线程超时后与直接 close 仍可能并行，缺少统一的状态转换。

独立终端 host 应在启动时确立进程所有权，关闭时确认整树退出。Windows 优先使用 Job
Object；赋值必须覆盖 spawn 竞态，不能仅在快速运行的进程启动之后追加。
主线现有 `packages/core/takeover_job.py` 可参考其 suspended-launch/Job 处理，
但它创建可见 console，不能原样充当 ConPTY spawn。清理失败应保留所有者状态并返回错误。

### 4. 缺失长期会话能力（产品差距）

没有 Terminal ID、管理 registry、list/get/attach/detach/resize/close、输出游标、
退出码事件、输入控制权、本机客户端或独立终端 UI。固定 `Screen` 只提供当前屏幕文本，
不能代替 xterm.js 的渲染、滚动历史、鼠标和终端模式状态。

这些不是回滚 PR 承诺的功能，不作为其回滚功能的 bug；它们意味着我们需要新模块。

## 建议的产品与实现边界

将 Terminal 作为全局管理的独立对象，可选择关联 Workspace 或 Pan Session。
Terminal ID 与 Agent Session ID、Worker ID 分开；默认启动普通 shell，用户可以在里面
运行 CLI/TUI。先让用户自由使用终端，再对接 Agent takeover。

用户已明确：浏览器存活不改变 Pan 服务行为，隐藏面板/断开连接不终止进程，
显式“关闭终端”才结束该终端。默认终端随 Pan 服务生死；显式 runtime detach 后
保留进程与重连方法。这不同于断开浏览器连接，两种动作必须使用不同语义。

首版不承诺运行中 Adapter 无中断切换原生 TUI；应按 Adapter 做可行性探针，视难度决定。
普通终端、网页中启动新的原生 TUI、保持原进程的 TUI 切换必须分别验收。
PR #6 创建 fork，再启动一个新的 CBC resume TUI，驱动回滚后关闭 PTY，
没有把正在执行的 stream-json 进程切换为交互模式。

当前 CBC Adapter 使用 `-p --input-format stream-json --output-format stream-json`，
没有生成可直接显示的原生 TUI 屏幕。把同样命令放进 PTY 不会自动产生 TUI。
当前 Codex Adapter 使用 `app-server --stdio` 协议，也需要探索其原生客户端是否能连接
同一后端。已有 takeover 会停止 runtime 后以 resume 启动另一个 TUI；保留 session ID
不能作为原进程或正在执行的 turn 连续性证明。

探针方向：原生 TUI 作为独立客户端连接仍运行的 backend；或从一开始就在 PTY 中启动
交互式 CLI，并验证 Pan 同时获得可靠结构化事件/控制通道的能力。
后者不能默认通过屏幕抓取替代现有 history、usage、tool/approval 和任务完成事件。
另外，自建终端风格视图可以保留现有 backend，但必须标明它不是 provider 原生 TUI。

推荐组成：

1. **Terminal runtime**：拥有 PTY、输出缓冲、进程树和生命周期。
   默认由 Pan 生命周期管理；支持显式 detach 到独立所有者，并提供重连方法。
   是否采用同一独立 host 配合两种所有权策略，需通过 shutdown/crash 与 Job 探针验证。
   detach 必须保留原 PTY/进程，不能用重启代替所有权改变。主机重启后旧进程无法
   恢复，应标记 exited/lost，而不是从元数据伪造存活。
2. **Pan API 与网页客户端**：从已有用户入口建立授权后的终端连接，前端采用
   xterm.js 和 fit addon，提供新建、列表、切换、关闭、复制粘贴和 resize。
3. **Adapter TUI 探索**：检查哪些 Adapter 支持原生前后端分离或可靠双通道，
   验证切换期间原 backend PID、运行中任务、写入控制权和结构化事件是否保持。
4. **与 Pan 后台 Job 的关系**：优先比较复用 `background_jobs` 的 detached runner、
   注册、日志和进程身份核验，或将终端与后台 agent Job 共用更底层的 runtime owner。
   Job 仍是任务/结果对象，Terminal 是交互进程端点，二者不应强制等同。
   这里的 Pan Job 与 Windows Job Object 不是同一概念。Windows Job Object 负责进程树
   所有权，不能直接把已有进程从 kill-on-close Job 中摘出；需在启动布局中预留 detach。

PTY 后台持续运行；客户端 detach 只释放连接和输入权。显式 close 才结束终端。
一个客户端持有输入权，其它连接只读；控制权转交后拒绝旧客户端的输入及 resize，
用 generation/lease 防止网络中迟到的消息继续操作终端。尺寸跟随当前输入客户端。

输出需要序号和确认游标。重连时若历史完整，可重放；超过保留窗口时必须明确返回
gap，并从可还原光标、颜色、alternate screen、终端模式的快照恢复。
单纯取最后几 KB ANSI 文本并不保证屏幕可正确重建。快照引擎的选择应通过 TUI 探针
决定，不能假设当前 pyte.text() 已覆盖这些状态。

浏览器客户端通过已授权连接访问自己的 terminal；runtime IPC 只允许当前本机用户。
身份和访问检查从 Pan 现有入口延续，不能只靠知道 terminal ID 就允许执行命令。
输出不会自动成为 Agent 对话内容；后续 Agent takeover 按现有 held/生命周期锁接入，
避免同一个 provider 会话同时有两名写入者。

Windows backend 首先评估已实测的 pywinpty/ConPTY；POSIX backend 单独实现。
不要自己重做终端渲染器或伪终端操作系统接口。pywinpty 依赖应限定 Windows，
功能不可用时在创建终端时解释，不阻断其它平台的整个 Pan 安装。

## 可执行验收场景

| 场景 | 必须看到的结果 |
|---|---|
| 新建 shell | 从 Workspace 指定目录启动，真实交互式提示符 |
| 基础输入 | 中文、长命令、方向键、Tab、Ctrl-C、Ctrl-D、粘贴正常 |
| TUI | alternate screen、光标、颜色、鼠标、resize 按实际程序验证 |
| 大量输出 | 内存受限，慢客户端不会拖住其它连接，gap 有明确恢复行为 |
| 自然退出 | 最后一段输出先交付，再收到正确退出码；reader 最终退出 |
| 关闭网页面板 | shell PID/变量/目录/运行程序仍然保持 |
| 网页断线/刷新 | 重接相同 Terminal ID，正确恢复屏幕和输出，无重复输入 |
| Pan 服务结束/重启 | 默认终端结束；已显式 detach 的终端保留，并能重新连接原进程 |
| 显式 runtime detach | 保持原 shell PID/PTY，持久化可核验所有权和重连方式 |
| Adapter 无中断 TUI 探针 | 原 backend/运行中任务保持，控制权转交，不以 resume 新进程充当证明 |
| close/restart 竞争 | 整树退出确认后才允许新进程；迟到输入/resize 被拒绝 |
| 父先退出/后代脱离 | 验证受拥有的后代清理；失败保留明确的可重试状态 |
| 未授权客户端 | 无法列出、读取、输入或关闭不属于自己的终端 |
| Agent takeover | held/恢复与现有 Worker lifecycle 一致，无双 writer |

建议先做公共 PTY/runtime 的接口与 Adapter 可行性探针，再实现网页交互、
默认所有权/显式 detach、重连/容量/清理，并接入 PR #6 的临时自动化用例。
PR #6 确定要合并，但先适配公共核心并验收；无中断 TUI 是否纳入首版由探针结果决定。

## 本次验证记录

运行环境：Windows，Python 3.14；PTY 探针使用 uv 临时环境及 PR 固定的
`pywinpty==3.0.5`、`pyte==0.8.2`，没有向 Pan 运行环境安装依赖。

PR 的 7 个 `tests/test_rewind_*.py` 文件：62 passed。pytest 提示当前全局环境未装
pytest-timeout，配置 timeout 未启用；所有测试自然完成。

独立探针：`audit/pr6_terminal_probe.py`。探针不调用 CBC 或恢复真实文件，覆盖：

- 退出先于 drain 的控制流程复现；
- 没有消费者时的 8 Mi 字符无界排队复现；
- 实际普通父子进程的 fallback 清理顺序复现；
- 实际 pywinpty 中文输入输出/ANSI 解析；
- 实际 PTY 单进程和 Ctrl-C-resistant 子进程清理，两者本次都成功。

复现命令（指定本机 Python 路径是为避免 uv 自动选到 Python 3.11，PR 源码需要较新
Python 的 f-string 语法）：

```powershell
uv run --no-project --python C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe --with pywinpty==3.0.5 --with pyte==0.8.2 --with psutil -- python audit/pr6_terminal_probe.py D:/project/pan-worktrees/pr6-terminal-source-20261002
```

未运行浏览器终端验收、完整前端套件、真实 CBC 回滚或跨平台 PTY 测试。
目前交付的是审查、可复现探针与设计建议，尚未实现完整 Pan 终端产品。

## 官方参考

- Microsoft ConPTY 创建、尺寸、退出与持续 drain：
  <https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session>
- pywinpty：<https://github.com/andfoy/pywinpty>
- xterm.js 及 fit/serialize addon：<https://github.com/xtermjs/xterm.js/>
- xterm.js 输出流控：<https://xtermjs.org/docs/guides/flowcontrol/>
