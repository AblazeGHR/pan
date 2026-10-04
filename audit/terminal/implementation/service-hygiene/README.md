# P2 TerminalService 窄修卫生证据（service-hygiene）

窄修 **TA** 证据：只处理 **F11（哈希口径标注）** 与 **F12（真实心跳用例时序卫生）**，
**不改生产代码、不派子代理、不写全局记忆**。本目录为**新增**，与
`audit/terminal/implementation/service/`（实现自证）、
`.../service-review/`（独立审查，位于另一工作树）**并存、互不覆盖**。

## 0. 身份与纪律

- 注册树：`D:/project/pan-worktrees/terminal-service-hygiene-ds-20261003`
- 分支：`implement/terminal-service-hygiene-ds-20261003`
- HEAD：`cde2dbd867eb7f59eb551df70c4aa9d5baeecfa9`（= 被审/实现提交），开始时 `git status` 空
- 纪律：`apply_patch` 编辑；**无** `stash/reset/push/merge/restart/子代理/全局记忆`；
  不碰其它树、`main`、`practical`、`.workflow`、既有服务/8768；清理只对自有资源做
  **同 handle raw FILETIME + Wait**，**不按 PID 单值 / 名称 / 命令行广杀**。

## 1. 写范围（全部改动）

| 路径 | 性质 |
| --- | --- |
| `tests/test_terminal_service.py` | **改**：仅 `test_real_service_independent_heartbeat_and_same_pid_after_disconnect` 及其必要私有 helper `_await_heartbeat_advance`（+2 常量） |
| `audit/terminal/implementation/service/README.md` | **追加** §7「哈希口径更正」；**不改旧表、不倒写历史** |
| `audit/terminal/implementation/service-hygiene/**` | **新增**：本报告 + 门控/复算脚本 + 证据 |

**生产 0 改**：`git diff --name-only HEAD -- packages/` 为空；`main.py/docs/scripts/tools/.workflow`
零改（见 `evidence/range_diff.txt`）。

## 2. F11 —— 哈希口径更正

**问题**（审查 F11）：实现 README §1 的 source blob 哈希口径不一致——
`launcher.py` 记的是**工作树 CRLF 字节**而非 LF blob；`test_terminal_launcher.py`
是 **63 位截断串**（缺末位 hex）。

**核对**（源 cde `cde2dbd8`，算法 `sha256`，对原始字节）：

| 文件 | 口径A LF blob | A bytes | 口径B CRLF 工作树 | B bytes | 换行数 |
| --- | --- | --- | --- | --- | --- |
| `packages/core/terminal/service.py` | `71e295c0…f59833fbf3` | 86846 | `fb0421ed…27b9a3c3` | 88743 | 1897 |
| `packages/core/terminal/launcher.py` | `4ab84a80…9812b54647` | 32330 | `0223bb7b…293af30ebfc` | 33087 | 757 |
| `tests/test_terminal_service.py` | `aea9065c…9c605c960f` | 60706 | `f52f6a95…1bd8a65b83f8` | 62064 | 1358 |
| `tests/test_terminal_launcher.py` | `ce8b9fab…6091016d4cb` | 83273 | `c8f0b439…92472e0f8` | 85133 | 1860 |
| `docs/design/PAN_TERMINAL_SERVICE_INTERFACES_20261003.md` | `a3a0a346…96c2d8a28c70` | 15852 | `2aff9bfc…7fbe5e7aea45` | 16105 | 253 |

- 口径 A = `git cat-file blob cde2dbd8:<path>` 的 **LF 字节**（锚定权威）。
- 口径 B = A 的字节把每个 `\n` 重写为 `\r\n`（`core.autocrlf=true` 检出形态；
  **实测**与 `cde2dbd8` 检出工作树逐字节一致）。
- **63 位截断补全**：`tests/test_terminal_launcher.py` 的旧声称 = 口径 B 的 63 位截断，
  完整 64 位 = `c8f0b43930d9d199388a2bb67b0e1565e3107f4f609217f7a09278e92472e0f8`。
- 旧 §1 其余三项（`service.py` / `test_terminal_service.py` / 接口文档）等于口径 A，无需更正。
- **不补造历史**：旧表原值保留在 §1；只补完整值与口径定性。
- 完整可复算脚本：`verify_source_anchoring.py`（只读）→ `evidence/source_anchoring.{txt,json}`。
- `emulator_sidecar/node_modules/` 被 `.gitignore:11` 忽略，**不计入** `git status` dirty。

## 3. F12 —— 真实心跳用例时序卫生

**问题**（审查 F12，实际失败日志 `review_uv_service_full_run2_FAILED_flake.txt`）：
`assert 0 > 0`，心跳未在 **6 次快速 input 窗口**内推进。根因是测试时序容忍度不足——
心跳首拍需先完成 attach + HMAC 握手（`_Heartbeat._beat_once` 首次 `_ensure_client()`
含 attach），固定输入窗口未必够一拍。**非产品功能缺陷**（审查已判定，隔离重跑通过）。

