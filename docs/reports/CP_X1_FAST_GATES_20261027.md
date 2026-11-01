# CP-X1 三平台持续快层候选

基线：`20955b3c8959c881797d84bcd03302158ddf08c3`。源码提交：
`aea397157f03c7649c4b2625eef760ce0325e9ec`。
分支：`xplat/job-fast-gates-20261027`；工作树：
`D:/project/pan-worktrees/xplat-job-fast-gates-20261027`。

本候选只涉及 CI、独立 helper、专属测试和本报告/证据。没有修改平台实现、
Job 产品、P23 测试、共享 conftest 或跨平台主计划。没有合入 CP-X2 分支。
MA 负责整合 Job 树及更新统一能力矩阵；本候选不代表整体验收。

## 行为与范围

`.github/workflows/test.yml` 保留 Windows Python 3.12 / 3.14 全量 Job，包含原有
React 构建、终端侧车、Codex、浏览器及 `tests/ packages/qq` 执行命令。
比较基线与候选的完整 `pytest` Job，去除空行/注释后逐字一致。

原 `posix-fast` 替换为 `platform-fast`：Windows / Ubuntu / macOS 均固定 Python
3.12，矩阵 `fail-fast: false`，单 Job 超时 15 分钟。每个平台安装 minimal、QQ、
pytest、pytest-timeout、Playwright Python 包。Playwright/QQ 是全仓收集的导入依赖；
此快层不下载浏览器、不构建前端、不运行模型 CLI。

`scripts/platform_fast_gate.py --output <独立证据目录>` 顺序运行三个独立阶段：

1. 通过隔离 wrapper，在真实宿主解释器导入 `packages.web.server`，检查真实 app；
   导入前把 config 文件指向自有临时目录，不进入 server lifespan、不启动服务。
2. 全仓 `pytest --collect-only`：显式包含 pytest.ini 的全部三个 testpaths，
   即 `tests/ packages/qq/ packages/wechat/`。不使用 ignore、deselect、缩小子集重试
   或 `--continue-on-collection-errors`。
3. 运行 `FAST_TESTS` 中明确列出的文件/节点。ProcessOwner 两个平台实现复用原契约，
   相反宿主实现仍按原 `skipif` 显式跳过；不会通过改写 `sys.platform` 声称实机通过。

每阶段都调用 `scripts/run_tests_isolated.py`，保留真实子进程退出码、UTF-8 日志，
pytest 阶段输出 JUnit。即使 import/collection 失败，仍执行后续阶段保留独立证据，
最终返回首个非零退出码，收集错误不能因快层通过而变绿。CI 使用 `if: always()`
上传每个平台独立的日志/XML/结果 JSON；没有 `continue-on-error`。

专属 runner 回归使用真实 Python 子进程构造 collection exit 2，验证 fast 仍执行、
日志仍保留且整体仍返回 2。这个故障注入是 helper 契约测试，不是产品故障或平台实测。

## 快层清单与选择理由

完整节点清单唯一来源是 helper 的 `FAST_TESTS`；以下为实际 Windows 计数。

| 范围 | passed | skipped | 目的 |
|---|---:|---:|---|
| platform layer / API / portability paths | 32 | 0 | 能力、unsupported 和路径契约 |
| import chain 的两个静态 AST 节点 | 2 | 0 | 共享模块导入边界；未选择模拟 POSIX 用例 |
| ProcessOwner 共用契约与 factory/guards | 22 | 19 | 真实 Windows Job Object、身份、wait、终止、树清理、恢复与拒绝 |
| POSIX ProcessOwner 控制流单测 | 25 | 0 | seam 单测；不算真实 POSIX 内核证据 |
| runtime owner | 28 | 0 | epoch CAS、跨进程锁释放、单 owner、旧 writer fencing、unknown 不重放 |
| Job attempt epoch | 3 | 0 | attempt/owner epoch 与迟到回执拒绝 |
| typed executor | 19 | 0 | argv、cleanup proof、unknown/partial、provider 幂等回执 |
| independent execution 的九个显式节点（含参数化） | 15 | 0 | 并发 admission、stale fence、原身份重查、事务故障、两条真实 detached 自有探针 |
| CP-X1 helper 专属回归 | 3 | 0 | 全仓根、失败传播、非法阶段拒绝 |
| **总计** | **149** | **19** | **168 项，0 failure / 0 error** |

没有把生命周期服务/HTTP/浏览器慢层整包塞进快层。两条 detached 测试只创建临时目录与
自有 Python 命令，通过持久 receipt 和树清理 proof 断言执行结果；它们不操作 Pan 服务。

Windows 本轮 19 个 skip 全部来自 `tests/test_platform_process_contract.py`：
`TestPosixProcessOwnerContract` 的 18 个用例，以及
`test_windows_owner_rejects_construction_on_posix`。逐节点与原始 reason 见
`evidence/cp-x1-windows-final/summary.json` 和 `fast.xml`。
真实 POSIX 预期反向跳过 Windows 类的 18 个用例及
`test_posix_owner_rejects_calls_on_windows`；这是代码预期，尚未实测。
没有新增产品测试 skip/xfail，也没有修改任何原断言。

