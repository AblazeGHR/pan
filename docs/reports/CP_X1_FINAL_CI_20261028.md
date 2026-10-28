# CP-X1 连续任务：最终 CI 接线与远端 ref 提案

组合基线：`41a5ffd0b62787f8d7eb97f0a801921d0db31a54`。
受测/拟远端执行源码：**`5adbf3662b8e06e8eae13bf4aa5320a336bfa7a7`**。
分支 `xplat/job-fast-gates-20261027`；仍在原工作树
`D:/project/pan-worktrees/xplat-job-fast-gates-20261027`。
开始时原候选 `7f1cad9f` clean 且为组合基线祖先，仅快进本任务分支至指定组合基线。
没有新建 TA、合其他分支或操作 main/practical。

## 完成的接线

`scripts/platform_fast_gate.py` 在原 **21 个文件/节点条目**之上添加三个完整执行文件：

- `tests/test_worker_platform_admission.py`
- `tests/test_quota_native_admission.py`
- `tests/test_job_process_logs.py`

原顺序及全部条目保留，包含 MA 已整合的 CP-X2 `test_job_posix_safety.py`。
三平台 Python 3.12 的 import、全仓 collect、fast 阶段、失败传播、平台 skip、
15 分钟 Job 超时和 always artifact 均不变。添加专属 helper 回归，确保三个文件进入
实际执行命令且没有只收集、ignore、deselect 或 `-k` 筛选。
现有 Windows Python 3.12/3.14 全量 Job、原触发器和 `platform-fast` Job 经 YAML
解析与基线比较完全相等。没有增加 `continue-on-error`、xfail 或原测试 skip。

新增独立 `ubuntu-browser` Job（Ubuntu、Python 3.12、20 分钟上限）：

1. checkout 后立即创建新证据目录，并只在这个一次性 CI checkout 中清空两个 fixture
   的生成输出目录 `artifacts/notification-error-exclusions/http` 与
   `artifacts/history-terminal/http`。这些目录在基线含历史结果；不清空就可能在 setup
   失败时被 always-upload 当成本轮结果。没有更改 Git 中的原历史证据。
2. 使用现有 Node 24、pnpm 9.7.0、`pnpm install --frozen-lockfile` 与 `pnpm build`。
3. 安装 minimal、QQ、pytest、pytest-timeout、Python Playwright **1.63.0**，与
   pnpm lock 的 `@playwright/test`/`playwright` **1.63.0** 一致。
4. `python -m playwright install --with-deps --only-shell chromium` 安装 Chromium shell
   及 Linux 系统依赖；测试与安装使用同一个 runner 临时 browser cache。
5. 通过隔离 wrapper 顺序运行 `tests/test_notification_http.py` 和
   `tests/test_history_terminal_http.py`，保存 JUnit/stdout。显式 `shell: bash` 使用
   Actions 的 `-e -o pipefail`，pytest 失败不会被后面的 `tee` 吞掉。
6. `if: always()` 上传本轮 JUnit/log，以及两个 fixture 的浏览器、HTTP 与 cleanup 输出。
   Job 没有依赖其他 Job，Windows 失败不会取消该独立慢层。

