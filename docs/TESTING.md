# Pan 测试隔离约定

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

旧数据检查使用 `tests/test_premerge_legacy_data.py` 的合成临时记录：验证缺省
Terminal 字段、Session 旧 CLI id、缺失 Rewind sidecar、Job 混合时间戳；读路径
前后比较文件 bytes。需要写升级字段时，只允许显式用户操作，并验证 PID、raw
FILETIME、scope 和退出事实未丢失。此测试不是对实用数据的迁移或修改授权。
