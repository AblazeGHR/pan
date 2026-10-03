# P2 第一批：TerminalService 实现证据（service）

本目录是 **P2 第一批服务控制层**的证据。**只服务本批**（service +
其最小 launcher 接线）；`audit/terminal/implementation/` 下`backend/`、`ipc/`、
`emulator/`、`launcher/`、`runner/`、`composition*/` 等**旧证据未改、未覆盖、未重生成**。

- 工作树：`D:/project/pan-worktrees/terminal-service-implement-20261003`
- 分支：`implement/terminal-service-20261003`
- 任务起点：`6b6118ef`（生产代码基线 `475395c3`）
- 范围与纪律：`docs/design/PAN_TERMINAL_SERVICE_IMPL_BRIEF_20261003.md`、
  接口 `docs/design/PAN_TERMINAL_SERVICE_INTERFACES_20261003.md`

## 1. 源码锚定（source blob）

固定源哈希（UTF-8 字节级`sha256`）：

| 文件 | sha256 | bytes | lines |
| --- | --- | --- | --- |
| `packages/core/terminal/service.py` | `71e295c0283e0a41e03502c8498e662b334cc9f42c872ad9415742f59833fbf3` | 86846 | 1898 |
| `packages/core/terminal/launcher.py` | `0223bb7bc0d6a884a304b97291c8e5140fb276a4b8f3fb51aca35293af30ebfc` | 33087 | 758 |
| `tests/test_terminal_service.py` | `aea9065c388b5f26d344763078a679f0dca92fec4c6e319218b8df9c605c960f` | 60706 | 1359 |
| `tests/test_terminal_launcher.py` | `c8f0b43930d9d199388a2bb67b0e1565e3107f4f609217f7a09278e92472e0f` | 85133 | 1861 |
| `docs/design/PAN_TERMINAL_SERVICE_INTERFACES_20261003.md` | `a3a0a3469d6485198229e9cfcc4fc6e47c6c232c6541282ca19596c2d8a28c70` | 15852 | 254 |

`launcher.py` 与 `test_terminal_launcher.py` 的哈希对应**本批最小接线之后**的状态
（新增可选 `cwd` / `shell_argv` 与其定向测试）；接线前的行数分别为 716 / 1733。

## 2. 实测结果（本批亲自执行）

命令均在工作树根执行，`-o addopts= -p no:cacheprovider`；日志为 UTF-8 `.txt`，
存于 `evidence/`。

| # | 项目 | 命令 | 结果 | 日志 |
| --- | --- | --- | --- | --- |
| 1 | service 全量（直连） | `python -m pytest tests/test_terminal_service.py -o addopts= -q` | **41 passed**（rc 0，75.5s） | `evidence/direct_service_full.txt` |
| 2 | service 全量（uv 隔离） | `uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout --with pyte==0.8.2 -- python -m pytest tests/test_terminal_service.py -o addopts= -q` | **41 passed**（rc 0，76.6s） | `evidence/uv_service_full.txt` |
| 3 | 相邻 registry/lease（直连，各一次） | `python -m pytest tests/test_terminal_registry.py tests/test_terminal_lease.py -o addopts= -q` | **40 passed**（rc 0，0.73s） | `evidence/direct_adjacent_registry_lease.txt` |
| 4 | launcher 注入门控（含本批新增 16 项定向） | `python -m pytest tests/test_terminal_launcher.py -o addopts= -q -k "not real"` | **63 passed / 20 deselected**（rc 0） | `evidence/direct_launcher_injected.txt` |
| 5 | launcher 真机回归（最小对照） | `python -m pytest tests/test_terminal_launcher.py -o addopts= -q -k "real_launcher_bootstrap_snapshot_protocol_a_and_stop"` | **1 passed**（rc 0，10.8s） | `evidence/direct_launcher_real_cwd_control.txt` |

- 第1/2 项各含4 项真实 Windows 用例（真 ConPTY / Job / DPAPI / 命名管道 / headless
  引擎，隔离临时数据根），因此耗时约 75s。
- **未**运行全库、其它旧方向、多轮稳定性刷证据；未运行浏览器/provider/长稳。

### 2.1 环境前置

`packages/core/terminal/emulator_sidecar/` 执行了 `npm ci`（exact pin，未改锁文件；
`node_modules/` 被 `.gitignore` 忽略，**未提交**）。缺此依赖时 4 项真实用例会
`skip`（`requires_sidecar`），本批执行时依赖已就位。

## 3. 先失败后通过（本批发现并修复的真实缺陷）

均为**先失败**观测到、再定位修复、复跑通过；不是测试笔误冒称产品缺陷。

