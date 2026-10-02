# 公共 PTY / runtime 契约探针

探索阶段产物（T-TERMINAL-PTY-20261003，公共 PTY 契约与 PR #6 接入）。
**不修改 `packages/`，不改正式依赖锁，不注册到 Pan 服务。**

## 文件

| 文件 | 作用 |
|---|---|
| `pty_contract.py` | 最小契约原型：`PtyBackend`、`OutputLog`（有界 + 绝对字节序号 + gap）、`PtyRuntime`（drain 到 EOF、状态机、清理契约）、`TerminalRegistry`、`AttachmentRegistry`（control lease）、`PyteScreenObserver`、`AutomationDriver` 边界、detach 能力扩展点 |
| `probe_lib.py` | 探针工具：结果记录 / JSON 汇总 / 进程身份核验 / 硬超时看门狗 |
| `probe_mock.py` | **MOCK** 契约逻辑测试（`ScriptedBackend`，不代表真实 CLI 行为） |
| `probe_real.py` | **REAL** 真实 PTY 测试（pywinpty/ConPTY + `cmd.exe` / `less.exe` / `ping.exe`） |
| `evidence/` | 上述两者的 JSON 证据输出 |

## 运行（依赖装在临时环境）

```bash
# MOCK（无 PTY，仅需 pyte/psutil）
uv run --no-project --python E:/software/miniforge/python.exe \
    --with pyte==0.8.2 --with psutil \
    -- python audit/terminal/contract/probe_mock.py \
    --json-out audit/terminal/contract/evidence/probe_mock.json

# REAL（真实 PTY）
uv run --no-project --python E:/software/miniforge/python.exe \
    --with pywinpty==3.0.5 --with pyte==0.8.2 --with psutil \
    -- python audit/terminal/contract/probe_real.py \
    --json-out audit/terminal/contract/evidence/probe_real.json
```

退出码：`0` 全部通过；`1` 有失败；`9` 触发硬超时保护（`--hard-timeout`，默认 300s）。

## 边界

- 只创建和操作探针自己的进程；不触碰既有服务、Session、Worker、CLI thread。
- 不打开任何监听端口（`ping` 只走 ICMP 回环）。
- 只在 `%TEMP%\pan_pty_contract_probe_*` 写测试文件，结束时删除。
- 注入故障（故意失败的 `terminate`）在结果里显式标注 `injected`。
- MOCK 结果不得当作真实 CLI/PTY 证据；REAL 结果见 `probe_real.py` 的 JSON。
