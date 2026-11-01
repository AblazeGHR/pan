# CP-X2 返修候选交付

- 工作树/分支：`D:/project/pan-worktrees/xplat-job-posix-safety-20261027` / `xplat/job-posix-safety-20261027`。
- 保留的被审查候选：`43765dbbec4baee7c6d24007b16255a76f496b93`，未 reset/rebase。
- 本轮冻结源码：`42b413240ae8ec54e91bca65eafa7f1c44f932fe`。
- 最终 SHA：包含本报告的后继证据提交，最终答复给完整 SHA；该提交不改变源码。
- 性质：integration-only 返修候选，待 MA 验收；Linux/macOS 真实运行未验证。

## 对四项审查意见的处理

1. **只读退出与发信号分离。** `terminate/kill_tree` 可以利用原 Popen 的退出回执、
   组为空及已观察成员消失，幂等返回已清理，而不调用 `_signal_authority` 或发送
   非零信号。组/成员/僵尸存在、查询失败或成员扫描曾不确定时仍 unknown。wait
   期间保留成员身份，空组不抹掉已观察逃逸成员。同进程重新创建 owner 时，只有
   精确出生身份和 pgid 匹配才可复用实际保留的原 Popen；不会按 PID 伪造句柄。
2. **macOS 实现强身份。** 新增平台私有 `process_darwin.py`，使用 libproc flavor18
   单次复制同一 proc 的 BSD 状态/pgid/timeval 和 kernel uniqueid/idversion；以
   查询前后相同的 boot session UUID 分域。检查 ABI、完整返回、字段和 UUID；
   错误全部 fail closed，不退回弱 ps。exec 可能改变 idversion，同 uniqueid 的
   版本变化保持 unknown，不能假冒 PID 复用后的 supervisor 退出证明。
3. **恢复公共成功路径。** 原调用链未改：canonical public -> IndependentExecutor
   -> 原 runner -> TypedExecutor._process -> ProcessOwner.wait/kill_tree -> 原回执。
   上版 kill_tree 在 root 已退出后无条件申请 signal authority，导致正常执行
   经 process_exception 变成 unknown；现改为充分的只读资源证明后 succeeded。
   新增本地 TypedExecutor 控制流负控，空组成功/残留组 unknown，均不重放；真实
   公共正常成功、冷恢复、迟到成功、取消与失联 unknown 均提供可执行 POSIX 后继。
4. **解释器名称纠正。** 上版把 P23 私有 `.validation` venv 称为 canonical 不准确。
   旧报告及证据保留为被拒候选历史；本报告和新 manifest 明确区分两者。本轮最终
   源码使用指定 Pan-main venv 做完整 Windows/shared/P23 回归，不以旧计数替代。

共享变更仅按 MA 后续明确授权：`test_spawn_binds_exact_identity` 的 kind 白名单
增 `proc_uniqueid`，其余原共享断言/节点/skip 保持不变；ProcessIdentity 仅更新
kind/boot 域说明。未改 schema、结构、注册表或主矩阵。

## Apple 原始依据与未验边界

完整原始 URL、固定 commit、文件 SHA256 和行位置：`apple-sources.json`；
结构表、函数/字段解释和一手来源链接：
`docs/reports/CP_X2_CAPABILITIES_20261027.md` 的 Darwin ABI 段。

固定 Apple XNU commit：`f6217f891ac0bb64f3d375211650a4c1ff8ca1ea`。
本地验证 ctypes 的 BSD=136/align8、unique=56/align8、combined=192/align8，及
各字段偏移和模拟 copyout；这不是 Apple ABI 实测。proc_pidinfo 的 macOS10.5
基础注解不等于 flavor18 的最低版本承诺。本实现仅在符号、flavor、64位 ABI 和
UUID 均实际可用时准入，缺失明确 unknown；没有声称任何历史最低系统版本已支持。

strong identity 不是 containment。已观察逃逸、leader 失联和僵尸保留 unknown；
进程组快照无法完整证明任意并发 fork/escape，外部恢复的检查到 group signal
之间仍没有内核原子句柄。跨进程失联后的完整树证明不足不会变成清理成功。

## 解释器、命令与结果

两解释器本轮现场版本均为 Python **3.14.5**, MSC v.1944 64 bit AMD64。
精确 `sys.executable/version/prefix/basePrefix` 和源码 hash 见 `source-manifest.json`。

