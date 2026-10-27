# CP-X2 候选交付

- 工作树：`D:/project/pan-worktrees/xplat-job-posix-safety-20261027`
- 分支：`xplat/job-posix-safety-20261027`
- 基线：`20955b3c8959c881797d84bcd03302158ddf08c3`
- 冻结源码 SHA：`eb214dcac23bb52f72b2f679f75dba60c5e2cc90`
- 最终 SHA：包含本报告的证据提交；用 `git rev-parse HEAD` 取得。证据提交不改变源码。
- 交付性质：integration-only 候选，供 MA 组合 Job 树；不代表 CP-X2 目标平台验收通过。

修复与边界见 `docs/reports/CP_X2_CAPABILITIES_20261027.md`。产品代码仅改
`process_posix.py`：秒级 lstart 不授予所有权；macOS spawn 在副作用前显式拒绝；
Linux 缺 boot ID 不准入；每次 group signal 前检查出生身份和当前组；leader 退出/
僵尸不授权旧 pgid；观察到逃逸成员则拒绝；清理后复核已观察成员和僵尸；spawn
abort 不退回裸 PID kill、不把 wait 失败报告为已清理。共享 schema/API/fence 未修改。

## 命令与证据

以下命令均从本树运行。`P` 为最终 canonical venv：
`D:/project/pan-worktrees/job-p23-retained-safety-20261027/.validation/p23-venv/Scripts/python.exe`。
第一轮 Windows 回归用 `D:/project/Pan-main/.venv/Scripts/python.exe`。

最终组合回归：

```text
P scripts/run_tests_isolated.py -- P -m pytest tests/test_platform_process_contract.py tests/test_platform_process_posix_unit.py tests/test_job_retained_safety_protocol.py tests/test_job_retained_safety_owned.py tests/test_job_retained_safety_canonical.py tests/test_job_retained_safety_public.py tests/test_job_posix_safety.py -q -o addopts= --tb=short --junitxml=artifacts/cp-x2/windows-regression-final.xml
```

最终组合：exit 0，92 passed / 27 skipped / 2 warnings，118.17 秒；见
`validation.json` 和 `windows-regression-final.xml`。两个 warnings 来自现有
websockets/uvicorn 弃用提示。第一轮
`windows-regression-first.xml`：exit 0，89 passed / 25 skipped，116.92 秒。
第一轮后新增了三个负控和两个目标平台节点；不得把两轮 count 混用。

新增集合：

```text
P scripts/run_tests_isolated.py -- P -m pytest tests/test_platform_process_posix_unit.py tests/test_job_posix_safety.py -q -o addopts= --tb=short --junitxml=artifacts/cp-x2/final-focused-v3.xml
```

exit 0，44 passed / 8 skipped，1.17 秒。随后仅加强 Linux 公共后继的完整树 proof
断言；最终组合回归覆盖冻结源码。八项真实目标平台测试本地均 skip，不能算通过。

收集：

```text
P scripts/run_tests_isolated.py -- P -m pytest tests/test_platform_process_posix_unit.py tests/test_job_posix_safety.py --collect-only -q -o addopts= --junitxml=artifacts/cp-x2/collection-final.xml
```

exit 0，52 nodes collected，0.42 秒。`--collect-only` JUnit 不记录运行用例，不能当作执行结果。

隔离导入：

```text
P scripts/run_tests_isolated.py -- P -c "import packages.core.platform.process; import packages.core.platform.process_posix; import packages.core.platform.execution_host; import packages.web.server; print('shared imports OK')"
```

exit 0，输出 `shared imports OK`。只证明本地 Windows 隔离导入。

修前行为红证据：

```text
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe artifacts/cp-x2/run_baseline_red.py
```

脚本只在内存载入基线 `process_posix.py`，不回写当前源码，然后运行新增负控。
`baseline-behavior-red.xml`：exit 1，10 failed / 2 passed / 29 deselected，15.85 秒；
复现弱身份被认可、leader 已退出/僵尸/组变化仍发信号、leader 退出后仍升级信号、
macOS 未在 Popen 前拒绝。此为 seam 控制流证据，不是真实 POSIX 内核证据。
首次 `baseline-red.xml` 因新增 snapshot seam 尚未兼容基线而红，属于 harness
错误，保留但不作为缺陷证据；后继使用兼容 seam，未放宽行为断言。

## 资源和权限

P23 原测试会写原有 evidence 路径。本轮仅把自己生成的 JSON 复制到
`windows-runtime/`（首轮）和 `windows-runtime-final/`（终轮），再恢复原路径的
基线 bytes；没有把它们混入 P23 原证据。各轮保留 13 个自有实例统计，检查
`portReleased`、`canonicalPortReleased`、`ownedProcessesRemaining`，终轮汇总见
`validation.json`。未创建 POSIX 测试子进程；八个后继在本机显式 skip。

不派子 agent；未改 main/practical、push、远端 CI、保护服务、真实 data 或
8767/8768。未启动 WSL 发行版、未安装目标环境。无前端变更，因此不跑前端构建。

## MA / CP-X1 后继门禁

- CP-X1 快层入口：`tests/test_platform_process_posix_unit.py`（44 项）。
- CP-X1 Linux 慢层入口：`tests/test_job_posix_safety.py` 的七个 Linux 节点。
- CP-X1 macOS 入口：`test_macos_public_execution_refuses_weak_birth_before_effect`
  加快层；它验证明确 unsupported，不是功能支持。
- 八个完整节点名、目标命令和逐项覆盖在独立 capability 文档中；不合 CP-X1 分支。
- `CAPABILITIES["processOwner"].supported_on` 仍含 DARWIN，必须由 MA 协调对齐，
  或补可靠 macOS 原生出生身份后实测。共享契约的 macOS spawn 期待也需由 owner
  协调显式 unsupported 契约；本 TA 未修改注册表/主矩阵/原契约或原 P23 skip。
- Linux/macOS 本轮均未实测，包括目标平台 import、原契约和公共 HTTP 后继。
- Linux group snapshot 不能证明任意并发 fork/escape 的完整树包含，也没有消除
  外部恢复 check-to-signal 的竞态；leader 已退出后的资源回收没有新的授权机制。
  不得把本候选写成 Job Object 等价能力或全树回收已验收。
- 源码、Windows 单测/真实 Windows P23 HTTP、目标平台未验、远端 CI 未跑、部署/
  迁移/人工验收未做，均为不同证据层。
