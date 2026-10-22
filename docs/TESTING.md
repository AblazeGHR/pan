# Pan 测试隔离约定

Codex fixed-provider launch tests additionally use an owned `CODEX_HOME`, local
fake provider and app-server fixture; `PAN_CODEX_FIXED_PROVIDER_LAUNCH` defaults
off and must never be applied to a real profile for these tests. The fixture
asserts process `-c` provider overrides and thread receipts while retaining its
own native-history sentinel. Offline CLI schema generation is not a paid turn or
an account/config identity attestation. Test source injection cannot be replaced
with a public verified flag. See `tests/test_provider_launch.py` and its owned HTTP
launcher for process/ACL/Job cleanup assertions.

Installed native compatibility is a separate explicit opt-in, never an account
test. Set `TEST_REAL_CODEX_JS` to the installed codex.js and run
`python scripts/verify_provider_launch_real.py` after freezing source. It runs
the isolated runner, actual installed app-server and real Pan wrapper against
an owned loopback Responses server, with a fresh file-auth-only CODEX_HOME and
scrubbed environment. It verifies start/resume/materialized native branch,
concurrent global-config change, durable bindings and retained-handle cleanup.
No credentials or paid models are required. Evidence is under
`artifacts/provider-launch-real`. Without explicit opt-in this one external-CLI
integration test is skipped; its strict native-shape/buffering unit tests still
run. The tests never replace the product CODEX_HOME contract or production
provider exporter with a client-supplied verification flag.

## 默认入口

从 Job、Agent Worker 或已配置的 Pan 终端运行测试时，使用统一入口：

```powershell
python scripts/run_tests_isolated.py -- uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest tests/ -o addopts= -q
```

示例解释器路径仅为开发机配置；其它机器换成自己的解释器。脚本由其所在仓库
确定测试 cwd，清除子进程继承的全部 `PAN_*`，替换 `PYTHONPATH`，并分配测试 HTTP
端口。它不修改父进程环境，因此外层 Job Runner 仍能写自己的注册表并通知原
Session。端口探测后仍有占用竞态；测试监听失败必须显式报错，不接管已有端口。

**测试 cwd 不等于数据隔离。**`PAN_BACKGROUND_JOBS_DIR` 等环境变量优先于代码
默认值；仅 monkeypatch `DEFAULT_ROOT` 会被继承的环境变量覆盖。不要直接把 Job
的 `dict(os.environ)` 传给 pytest，也不要把实用应用数据目录作为测试数据根。

## pytest 的第二道防线

`tests/conftest.py` 在应用导入前清除继承的 Pan 设置（仅保留显式 `PAN_TEST_*`
测试命名空间）。公共 autouse 夹具将 Session、Job、scheduler、Terminal 注册表
和 config 文件的默认位置都设为每个用例的 `tmp_path`。因此直接跑 pytest 也有
保护；需要某个应用设置的用例，应在局部夹具中使用 `monkeypatch.setenv`，目录
必须来自该用例拥有的临时根。更窄的夹具可以覆盖默认位置，不能指向真实 data。

## 后台测试子进程窗口

普通后台 Python/Node/PowerShell 辅助进程使用无 Pan runtime 依赖的
`packages.jobs.window.command_window_options()`，Windows 默认隐藏；显式传
`False` 时创建可见控制台，非 Windows 返回空参数。隔离入口显式转发标准
输入/输出/错误句柄，隐藏不能丢失测试日志或退出码。
该 helper 只约束本次创建，不强制任意第三方后代。测试中每个我们拥有的
后台启动边都应设置策略；不能机械替换 ConPTY、Job ownership、脱离宿主或
takeover 的显式交互启动。`DETACHED_PROCESS` 与 `CREATE_NO_WINDOW` 不可混用
来宣称隐藏（Windows 忽略后者）。

`tests/test_job_window_policy.py` 的自有 Python 父→子→孙探针逐边应用策略，
使用真实 `GetConsoleWindow` / `IsWindowVisible` 验证隐藏与可见反向控制。
探针只读自己的控制台 HWND，不扫描或隐藏用户窗口；超时清理核验自有 retained
handle 的 PID 和 raw FILETIME。静态 flags 与这些内核实测是不同证据层。

测试子进程、真实服务 harness 和辅助 CLI 必须显式传递自己的临时根，并在
启动前检查最终解析值。不要通过更换 `config.json` 这个载体来代替隔离；读到
同一份实用配置同样危险。读取配置、写入数据和运行代码的位置必须分别核对。

## 新功能测试检查表

- 列出新功能读取的环境变量、配置入口和持久化路径；应用级变量使用 `PAN_*`
  命名空间，新默认数据存储加入公共夹具。
- 导入阶段不启动服务、迁移实用数据或执行写入；确需初始化的动作放在显式入口。
- 新建一个“带实用配置的父进程”负控，证明测试仍写临时根。不能只证明在无配置
  的开发终端中通过，也不能只检查 cwd。
- 对旁边的模拟实用目录放置 sentinel，前后检查文件清单和 bytes 不变；不要用
  真正实用目录做破坏性负控。
- 环境变量值可能含秘密，诊断只记录变量名和测试根，不输出完整环境。
- 长测试交给 Job 后记录 ID、冻结源码和结果文件位置；进入 idle 等通知，完成后
  检查退出码、XML 和资源清理，Job `delivered` 不是测试验收通过。

`tests/test_test_isolation.py` 用真实 pytest 子进程和带污染配置的父环境验证
这些约定；它使用模拟实用目录，不碰用户应用数据。

## Terminal 安装与旧数据回归

Windows Terminal 的 Python 依赖随 `minimal-requirements.txt` 安装；传统
`requirements.txt` 经 dev 层包含该文件。前端 `pnpm install --frozen-lockfile`
与 `pnpm build` **不会**安装 headless 引擎的独立依赖。部署或新 worktree 验收
前，另在 `packages/core/terminal/emulator_sidecar/` 执行 `npm ci`，使用该目录
的 exact-pin lock。不要提交 `node_modules`，也不要把缺依赖导致的 skip 当作通过。

部署验收必须使用 Pan 实际选择的解释器（config.python / PAN_PYTHON / .venv
的解析结果），不能用临时 `uv --with pyte` 环境代替。安装 minimal-requirements.txt
后，用该解释器执行 `scripts/check_terminal_deployment.py`。该脚本只检查 Python
依赖、CBC 命令与 Node exact-pin 侧车依赖，不调用 provider、不改 Session；ready
不是功能验收。setup.bat 也安装侧车并执行该检查。
真实验收须覆盖 REST/WS、中文输入、快照/gap、resize、control 权限、Ctrl-C、
detach/重启、归档/删除/外部退出、收尾与旧数据，使用临时根和非实用端口。
子进程服务 harness 使用 sys.executable，不以 _base_executable 绕过部署 venv。
真实 CBC 三种撤回范围需自有临时项目/检查点，模拟回归不是 provider 验收。

旧数据检查使用 `tests/test_premerge_legacy_data.py` 的合成临时记录：验证缺省
Terminal 字段、Session 旧 CLI id、缺失 Rewind sidecar、Job 混合时间戳；读路径
前后比较文件 bytes。需要写升级字段时，只允许显式用户操作，并验证 PID、raw
FILETIME、scope 和退出事实未丢失。此测试不是对实用数据的迁移或修改授权。