- `M = D:/project/Pan-main/.venv/Scripts/python.exe`：用户指定 Pan-main venv。
- `P = D:/project/pan-worktrees/job-p23-retained-safety-20261027/.validation/p23-venv/Scripts/python.exe`：P23 私有验证 venv。

指定 Pan-main 最终组合：

```text
M scripts/run_tests_isolated.py -- M -m pytest tests/test_platform_process_contract.py tests/test_platform_process_posix_unit.py tests/test_job_retained_safety_protocol.py tests/test_job_retained_safety_owned.py tests/test_job_retained_safety_canonical.py tests/test_job_retained_safety_public.py tests/test_job_posix_safety.py -q -o addopts= --tb=short --junitxml=artifacts/cp-x2/revision/pan-main-final.xml
```

exit 0，**128 passed / 28 skipped**，117.95秒；见 `validation.json` 和
`pan-main-final.xml`。28项skip是本地平台门禁，不作为通过。

P23 私有 venv 最终新增集合（同一冻结源码）：

```text
P scripts/run_tests_isolated.py -- P -m pytest tests/test_platform_process_posix_unit.py tests/test_job_posix_safety.py -q -o addopts= --tb=short --junitxml=artifacts/cp-x2/revision/p23-private-focused-final.xml
```

exit 0，**80 passed / 9 skipped**，1.27秒。不能称为 Pan-main 完整回归。

Pan-main 收集：

```text
M scripts/run_tests_isolated.py -- M -m pytest tests/test_platform_process_contract.py tests/test_platform_process_posix_unit.py tests/test_job_posix_safety.py --collect-only -q -o addopts= --junitxml=artifacts/cp-x2/revision/collection-final.xml
```

exit 0，**130 nodes collected**，0.48秒；收集不等于运行。

Pan-main 隔离导入（exit 0，`shared imports OK`）：

```text
M scripts/run_tests_isolated.py -- M -c "import packages.core.platform.process; import packages.core.platform.process_posix; import packages.core.platform.process_darwin; import packages.core.platform.execution_host; import packages.web.server; print('shared imports OK')"
```

修前红证据（内存加载被拒候选43765dbb，不回写源码）：

```text
M scripts/run_tests_isolated.py -- M artifacts/cp-x2/revision/run_rejected_candidate_red.py
```

exit 1，**4 failed / 72 deselected**，5.32秒；对应两种自然退出幂等、只读 dead
recovery 和保留原 Popen 的恢复后继。这是 seam 控制流红证据，不冒充原生 POSIX。
第一轮返修 `unit-first.xml` 的3项红分别是两项已由“拒绝信号”改为“只读返回残留”
的自有负控和旧永久unsupported期待；后继更正为新明确契约，原共享断言未削弱。

## 目标平台入口与资源

MA/CP-X1 可直接运行：

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_platform_process_contract.py tests/test_platform_process_posix_unit.py tests/test_job_posix_safety.py -q -o addopts= --tb=short
```

9个公共节点的精确名字和覆盖在能力文档中。公共用例使用真 HTTP 和原 runner/
store/executor，只有首次身份拒绝可注入；不注入成功 proof。新增集合本机9项skip，
Linux预期8公共节点运行、1个macOS专属skip，macOS预期9公共节点运行；实际目标
count/exit 必须由目标运行给出。原共享POSIX套件、native import和公共后继均未验。

`wsl --list --running --quiet` 本轮返回空；未启动未知发行版或安装环境。
完整 Windows P23 回归输出的13份自有实例统计保存到 `windows-runtime-pan-main/`，
核对端口释放和捕获进程无残留后，原 evidence 路径恢复为本轮前的 bytes。

没有 main/practical 推进、merge integration40e0d856、push、远端CI、保护服务操作、
真实data、8767/8768、前端变更或子agent。本轮通过 Pan skill 的 `agent_send_force`
向 MA 报送精确共享需求；回执 queued 仅证明入队，不代表 MA 验收。

最终核对：原共享契约文件逐字比较仅增加授权kind；process.py 去除说明文字后的
可执行 AST 与43765dbb相同。13份自有实例统计端口均释放、捕获进程无残留，
共回收10个supervisor。源码hash与冻结manifest一致。

证据格式核对曾发现 pytest 红 XML 内两行尾随空格；原始 bytes 保存在
`rejected-candidate-red.raw.zip`，可读 XML 统一 CRLF 换行并去尾随空白，count/exit 不变，
前后 SHA256 见 `artifact-format.json`。源码和断言未修改。