| # | 缺陷 | 复现证据 | 修复 |
| --- | --- | --- | --- |
| F1 | **容量准入与记录持久化不原子**：`_admit` 计数后释放锁，`_create_record` 在锁外 → 6 个并发 create 全部落盘（实测 `6 <= 3` 失败） | `test_concurrent_create_never_exceeds_capacity` | 合并为 `_admit(rows, cols, ..., terminal_id)`：同一 `_admission_lock` 内"计数 → 分配 id → `registry.create`"；超限零记录零进程；重复 id 在锁内换新 id |
| F2 | **close 判定被整体缓存进 worker**：首次"未证明"的结论被永久缓存，后续补齐的引擎收尾/进程退出证据永远读不到，重试永远无法收敛 | `test_close_requires_all_three_proofs`（第二次 close 仍 `CleanupUnconfirmed: explicit-close`） | worker 只覆盖**阻塞的 stop 调用**（`_request_stop`）；launcher 收尾与进程退出证据**每轮重新评估**；迟到成功仍由缓存消费，不发第二个 stop |
| F3 | **`--cwd` 是死参数**：`_launcher_argv` 从不接受/传出 `cwd`，服务层 `create(cwd=...)` 静默失效 | 真实 cwd 断言失败（终端落在仓库根而非临时目录）；独立探针确认 PTY 提示符在 `cwdprobe-*/workdir` | `_launcher_argv(..., cwd=None, shell_argv=None)`：非 `None` 时追加 `--cwd`；`main()` 调用点透传；缺省不传（保持默认） |

对照：`_read_until` 的大小写与`importlib.reload` 污染属**测试自身**问题
（先 needle 转小写却做大小写敏感比较；reload 换掉类对象导致 `pytest.raises`
比对旧类），已修测试，**不计作产品缺陷**。

## 4. 测试分层

- **确定性**（37 项，注入原语，不起真实进程）：导入/构造无副作用（**子进程**验证）、
  容量准入与并发 create、重复 id、四门前不发布 RUNNING、启动部分失败保 owner、
  心跳独立且不被慢业务拖死、心跳丢失如实上报、撤销后零新写、observer 越权、
  控制权转交、resize 分列确认、read gap 不补零/不跳 cursor、F5 bool-or-null 与
  不升级、applied 驱逐提示、close 三项证明、CLOSING 映射、重试不叠加、
  秘密删除时机与写失败如实上报、detach 拒绝零变化、detach 注入机制标注、
  reconcile 未知身份/身份不符/fresh PID absence/不派生替代进程/不复活旧 lease/
  未确认清理分列、shutdown 预算含锁等待/保留 detached/如实收敛、
  公共面脱敏、`created_by` 来源、错误静态化、FILETIME 十进制串、默认 shell/size、
  argv 无 token 且带 cwd。
- **真实 Windows 隔离**（4 项）：默认创建+真实 cwd（提示符回显核对到具体临时目录）
  +中文输入与 read/snapshot/独立心跳/断连同 PID/显式 close 整树收尾；
  慢业务期间心跳推进 + 断连后同 PID 存活 + 秘密保留；真实 ambient detach 拒绝零变化；
  服务宿主崩溃后 lease 自停 + 新实例 reconcile 分列。

## 5. 资源清理纪律

- 真实用例的清理只对**自有**资源做**同 handle 核验**（raw FILETIME + Wait），
  经 `win_pipe.terminate_verified_process`；**不按 PID 单值/进程名/命令行广杀**。
- 真实服务以 `DETACHED_PROCESS` 派生（不随 Pan 服务消亡，符合 detach 前提）；
  测试收尾一律 `shutdown()` + 同 handle 核验终止。
- 未启动既有 Pan / 完整 Web 服务；未使用 8768；未创建账号；未开网络监听。
- 诊断用临时探针脚本（`_tmp_cwd_probe.py`）与临时数据目录已删除，未入库。
- 工作树未跟踪物：仅 `emulator_sidecar/node_modules/`（被 `.gitignore` 忽略），
  **保留不删、不提交**；因此不称"全 clean"。

## 6. 未验收 / 未验证（不得当作已完成）

- **Ctrl-C** OS 语义：未验收。
- **真实 durable detach**：ambient Job 下**维持拒绝**（`detach-refused`、零状态变化）；
  注入替身下的"已 detached"仅验证逻辑，报告标注 `mechanism="runner-reported"`
  **不称真实 durable 验收**；未做 breakaway / 环境逃脱实验。
- **浏览器**渲染/fit、**Web 鉴权**（Origin/CSRF/MCP caller gate）、provider/账号/
  网络服务、跨用户/跨主机、POSIX、**长稳**/慢客户端背压、跨 sidecar 重启恢复：均未验收。
- 预算是**调用方侧有界等待，非 OS 硬 SLA**；真实会话 `partial` 是常态
  （未验证 VT 序列不升级）；F5 确认字段为**保守近似、无历史世代原子绑定**。
- Job 内核退出兜底只按**已测布局**陈述，不泛化为任意部署的通用保证。
- 容量准入是**单服务实例内**硬约束；跨进程并发创建同一数据根不在本批保证范围
  （`registry` 无跨进程容量原语，**未擅自扩展共享协议**）。
- 吞吐/时延数字仅本机探针，不可外推。
