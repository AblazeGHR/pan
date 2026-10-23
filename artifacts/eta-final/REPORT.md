# ETA v0.1 最终覆盖审计与组合可靠性候选

原工作树 `D:/project/pan-worktrees/event-trigger-core-20261022`，分支
`feature/event-trigger-core-20261022`。开始 clean；已将指定整合基线
`bbb38bffd4a131e09eddbce58545b53cf49b8f52` 快进合入本树。
没有改 integration 树、main/practical 或其他 TA 工作树。

**精确受测 SHA：`044322d1f08fdb4cf29cb540025c7aa6884f36a2`**。
此后提交只增加本目录报告/矩阵/日志/证据，不改变源码或测试。最终候选 SHA
由本分支 `git log -1 --format=%H` 核验。

## 审计与新增工作

逐项设计/实施补充/core-contract 矩阵见 [DESIGN-MATRIX.md](DESIGN-MATRIX.md)，
包含实现入口、可执行检查、历史与本轮证据，以及明确延期/待用户边界。
没有把主设计中“所有 API/命令”的长期方向、parallel、可靠事件投递、完整账本
保留/分页索引等未实现范围默认为完成。query、生产UI和stdio插件已整合，
core-contract 旧阶段状态已校正。

本轮**未复现新的产品 P1/P2**，没有猜测性产品重构或降低原测试要求。
新增两个验收用例文件与生产 Chromium 脚本，覆盖跨线组合和有限账本增长测量。
新脚本早期的GBK文本解码及旧heading locator不适配新版按钮列表造成两次失败，
属于验收脚本修正，不能称作产品缺陷修复。

## 冻结提交验证

完整可执行命令见 [final-command.txt](final-command.txt)。只选择25项跨线相关
用例，没有机械重跑已有79/192/185整套，也没有全仓全量。

- **25 passed，0 failures，0 errors，0 skipped；退出码0**，13.95秒。
- [final.xml](final.xml)、[final.log](final.log)、[exit-codes.json](exit-codes.json)。
- compileall：退出码0；原始和暂存差异检查：退出码0。
- `packages/web` 的 `pnpm build`：退出码0，80个压缩生产asset验证通过，
  [build.log](build.log)。依赖为本树junction只读引用已存在的
  `D:/project/pan-worktrees/event-trigger-ui-20261022/packages/web/node_modules`；
  未安装/改共享依赖，dist/tsbuild缓存在本树，未直接编辑dist。
- 5条warnings为当前解释器缺pytest-timeout引起的旧配置/标记提示及 MCP 依赖的
  pydantic_settings lifespan forward-reference 提示，详见final.log；没有新增skip/xfail。
  新组合的HTTP/Chromium/进程等待均有显式超时。

本轮受测内容：真实Node+query+原Pause+生产UI+真实stdio delay MCP；并发owner/
wake/close交错；serial与动态Action；插件reconcile close、EOF无热循环；append/
partial persist/checkpoint写失败；取消uncertain；真实Session冻结后变更不重选；
query恢复不重新解析；状态缓存/布尔、token边界、bus满载、失败Action与零结果。

失败证据：combined-initial.log/xml（退出码1，GBK解码新脚本输出），
combined-second.log/xml（退出码1，新版Actions条目为button而非heading），
修正后combined-third、targeted、pre-source均退出码0。过程计数不叠加最终25项。
文本日志及失败JUnit仅统一换行、删除行尾空格，保留失败正文。

## 一条完整隔离闭环及恢复/共存

[combined-http.json](combined-http.json) 记录临时profile、PID/createTime/command、
Event/Run/query选择/queue/Node收据/fakeCLI消费/MCP返回和清理。
最终端口 **50432**；初次服务PID **35008**，重启服务PID **42380**。

1. 最小自有Manifest启动实际Node producer；Session binding和generation绑定发布
   身份。3个自有Session，query Action选中名为combined-target者。
2. 实际Node发布 `producer.ping`，Run按query固定目标；原Pause业务持久化
   queue_paused。解锁目标积压仍不交付，控制Session实际fakeCLI收到消息。
3. 生产构建由该HTTP实例提供；Chromium **153.0.8010.12** 直接打开
   `/react/actions`，打开query编辑器确认条件，然后 `/react/automation` 查看
   succeeded Run、producer.ping、冻结目标ID、选择数及逐目标ok。
4. 同一HTTP实例通过**真实MCP stdio ClientSession**调用agent_restart_delayed，
   既有HTTP/Worker业务完成restart；独立受限caller不能操作无托管关系的target。
   没有使用query为MCP注入权限或替代Session绑定。
5. 关闭最后producer binding，definition unavailable且Trigger仍enabled；重新
   enable产生新generation。实际Node带旧generation发布被拒绝，不生成新Run。
6. 停止自有进程后，在自有临时ledger注入 queued/running 两个**离线崩溃状态
   fixture**；重启实际服务/插件，queued在ready后完成，running变uncertain，原
   冻结目标保留。原Event重新发布等待新的Node收据，Run总数仍3，不重复生成。
7. 再次生产Chromium查看恢复后的 succeeded queued和uncertain警告，无retry/
   resend按钮。[browser.json](browser.json)、[recovery/browser.json](recovery/browser.json)
   均 `ok=true, errors=[]`。
8. Playwright在finally等待browser.close，两次browser脚本正常退出；自有服务器/
   Node/fakeCLI/宿主后代按PID+createTime核验退休，`ownedProcessesRemaining=[]`，
   `portReleased=true`。依赖Chromium已存在，没有下载或启动真实模型。

截图：[Action query](production-action.png)、[运行结果](production-run.png)、
[恢复后Run/uncertain](recovery/production-run.png)。已实际查看截图。
配置/enable在本轮由真实API完成，UI验证生产编辑器和结果展示；完整UI创建/启用
交互沿用MA接受的f328a8d2，不能将本轮简化成又一次全部UI交互验收。
恢复fixture不等于实际OS强杀/断电实验。原始provider消费为fakeCLI，不是收费模型。

## 账本增长与可靠性边界

[ledger-growth.json](ledger-growth.json) 为自有临时根1/100/1000条合成终态记录。
最终样本分别1042/56554/564154 bytes；单条原子更新约3.57/5.19/31.32ms，
浅读约0.31/0.57/3.16ms。不是生产容量指标或长稳证明，未设置无依据性能断言。

当前每次读/事务重写与完整历史有关，ledger锁仍会覆盖该次文件事务；没有持锁
等待provider/Action或同步磁盘阻塞loop。慢盘屏障证明loop仍可推进，但不保证无限
历史下无延迟。账本无限保留，HTTP snapshot也返回全量；大历史前必须另行确定
保留/分页/索引以及去重保留策略，不能擅自prune破坏Event+Trigger永久去重。

事件入Bus到持久化前的丢失窗口、有限diagnostics进程内、无exactly-once、失败
不重试、uncertain需人工对账均保持原契约。整体真人验收仍**待用户**，自动测试、
实际HTTP、生产Chromium和截图均不代替用户整体接受。

未访问8767/8768、真实data或用户Session；没有真实模型、共享依赖写入、push/
release、既有服务操作或子派发。最终clean候选交付后停止，等待MA复审。
