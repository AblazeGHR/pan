# 最终组合 harness Windows 默认编码返修

工作树 `D:/project/pan-worktrees/event-trigger-core-20261022`，分支
`feature/event-trigger-core-20261022`；开始 clean，基线
`5e8d37dea11b80d331dc38ea0abd0b6291425dfb`。

**精确受测源码 SHA：`1126367188836155479614952cfe5f904354d208`**。
后继证据提交只增加本目录资料，源码/测试保持该SHA。当前候选可由
`git log -1 --format=%H` 核验。

## 修复

MA在canonical Python默认Windows编码复验得到1 failed/1 passed：recovery浏览器
链路返回后，Path.write_text默认GBK无法编码输出字符，UnicodeEncodeError。
这是新增验收harness缺陷，不是吞吐波动。MA原始失败证据只读复制为
`ma-before-ma-combined.xml`、`ma-before-combined-http.json`，原目录未修改。

- `tests/test_eta_final_combined.py` 所有文本读写显式UTF-8：manifest/config、key、
  两次browser-input、两个browser日志、重启ledger读写、HTTP JSON和server日志。
- `tests/test_eta_ledger_growth.py` 的JSON输出显式UTF-8。
- Git check_output与两次Node subprocess.run均显式UTF-8解码；没有errors=ignore
  或replace来掩盖子进程日志。已有server日志读取的errors=replace保留原行为。
- Node浏览器脚本readFile和writeFile均显式utf8。两次成功执行都输出一个GBK不能
  编码的UTF-8标记，Python保留原业务断言并额外断言解码结果含该标记，以实际
  覆盖成功日志写盘场景。两份日志均成功UTF-8读回，且cp936编码会失败。
- 没有设置PYTHONUTF8、PYTHONIOENCODING、-X utf8或修改全局环境/系统编码。
  没有扩大产品范围、跳过断言或变更产品实现。

AST检查清单及Unicode往返证据见 [encoding-audit.json](encoding-audit.json)。

## 冻结源码验证

解释器：`D:/project/Pan-main/.venv/Scripts/python.exe`。
[environment.json](environment.json) 实测：utf8Mode=0，preferredEncoding=cp936，
PYTHONUTF8=null，PYTHONIOENCODING=null。测试未向子进程添加任何编码覆盖变量。
外层使用二进制句柄保存stdout/stderr，避免外层重定向自行改变日志内容。

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_eta_final_combined.py tests/test_eta_ledger_growth.py -o addopts= -q --junitxml=artifacts/eta-encoding-fix/final.xml
D:/project/Pan-main/.venv/Scripts/python.exe -m compileall -q tests/test_eta_final_combined.py tests/test_eta_ledger_growth.py
```

**2 passed，0 failures，0 errors，0 skipped，12.18秒，退出码0**。
compileall/编码AST检查/暂存diff check退出码0。
[final.xml](final.xml)、[final.log](final.log)、[command.json](command.json)、
[exit-codes.json](exit-codes.json) 可复核。仅1条既有pydantic_settings lifespan
forward-reference提示。没有重跑无关整套，也没有全仓全量。

## 隔离闭环与清理

原业务断言全部保留：实际Node→query冻结→Pause/队列阻止→生产Chromium，
真实stdio delay MCP、停用/旧generation拒绝、重启queued/uncertain及不重复投递。
本轮生产build复用本树已有产物，harness修复无需改产品或重建UI。

最终端口 **49534**；完整PID/createTime/command、Run/Node/MCP/queue与退休状态
见 [run/combined-http.json](run/combined-http.json)。终态
`ownedProcessesRemaining=[]`、`portReleased=true`。两次Chromium脚本等待
browser.close并正常退出；[run/browser.json](run/browser.json) 与
[run/recovery/browser.json](run/recovery/browser.json) 均ok=true且errors=[]。

本轮输出另存本目录run/，旧artifacts/eta-final证据已恢复为基线内容，避免覆盖
旧报告。日志和失败JUnit只做换行/行尾空格统一，Unicode正文保留。

整体真人验收仍待用户，旧设计矩阵的产品范围/延期边界未变。未移动main/practical、
改集成树、push/release、访问8767/8768/真实data、调用真实模型、写共享依赖或
派子任务。提交clean候选后停止，等待MA复审。