## Windows 本机复验

使用用户指定的 `D:/project/Pan-main/.venv/Scripts/python.exe`，实际版本 **3.14.5**。
没有安装或更改 canonical 依赖。完整命令：

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe scripts/platform_fast_gate.py --output evidence/cp-x1-windows-final
```

| 检查 | 结果 | exit |
|---|---|---:|
| 真实 server import | `sys.platform=win32`，真实 app 存在 | 0 |
| 全仓收集 | **7,427 项**；覆盖三个根；0 collection error | 0 |
| 核心契约与 CI helper 回归 | **149 passed / 19 skipped / 0 failed / 0 error** | 0 |
| helper 总命令 | 所有阶段完成 | 0 |
| workflow 结构与保留契约 | 已有 PyYAML BaseLoader 解析；三 OS/3.12、Windows 双版本、触发器与 always artifact 断言通过 | 0 |
| `git diff --check` | 通过 | 0 |

冻结源码后的分阶段完整 argv、耗时、退出码：
`evidence/cp-x1-windows-final/results.json`。全仓收集按 pytest 的每文件计数求和，
不是把 collect-only JUnit 的 `tests=0` 当成已运行测试。
分组计数、19 个 skip 节点和静态保留检查：`summary.json`。
首轮绿色证据另存 `evidence/cp-x1-windows-initial/`；首轮 stdout 为 Windows 本地编码，
因此最终 helper 显式设置 UTF-8 后重验。JUnit 仅将 hostname 脱敏为 `windows-local`，
测试结果/计数/skip reason 未改写。没有全仓收集红项需要缩小范围或跨范围修复。

## 授权后触发 CI 的明确途径（本轮未执行）

自动 push 仍只匹配 `main`、`test`、`feat/**`，没有新增 feature/integration/xplat 通配。
不能假设 `integration/...`、`feature/...` 或本分支的 push 会触发这个 workflow。
`pull_request` 事件保持原状；创建/更新 PR 可能产生正常 CI 成本，也须在授权范围内操作。

新增 `workflow_dispatch`，checkout 使用所选运行 ref。根据
[GitHub 官方手动运行说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)，
手动运行需要 workflow 已出现在默认分支，操作者需要仓库写权限。
MA 只有取得 push/远端 CI 授权后才可执行以下流程；这些示例不是现时授权：

1. 核对获准远端仓库及候选 branch/tag 指向的 SHA。先确认默认分支已注册包含
   `workflow_dispatch` 的 `test.yml`；本轮没有修改默认分支。如果尚未注册，需要先获准
   正常整合，或走已获准的 PR 事件，不能声称 feature 分支上新增 dispatch 就立即可用。
2. 对已存在且包含本 workflow/helper 的获准 ref 执行：

   ```text
   gh workflow run test.yml --ref <authorized-branch-or-tag>
   gh run list --workflow test.yml --event workflow_dispatch --branch <authorized-branch> --limit 5
   gh run view <run-id> --json headSha,event,status,conclusion,jobs,url
   gh run download <run-id> --dir <owned-evidence-directory>
   ```

3. 核对 `headSha` 是获准候选，分别检查三平台 fast 和 Windows 3.12/3.14 full 的 jobs、
   结果与 artifacts。记录每个平台真实安装、import、全仓收集、pass/skip/failure；不得
   用本机 3.14 或一个平台通过替代三平台 3.12。分支可能移动，SHA 不匹配时不作为候选验收。

## CP-X2 组合接口与未验证层

已向 CP-X2 Session `ses_ccbec39f8478974f` 发出测试清单协调请求，说明 CP-X1 的范围、
现有 ProcessOwner/runtimeOwner/Job 清单和不吸收分支边界。交付时尚未收到新节点清单。
MA 整合 CP-X2 后应把确认的专属文件/节点显式加入 `FAST_TESTS`，并重新全仓收集和运行
三平台快层；不应以“文件不存在则忽略”的可选发现方式悄悄跳过 CP-X2 门禁。
本 helper 的 `--phase fast --output <目录>` 可独立复验，未注册的新测试必须由 MA
明确加入清单后才属于持续快层。CP-X2 变更若使旧 seam 测试失效，保留红证据交 MA 协调，
本候选不会弱化原测试或合其产品代码。

**尚未验证**：三平台 Python 3.12 实机与干净依赖安装、Linux/macOS 真实内核行为、
远端 workflow 调度/runner/artifact、Windows 3.12/3.14 全量、CP-X2 组合、真实 HTTP/浏览器、
供应商、部署、真实数据迁移及人工验收。YAML 解析不是 GitHub runner 执行证明。
未发现需本任务跨所有权修改的产品缺陷；目标平台运行后仍可能暴露导入/收集或契约问题。

没有移动 main/practical，没有 push、触发远端 CI、重启/操作服务、使用真实 data/profile
或监听 8767/8768。只在当前候选分支提交，供 MA integration-only 整合。
