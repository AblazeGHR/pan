# Codex Terminal TUI restart 修复

基线 `6f294441`；独立树 `codex-native-restart-20261011`。

## 根因与改动

restart/respawn 复用 Worker 对象，clear_native_runtime_state 原先只清 thread
展示缓存，遗漏 native owner mode/generation、ready、output 及在途回执。
新 kernel 的 ControlOwner 从 pan/0 开始；旧 Worker 曾 native/多次切换时，
旧代号发送给新 kernel 会被拒，最终持续 native-switch-unconfirmed。

- 进程边界重置原生运行态、清旧输出、结束旧回执；不改 Session 的 thread id、
  history、queue 或 native-control 配置。
- 切换/输入调用绑定 Worker runtime generation，旧调用不得改变新控制权或写入。
- status/output 增加 workerGeneration；新浏览器输入带该值，旧代输入拒绝。
  兼容旧调用者不带该可选字段的形状；原控制代号检查不变。
- 前端同 Worker id 但 runtime generation 改变时重建 xterm，游标归零。
  普通控制权切换不重建屏幕。重启后控制权以新 kernel 确认为准，不自动伪造 native。
- Terminal TUI 展示中的按钮为蓝色（桌面/移动入口共用），可用未展示为绿色，
  error 仍有危险色提示。展示状态不是控制权成功证明。

## 验证

- Python 定向：131 passed，2 个既有 Starlette/anyio warning。
  包括真实 restart 入口调用（进程/任务原语为受控替身）、旧请求跨重启、
  API 错 runtime generation 零调用、配置转换、队列 restart 回归。
  JUnit: `pytest.xml`。
- 真实 Codex wrapper/Windows ConPTY + 本地 SyntheticResponses：2 passed。
  验证 kernel mode 往返不退步，无外部账号/模型调用；`kernel.xml`。
  此项不是完整浏览器 restart 操作链实测。
- 前端五文件：47 passed（store、xterm 生命周期、poller、TopBar、mobile entry）。
  新组件测试使用 xterm 替身，不冒充真实浏览器渲染验收。
- pnpm build：TypeScript/Vite/64 个压缩资产校验通过；既有大 chunk warning。
- git diff --check 通过；package/lock 未改。

验证环境首次共享 node_modules 缺既有 xterm 依赖，构建失败；移除本树由工具创建
的 junction 后本树 frozen-lockfile 安装并构建通过，没有修/写 canonical 依赖。
新组件测试首次未声明 jsdom 导致 document undefined，补测试环境后通过。

未实现允许所有原生操作透传（按用户先后顺序留到下一阶段）；未改默认配置，
未重启/合并 main/practical、未 push，未运行全库/完整 live 浏览器操作。