官方依据：[Actions shell 语义](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepsshell)、
[Playwright Linux 依赖及 headless shell](https://playwright.dev/python/docs/browsers#install-system-dependencies)。

## Fixture 边界检查

两条 browser 测试复用 `_PanRuntime`、`automation_http_server.main`、
`isolated_http_server.main`。它们启动自有真实 FastAPI/uvicorn、生产前端、认证 HTTP/WS
与 Chromium，用临时 profile 和隔离 wrapper 分配的非 8767/8768 端口。
通知 fixture 将 QQ/System sender 替换为记录器并禁止启动 CLI Worker；history fixture
使用 `history_terminal_fake_cli.py`。不会触达真实账号、QQ/System 通知或供应商。

本轮源码审查没有发现这条共享 fixture 链上无条件 Windows-only 的入口；cleanup 使用
Popen 与 psutil 的已记录 PID/birth identity。process-log symlink 测试已有明确平台分支：
Windows junction / POSIX symlink。**这些是源码检查，不是 Ubuntu 行为证明。**
共享 fixture、产品模块和原测试均未修改。Linux 若暴露问题必须保存远端失败，再向 MA
报告具体符号/平台接口；不能以新增 skip 或改产品断言补绿。

## 本轮本地验证

指定解释器 `D:/project/Pan-main/.venv/Scripts/python.exe` 实际为 **Windows Python 3.14.5**。
没有安装 canonical 依赖、构建前端或重复 MA 先前的浏览器验证。
所有验证均在源码 `5adbf3662b8e06e8eae13bf4aa5320a336bfa7a7` 的相同代码上执行。

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_platform_fast_gate.py tests/test_worker_platform_admission.py tests/test_quota_native_admission.py tests/test_job_process_logs.py -q -ra --durations=15 --junitxml=evidence/cp-x1-final-20261028/local-added.xml
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x1-final-20261028/validate_ci.py
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe scripts/platform_fast_gate.py --phase collect --output evidence/cp-x1-final-20261028/collection
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe scripts/platform_fast_gate.py --phase import --output evidence/cp-x1-final-20261028/import
git diff --check
```

| 检查 | 本轮结果 | exit |
|---|---|---:|
| helper 专属回归 | 4 passed | 0 |
| Worker admission | 14 passed | 0 |
| quota native admission（含真实进程重启） | 17 passed | 0 |
| process logs（含真实输出、冷读、取消、鉴权） | 20 passed | 0 |
| 上述一次组合执行 | **55 passed / 0 failed / 0 error / 0 skipped，204.219 秒** | 0 |
| 全仓 collection：tests、QQ、WeChat | **7,834 项，0 collection error**，10.267 秒 | 0 |
| 真实 server import | win32 app 导入通过 | 0 |
| workflow/dependency/preserved-red 结构校验 | 原 jobs/触发器、21 项旧清单、锁版本及保护文件不变 | 0 |
| diff 检查 | 通过 | 0 |

本次没有运行整个扩大后的 fast 集合、Windows 全量、React build 或两条浏览器用例。
之前 MA 的测试计数没有混入本轮结果。新增三个执行模块无 skip；原 ProcessOwner 的
相反宿主 skip 条件完全保留，完整三平台 skip 清单需远端实际运行后从各自 JUnit 提取。

证据根 `evidence/cp-x1-final-20261028/`：`local-added.xml`、`summary.json`、
`workflow-validation.json`、可重放 `validate_ci.py`、collection/import 的日志及阶段 argv。
JUnit 只把 hostname 脱敏为 `windows-local`，计数与结果不变。
`fixture-proof/` 保存本轮 quota TCP/两进程重启和 14 份 process-log cleanup 证明；
后者全部 `portReleased=true`、`supervisorsRemaining=[]`，quota 的
`allOwnedProcessesStopped=true`。fixture 中原有 `sourceCandidate` 是其历史说明，
本轮受测 SHA 以本报告和 summary 为准。测试会覆盖两个旧 quota JSON 输出，本轮已先复制
新结果至独立证据目录，再恢复旧路径的基线内容；没有提交历史结果覆盖。

结构 verifier 首次因将工作树 CRLF 与 `git show` 的 LF 原字节直接比较而退出 1；
`verifier-initial-red.txt` 保留诊断。只修正独立 verifier 为 Git clean-filter blob 比较，
复验退出 0。没有修改任何产品测试、原断言或原红证据。

## 原三个 quota 红项仍然可见

原文件及 `evidence/quota-native/original-red.log` 的 Git blob 与组合基线一致，
hash 记录在 `workflow-validation.json`。本轮没有重跑这些原项，更没有改为 skip：

| 保留的原测试 | 基线红项原因 / 严格 successor |
|---|---|
| `test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown` | 原要求旧 writer fence；canonical once admission 已允许。对应新真实进程 restart 用例 |
| `test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http` | 原 `monkeypatcreal_http` 拼写导致 NameError；对应新真实进程 restart 用例 |
| `test_job_retirement_quota_transport.py::test_quota_real_tcp_cas_reopen_and_legacy_writer_fence` | 原绑定后的 legacy fence 期待与迁移不符；对应 restart 及 unbound fence 用例 |

精确既有映射见 `evidence/quota-native/PROCESS_HTTP.md`。原项仍包含在 Windows 全量
`tests/` 命令中；不能预告该全量会绿，也不能把本轮 55 项通过抵消它们或其他全量失败。

## 具体远端执行提案（待 MA 授权，本轮未执行）

- 已核对本地 origin 为 **`https://github.com/AblazeGHR/pan.git`**。
- 拟新 ref：**`refs/heads/feat/job-unification-validation-20261028`**。
- 拟推送的精确源码：**`5adbf3662b8e06e8eae13bf4aa5320a336bfa7a7`**。
  它包含组合基线及最终 CI 代码；其后的本地报告/证据提交不改变受测代码。
- MA brief 说明该 ref 已检查不存在；执行时仍应重新核对。若已存在，停止并由 MA 决策，
  不覆盖、不 force、不自动换 ref。
- 该 `feat/**` ref 已匹配原 push 过滤器，因此一次获准 push 会运行 **6 个 Job**：
  Windows full 3.12、Windows full 3.14、三 OS fast 3.12、Ubuntu browser 3.12。
  仓库当前只有 `test.yml` 一个 workflow。不要额外 dispatch 或同时建 PR 造成重复成本。

以下仅为审批后的命令，不是本轮已执行动作：

```text
git ls-remote --heads origin refs/heads/feat/job-unification-validation-20261028
# 仅当输出为空，且 MA 已明确授权这个 SHA/ref 的 push 和 CI 成本：
git push origin 5adbf3662b8e06e8eae13bf4aa5320a336bfa7a7:refs/heads/feat/job-unification-validation-20261028
gh run list --repo AblazeGHR/pan --workflow test.yml --event push --branch feat/job-unification-validation-20261028 --limit 5
gh run view <run-id> --repo AblazeGHR/pan --json headSha,event,status,conclusion,jobs,url
gh run download <run-id> --repo AblazeGHR/pan --dir <owned-download-directory>
gh run view <run-id> --repo AblazeGHR/pan --log-failed
```

必须确认 `headSha` 为上述精确 SHA、event 为 push，并保存 run URL/ID、6 个 job 的结论
及下列 6 个 artifact：`pytest-results-3.12`、`pytest-results-3.14`、
`platform-fast-windows-latest-py312`、`platform-fast-ubuntu-latest-py312`、
`platform-fast-macos-latest-py312`、`ubuntu-browser-py312`。
逐个平台核对真实安装/import/全仓 collection/执行/skip；Ubuntu 慢层需核对两个实际
testcase、认证 browser JSON 与自有 PID/端口 cleanup，不能只看 artifact 名或 job 上传成功。
setup 失败、缺 artifact、timeout、原红项或其他失败均独立保留，不改为成功。
三平台实际 runner、全量、原红项现状和 Ubuntu 浏览器是否可用须由该次运行证明。

## 交付边界

本轮只改 workflow、独立 gate helper、专属 helper 测试及新报告/证据。
没有产品/schema/fence/进程所有权/业务断言修改，未安装 canonical 依赖。
未 push、dispatch、改默认分支、移动 main/practical、操作既有服务/账号/rotation Job，
未使用真实 profile/data 或 8767/8768。新增测试运行的是隔离的自有临时实例。
Linux/macOS/Python 3.12、Ubuntu 实机浏览器、完整扩大后的 fast 组合、Windows 全量、
远端 CI、真实供应商、部署、真实数据迁移与人工验收均仍未验证。
