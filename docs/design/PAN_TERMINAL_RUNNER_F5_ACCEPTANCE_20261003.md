# Runner F5 观测面接受与隔离集成

2026-10-03，MA。仅接受内部观测面，不批准 P2 或生产 launcher。

## 来源与验收

- 实现链：e822e0b7 → 6ca24bb6 → 29d535bc。
- 独立审查：cc3011af（43 双环境）、68e0656e（47 双环境）、7141d4dd（最终 F5 定向 12 双环境）。最终报告全文、固定提交、clean 和生产只读边界已核对；无返工项。
- 最终独立门控包括严格 bool、reset/baseline 交叉、2^53 精确比较、超长数字转换异常、负偏移、空白/+、unknown 与合法 stale 的区别；失败阶段 2 failed / 1 passed / 47 deselected 的源码 blob 与 6ca 精确一致。旧证据未覆盖。
- 隔离集成树起点 8769f577；累计补丁 3-way check 干净，依次 cherry-pick -x 无冲突：e822e0b7 → f268959a，6ca24bb6 → 48211b0a，29d535bc → 4788b4a9。
- 集成后 runner.py、test_terminal_runner.py、Runner 接口文档相对源 29d535bc 的 diff 为空；其他生产模块未变。

## MA 集成树定向验证

在 D:/project/pan-worktrees/pr6-terminal-20261002 执行：

```text
uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest tests/test_terminal_runner.py -k f5 -o addopts= -q
............                                                             [100%]
12 passed, 38 deselected in 0.28s
```

exit 0；git diff --check 干净。未重复 50/47 全量、11 组合、48 emulator、core/全库或浏览器。测试为进程内观测面验证，不替代真实组合验证。

## 接受的限定

两个字段只接受真正 bool，来源必须自含 reset 未确认语义，且无 IO/短临界区。确认来源在快照之后读取，无历史世代原子绑定，只是保守近似；timeout_ms 不涵盖慢确认源额外延迟。

baseline 只接受非负 int（非 bool）或 strip 后 ASCII 数字、可带单个 + 的字符串；负数、非法与转换 ValueError 都为 unknown。未知 baseline 不造值、不额外约束；不能把这种选择解释为独立证明有效。字段缩减/上下文缺失时不得单凭机器字段宣称完整恢复；VT 原因仅在 note，不因 Python diagnostics.reasons 为空升级。

下一步为修正后的真实 ConPTY + IPC + headless 组合验收（含 F1/F2/F5 和宿主有界收尾）。不重写旧组合证据、不批准 P2/生产 launcher。Ctrl-C、真实 durable detach、浏览器、跨用户/主机、长稳与 POSIX 门维持。未合 main/practical/PR、push、build 或重启服务。
