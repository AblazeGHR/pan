# CP-X1 生命周期 followthrough 候选

本轮继续原 Session/worktree/model，起始清洁候选为 `3f32925733d1940154d6033cac084977f1bbdfdb`，
按 brief 正常 FF 到 MA 精确组合基线 **`4be0f6c788b1b544c4e2d6722ee0771a01a5db56`**。
当前分支 `xplat/job-fast-gates-20261027`，本轮没有改产品、平台、Card、quota、全局 conftest 或 CI。
MA 正在运行的冻结候选未被修改、push 或重新触发。

## 有界生命周期回归

由自有 Pan durable Job 启动，使用用户指定 venv 和隔离 wrapper：

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_main_service_exit.py tests/test_main_service_restart.py tests/test_legal_offline_http.py tests/test_job_lifecycle_prevention.py -q -ra --junitxml=evidence/cp-x1-lifecycle-followthrough-20261029/lifecycle.xml
```

Job `job_cfa3e05c007359e8bea667af` 终态为 failed、exit 1；原始 Runner 日志路径为
`D:/project/Pan/data/background_jobs/logs/job_cfa3e05c007359e8bea667af.log`。
JUnit `evidence/cp-x1-lifecycle-followthrough-20261029/lifecycle.xml` 已将 hostname
脱敏为 `windows-local`，失败 traceback 原样保留在 Job 日志。

| 原测试组 | testcase | passed | failed | error/skip |
|---|---:|---:|---:|---:|
| `test_main_service_exit.py` | 13 | 6 | 7 | 0 / 0 |
| `test_main_service_restart.py` | 14 | 7 | 7 | 0 / 0 |
| `test_legal_offline_http.py` | 20 | 13 | 7 | 0 / 0 |
| `test_job_lifecycle_prevention.py` | 22 | 10 | 12 | 0 / 0 |
| **合计** | **69** | **36** | **33** | **0 / 0** |

这不是全 Windows 回归，也没有把历史 run 的通过数混入本轮。失败 XML 仍为红证据。

## 调用链和精确分诊

受测 canonical 路径是：

`packages/web/server.py::api_system_main_lifecycle`
→ `_canonical_lifecycle_handoff`
→ `packages/core/main_lifecycle.py::ProtectedLifecycleExecutor`
→ `ExecutionStore` / `launch_execution`。

它生成固定 `system_main_lifecycle` Action 的 Job/Run/Attempt/effect 身份，校验 root、port、
old PID/birth、stepCount=1 和 capability，再在 receipt store 中 admit。`run_canonical_supervisor`
通过 `stopping → stopped → starting/checking_ready` 阶段保存 stop/offline/ready proof；unknown
和未确认结果保持 unknown，不能重发或伪造成功。

### 15 个 canonical lifecycle transition 节点

原断言仍要求 ask 的显式 choice、冻结 `exitStrategy` / `markRunningSessionsOffline` / running
Session snapshot、重复请求拒绝、Worker shutdown 先于 supervisor handoff，以及 durable job
状态/receipt。远端和本机现象主要是：测试仍通过 `background_jobs.find_service_job` 读取旧
service-job registry，而当前 API 已把真实执行交给 `_canonical_lifecycle_handoff` 的
`ExecutionStore`；在旧 registry 上得到 `None`、缺 `status` 或“supervisor must wait”并不证明
canonical effect 未发生。

最小后续接口是测试 fixture/observation adapter：从 `lifecycle_action.protected_plan` 得到
原 requestId 的 deterministic job/run/effect identity，再用 `ExecutionStore.get` 和
`ExecutionHost` 只读观察 phase/result；保留原 ask/option/duplicate/stop-before-start 断言。
若兼容旧 CLI 读取仍是产品要求，需 MA 决定 `server._canonical_lifecycle_handoff` 是否提供
只读 projection；不能恢复 legacy writer 或让测试继续假定旧 registry 是事实源。

### 7 个 owned host exit timeout 节点

`test_legal_offline_http.py` 的 `safe-api`、`preserve-api`、`q-config`、`q-menu`、
`q-menu-2`、`q-menu-3`、`q-triple` 都在等待自有 host `process.poll() is not None` 时超时。
这些路径会先通过真实 HTTP/console/queue 入口请求生命周期，再要求原 host PID 退出、端口释放、
owned tree 清理和 durable offline evidence。当前 traceback 没有证明是另一服务或端口占用；本机
只记录为 **owned-host exit behavior unresolved**。

最小诊断接口应在 owned harness 中记录原 host PID/birth、shutdown requestId/effectId、
`api_internal_main_shutdown` 返回、`PanServer.should_exit`、listener owner、process exit code
和 cleanup result；不能增加等待时间、吞异常或把 process 仍活着标为成功。产品相关符号为
`packages/web/server.py::api_system_main_lifecycle`、`packages/core/main_lifecycle.py::run_canonical_supervisor`
和 `packages/core/host_server.py::PanServer` 的 shutdown/lifespan 交界，交 MA 决定后再改。

### 3 个 controller tree setup 节点

`test_job_lifecycle_prevention.py` 中 3 个节点仍使用
`tests/helpers/lifecycle_controller_host.py` 的 legacy 分支（`jobs.start` / legacy runner）来
制造 controller crash/post-spawn 场景；当前 `packages/jobs/retired.py::missing` 明确拒绝该入口。
结果是 `RetiredEntryPoint` 或 `tree-0.json` 未产生，不能作为 canonical lifecycle capability
丢失的证明。

最小后继是为同一测试语义建立 owned canonical executor fixture：提交固定 process Job，保留
controller/runner PID+birth、crash 时点、原 Attempt/effect、子树成员和取消/无 survivors 断言。
不能恢复 `background_jobs.start`，不能让 legacy fixture 借权限通过，也不能用 synthetic receipt
代替真实 process identity。

## 结论和交付边界

本轮得到 36 个本机通过、33 个真实红节点和精确的后续接口建议；没有需要本任务直接修改的
产品符号获授权。当前候选保持 clean，MA 可在其组合分支中选择 fixture observation adapter
或另派产品修复。没有 push、远端 CI、服务运维、真实 profile/data/account 或 8767/8768 操作。