**修法**（仅该测试函数 + 私有 helper，保留原断言语义）：
- 断言前的「6 次 input 后立即比较 beats」改为 **有界等待** `_await_heartbeat_advance`
  （预算 15.0s / 轮询 0.1s），**轮询至 beats 递增或超时**。
- **保留** `beats 推进` 断言、断连后**同 PID 存活**断言、**秘密保留**断言；
  **未删除任何断言、未放宽为无条件 True**。`beats_after > before` 仍为硬断言。
- **明确失败诊断**：超时抛 `AssertionError`，附 `before/observed/budget/ticks`，
  **不靠大 sleep、不静默放行**。
- **业务窗口见证**：等待窗口内**持续下发真实业务**（`on_tick` 泵 input），
  返回后断言 `business["sent"] >= 1`。
- **语义边界（不过度声明）**：有界等待只证明心跳**最终**推进；因窗口内业务确被下发，
  可作「业务进行期间推进」的**窗口见证**——仍**弱于**注入门控
  `test_heartbeat_uses_its_own_connection_and_survives_slow_business`（那条直接钉死
  「慢业务不阻塞独立心跳线程」），本用例不据此声称更强结论。

**确定性回归**（自有 audit，`gate_heartbeat_wait.py`，只注入原语、不起真实进程）：
以文件路径加载**真身** `_await_heartbeat_advance`，验证三态：

| 门控 | 场景 | 期望 | 实测 |
| --- | --- | --- | --- |
| G1 | 心跳延迟后推进 | 有界预算内返回，耗时≈延迟（非固定 sleep） | PASS（beats=1，0.313s） |
| G2 | 心跳持续不推进 | 预算耗尽抛 `AssertionError`（显式超时失败，带 `before=` 诊断） | PASS（0.406s） |
| G3 | 业务窗口见证 | `on_tick` 在窗口内被调用（业务下发 > 0） | PASS（5 次） |

自然 flake 的**真实失败原日志引用审查**，不靠多轮赌自然重现。

## 4. 测试责任（本次亲自执行，直连 / uv 各一次）

环境 `E:/software/miniforge/python.exe`；uv 命令均
`uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout --`；
统一 `-o addopts= -q`，保留汇总。

| # | 项目 | 结果 | 日志 |
| --- | --- | --- | --- |
| 1 | 私有门控（直连） | **3 passed / 0 failed**，rc 0 | `evidence/gate_direct.txt` |
| 2 | 私有门控（uv 隔离） | **3 passed / 0 failed**，rc 0 | `evidence/gate_uv.txt` |
| 3 | 原真实心跳函数（直连） | **1 passed / 40 deselected**，rc 0 | `evidence/heartbeat_test_direct.txt` |
| 4 | 原真实心跳函数（uv 隔离） | **1 passed / 40 deselected**，rc 0 | `evidence/heartbeat_test_uv.txt` |
| 5 | 5 件源哈希复算（只读） | rc 0，旧 §1 声称全部可定性 | `evidence/source_anchoring.txt` |

- sidecar 专属 `npm ci`（**exact pin**，锁未改、`node_modules` 不提交，见 §5）。
- **未跑**（按任务边界）：41 全量、其它 launcher/core/组合/全库/provider/browser/长稳。

## 5. 证据索引（`evidence/`）

`gate_direct.txt`、`gate_uv.txt`、`heartbeat_test_direct.txt`、`heartbeat_test_uv.txt`、
`source_anchoring.txt`、`gate_heartbeat_wait.json`、`source_anchoring.json`、
`range_diff.txt`、`cleanup.txt`、`hygiene_findings.json`。

## 6. 清理与残留

- 真实用例清理沿用测试自带 `shutdown()` + **同 handle** `terminate_verified_process`
  （raw FILETIME + Wait），见用例 `finally`；TA **未**另起进程/监听/账号，**未用 8768**。
- 只读扫描：命令行含本工作树者**无**（`evidence/cleanup.txt`）。
- `emulator_sidecar/node_modules/` 保留不删、**不提交**（被 `.gitignore` 忽略）→ 不称「全 clean」。

## 7. 未验收 / 未验证（不得当作已完成）

- 本窄修**未**改动/未复核 F1–F10 等其它审查发现；**未**处理返工主体（服务层功能缺口）。
- **未**运行全量套件；心跳 flake 的**自然重现**未做（引用审查原日志）；
  有界等待只证明**最终**推进（业务窗口见证仍弱于注入门控）。
- Ctrl-C、真实 durable detach、浏览器/fit、Web 鉴权、provider/账号/网络、跨用户/主机、
  POSIX、长稳：均未验收。

## 8. 冻结

单一交付提交后**冻结**：不重跑、不重复确认、不 push/merge。
