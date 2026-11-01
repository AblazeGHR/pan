# CP-X1 lifecycle 三平台快层接线

基线 `2600a78f2deac4f55cd64e3180b55fcc7339b6c9`；代码候选
`cd696f1f18996a203f6e3d5064c8311bed814f9c`。`FAST_TESTS` 原有 25 个入口全部保留，
仅新增 `tests/test_lifecycle_fast_contract.py` 及原 prelaunch fence 的两个节点；全仓
collection、Windows 3.12/3.14 全量与其他 CI workflow 均未修改。

专属快层调用原 lifecycle 测试行为，覆盖 ask 缺 choice、冻结 Session snapshot、
offline/preserve 选择、先持久再 gate、shutdown 先于 launch、scheduled 非阻塞、
同/跨操作及丢失内存后的原 request/effect singleflight、部分准备/launch 不明、
prelaunch fence、claim 与 launch 标记竞争。受控 launch 测试是共享逻辑单元证据。

`service.lifecycle` 当前能力矩阵只声明 Windows：Windows 使用真实能力判断；无该能力的
Linux/macOS 对正向**共享逻辑单元**仅注入 capability 决策，不改变 `sys.platform`，
并另行以真实能力判断验证 exit/restart 返回 501、无 effect。注入不能证明 POSIX 支持
service lifecycle，也不能代替目标平台上的真实执行。Windows 上那两个真实 unsupported
断言明确 skip，原因是当前宿主具备该能力；原有平台 skip 原样保留。

逐原节点机器映射在 `CP_X1_LIFECYCLE_GATE_MAPPING.json`：原五条旧 registry、task 名或
pending 观察节点的红结果与原始证据保留，列出快层的具体后继断言和差异，不重分类为
原节点通过。三个原 controller desktop 门禁保持 `open_pending_cpx2_combination`，不改
Windows ProcessOwner 或恢复 legacy writer。

Windows 本机 Python 3.14.5，在精确代码 SHA 上运行：

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_platform_fast_gate.py tests/test_lifecycle_fast_contract.py tests/test_lifecycle_prelaunch_fence.py::test_prelaunch_original_error_idempotence_and_attempt_fence tests/test_lifecycle_prelaunch_fence.py::test_claim_or_launch_marker_forbids_prelaunch_rejection -q -ra --junitxml=evidence/cp-x1-lifecycle-fast-20261029/sourcebound.xml
```

结果 **23 pass、2 skip、0 fail、exit 0**；两个 skip 是 exit/restart 的真实 unsupported
宿主断言，Windows 具备能力故不适用。`sourcebound.xml/.log` 和 `validation.json` 留证。
仅运行受影响快层节点，未重复完整 fast gate；Linux/macOS 实机、远端 CI 和组合候选均
未验证，由 MA 负责后续整合及授权远端验证。没有 push、运行独立远端 CI、修改主分支、
平台产品、真实数据或现有服务。
