# Pan 列表/历史卡顿：代码分析与诊断补强

基线：`6eff88c6fc665f989e475700abf92386d0fbbb93`。用户授权验证隐患后直接补丁并同步 main/practical；未授权 push，本次不 push，不操作既有服务。

## 事故结论

停止前日志显示逐步拉长的响应间隔、provider 仍推进但 Pan 历史落后。退出为正常 stop 通道。进程已经退出，缺少当时线程栈、任务栈及浏览器 pending 请求。

对归档的 111 个 Session 做离线完整列表重放：元数据约 80ms，所有尾页约 349ms，编码约 73ms；部分单行整理约 44–63ms，没有复现持续卡死。这是隔离的磁盘现场重放，不能还原当时的并发和内存状态，也不是服务响应 p95。因此本次不宣称已确定事故根因。

## 发现与修复

1. **完整列表仍在主循环投影历史文本与 lastResult 本地链接。** 这些路径会解析文件位置、stat 并访问附件注册表。现在先在主循环冻结私有元数据，再在线程中投影历史与结果；不把可变 Session 对象交给线程。保留字段、身份和分页语义。
2. **两个未锚定 Markdown 链接正则存在平方耗时。** 合成连续未闭合 `[`：旧链接正则 8000 字符约 885ms，翻倍输入增长接近平方；新扫描器 100000 字符约 3.8ms。替换为线性扫描，保留嵌套起始括号、转义、附件过滤及位置；小输入穷举和随机样本对照旧表达式。真实事故尾部数据的正则离线扫描约 160ms，不能把此隐患当成事故触发证据。
3. **HTTP 原日志等待发生在响应头发送之前，且共享默认线程池。** 现在记录 start/headers/end，保留请求 ID、状态、耗时、字节数和完成/错误/取消状态。控制台写入在独立 daemon 中，最多一项在途、50ms 等待预算；忙碌时记录丢行，独立结构化记录照常保留。
4. **原保存诊断接口在主循环取保存锁。** 移到独立 diagnostic-read 线程，避免采集端点本身被保存锁阻塞主循环。
5. **隐式文本编码产生 QQ GBK 错误。** 隐藏启动的 Python 输出显式使用 UTF-8/backslashreplace，环境由桥接子进程继承。

保留 store-read 单线程契约，不用增加读取线程掩盖积压。新增运行时记录用于区分 queued、running、等待和执行耗时，而不是据猜测改变存储并发语义。

## 诊断交付

- `packages/core/runtime_diagnostics.py`：有界进程内记录、独立写入与慢操作监控、线程位置、恢复后的事件循环延迟及任务位置；启动时在监控线程保存磁盘源码 SHA-256、根目录、Python 路径。磁盘指纹用于对照，并不声称检测了全部已加载模块。
- `GET /api/diagnostics/runtime`：不经过 Session 读取队列；保存诊断仍在原 persistence 路径。
- `scripts/diagnose_pan.bat` / `.py`：独立进程，只读采集两处控制台日志、运行时日志、HTTP 完整性、端口身份、资源及可选外部栈；记录截取偏移及 SHA-256。HTTP 探测超时独立于服务；PID、创建时间和入口路径验证后才尝试 py-spy。
- `scripts/stop.bat --diagnose`：先采集再走原正常退出。普通 stop 不增加采集。
- `diagnostics-requirements.txt` 固定可选 py-spy 0.4.2；已安装到两个检出的 venv，并在受控 Python 3.14.5 进程验证抓栈退出码 0。Windows venv 的外层 launcher PID 不能被当作 Python 解释器 PID；服务采集以监听 PID 为准。
- [使用与判读说明](../runtime-hang-diagnostics.md)。

进程内监控无法保证在长期持有 GIL 时运行，故保留独立外部采样路径。新运行时记录不采集正文、查询参数、栈局部变量或异常正文；原始旧日志内容仍需按本地证据保管。

## 验证

证据在 [checks.json](../../audit/hang-diagnostics-20261021/checks.json)，JUnit 与日志在同目录。

- 相关 14 个测试文件：187 passed，0 failed，0 skipped，46.83s；FastAPI/Starlette 两条现有弃用警告。
- 最后诊断调整后：12 passed，0 failed，0 skipped。
- 真 HTTP：读取线程故意占住时，health 和 runtime 仍响应，诊断可看到队列及阻塞线程；释放后列表正常完成。使用临时数据及系统分配端口，不启动真实 provider。
- 既有进程级冷加载 E2E：真实 HTTP、Dashboard WebSocket 和 fake-provider Worker 并发通过。未声称完成真实浏览器交互验收。
- 外部工具：py-spy 0.4.2 / Python 3.14.5 受控进程抓栈通过；服务停止时只读采集 CLI 能保存现场，未启动或停止服务。
- compileall 与 git diff --check 通过。未运行全仓库测试；未改前端资源，没有重新构建前端。

原始事故归档保持原样。两个分支的最终提交一致性在交付时再次核验；原有未跟踪文件保留。
