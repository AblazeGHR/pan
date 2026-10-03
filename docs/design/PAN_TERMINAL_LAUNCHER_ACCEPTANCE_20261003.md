# Terminal launcher 阶段 MA 验收

## 结论

接受生产 launcher 的当前基础功能子集，已导入隔离集成分支
`audit/pr6-terminal-20261002`。不等同于完整终端产品或 P2 服务接线验收；
没有推进 main/practical、push、构建或重启既有服务。

## 对象与证据层次

- 首版 `5c44b36c`、r2 `7552319b` 已由独立审查接受，报告分别为
  `5681ef8e`、`549f00bc`。r2 独立复跑为直连及 uv 各 46 passed。
- r3 固定源 `da3fddac88dbd78086281f09fc3472afb283bc46` 导入为
  `90b2a31af940e2966d69413cbbf6ebe032441fa7`，无冲突。
- MA 逐行核对 r3 生产差量及新增测试：stderr 故障隔离、有限秒数投影；
  不改变进程所有权、IPC 或引擎收尾机制。
- 导入后 launcher、测试、接口、专属 audit 与固定源差量为空；
  r3 source_blobs 的五件锚定逐件一致。随后仅本文及接口可达性措辞发生文档修订。
- TA 的先失败证据为 9 failed / 12 passed / 46 deselected；属于固定旧版对照，
  不计作新版本失败，也不冒称 MA 重新执行了旧版对照。

## MA 亲跑

在隔离集成树执行（未设置证据输出环境变量，未刷新 TA 历史证据）：

```powershell
uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest tests/test_terminal_launcher.py -o addopts= -q -k "r3_ or r2_ or status or residual or startup or close or illegal or sentinel or tmp"
```

结果：**57 passed, 10 deselected in 10.15s，exit 0**。
这是 r3 与紧邻收尾定向集合，不是 67 项全量或真机组合重新验收。
未重复运行 13 组合、core、backend、IPC、emulator、全库或浏览器测试。

## 两点收口与限定

- N1：stderr 不可写不再使公告异常逃逸，不递归报告；内存事件只记类型名。
  退出公告路径只需 stderr 故障一个条件；状态写失败公告才需两个条件叠加。
  属同用户本地故障，不是远程输入漏洞。
- N2：非有限或不可转换的 cleanup_seconds 变为 null，有限值正常保留；
  标准 JSON 负例通过。真实引擎单调时钟来源未观测到此故障，属于受信注入面加固。
- 启动 owner 的同步 retry_cleanup 依赖上游自身有界契约，不承诺独立硬 SLA。
  迟到 close 结果仍可幂等重发，单飞不叠加；没有新增线程隔离机制。

## 下一阶段与未验收门

建议下一阶段为 P2 服务控制层与 registry 接线，单独冻结授权、心跳、恢复和关闭边界；
本记录不授权该接线，也不把它记作已完成。
Ctrl-C OS 语义、真实 durable detach、浏览器/provider、跨用户/主机、长稳、POSIX、
跨 sidecar 重启及真实部署持续运行仍未验收。
write budget 非硬 SLA，真实会话 partial 为常态，F5 无历史世代原子绑定。

工作树仅保留外部未跟踪 junction，不删除、不提交、不称全 clean；
main 中既有用户未跟踪文件保持原样。历史证据未修改。
