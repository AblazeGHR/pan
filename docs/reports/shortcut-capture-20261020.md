# 快捷键录制反馈、Steer 与换行配置

环境：`D:/project/pan-worktrees/shortcut-capture-20261020`，分支 `fix/shortcut-capture-20261020`，基线 `fc0c8d6f836509ced4784229b12aacda8dfbc4b5`。先读取 canonical `.workflow/constraints.md`；未移动 main/practical，未 push/merge/release，未对 8767/8768 发测试请求或重启既有服务。并行 handoff prompt / CBC 倍率范围未修改。

## 根因和复现边界

- 基线真实 Chromium：`Ctrl+Shift+F10` 和 `Ctrl+4` 能录入。用户随后也确认全部可录入，因此没有认定 Ctrl+4 存在稳定的 capture/focus/global interception 缺陷。
- 用户进一步指出提示位置；源码证实冲突/保留键错误统一放在快捷键页顶端，靠下行编辑时看不到，造成“没有录入”的观感。修复后在真实浏览器把最后一个 Session 动作设为 Ctrl+K，错误出现在该行，显示冲突对象 Open command palette，并用 `elementFromPoint` 确认提示中心没有被滚动容器遮住。
- 基线实际复现了两个独立问题：无修饰 F8 被 `bindingFromEvent` 静默拒绝而一直停留在 Press keys；Tab 在判定键之前被 preventDefault 吞掉，焦点留在 Record 按钮。现在功能键可录制，无效普通输入有就地解释，Tab/Shift+Tab 取消录制并保留原生焦点移动，Escape 只取消录制。
- 原快捷键保存为 best-effort，失败被吞掉；现在显示保存中/已保存/保存失败，失败回滚，整张 shortcuts map 的写入串行化，避免旧请求覆盖新编辑。

基线证据：`packages/web/test-results/capture-baseline/repro.json`、`matrix-headless.json`、`record.png`。Win32 按键尝试没有形成有效证据，未用于判断 Ctrl+4；浏览器键盘证据来自 Chromium/Playwright 的真实键盘输入，不是页面 dispatchEvent 或 jsdom。

## 行为和兼容

- 已有 registry / `AppSettings.shortcuts` schema / Shortcuts UI / `ui.shortcuts` 持久链新增 `composer.send`、`composer.steer`、`composer.newline`。默认 Enter 发送、Shift+Enter 换行；Steer 之前没有快捷键，默认未绑定，避免新增隐式发送动作。旧 shortcuts map 缺少新键时使用这些默认值，显式 null 仍表示清除，动态 panel 的稳定 ID 不变。
- 提示在编辑行内，与 Record 的 aria-describedby 关联；错误出现时仅将提示滚入最近可见位置。录制全程隔离业务快捷键，退出/取消/卸载清理 capture listener。
- composer 只匹配完整组合，不再把任何“非 Shift 的 Enter”都视为发送。send 和 Steer 使用现有按钮的 click 路径；按钮可见性、disabled、锁定入队模式、附件和队列编辑门控，以及现有服务端 readonly/pause/admission 门控保持在原事务中。unknown 事务保留，不自动重发或退回普通发送。
- 换行使用浏览器原生 editing command；Chromium 实测富文本与 textarea 都按 caret/选区插入、保留 focus 和 Ctrl+Z。textarea 不支持 command 时有 setRangeText 回退，回退路径的撤销栈不作原生保证。
- composer/send/queue/global 动作统一判冲突；平台上的显式 Ctrl/Meta 与 Mod 视为同一个组合。保存时拒绝冲突，历史配置出现冲突时双侧均不执行；输入框相关冲突同时阻止默认编辑，避免双发或误发。
- 录制和执行保留 IME/isComposing/keyCode229、repeat、modifier-only 防护。使用物理 code 归一化字母、数字和标点，规避大小写/布局歧义。输入框提示和按钮 tooltip 随当前绑定变化，冲突提示为 inactive。

## 验证

所有命令均为本隔离树定向验证，没有运行全量测试。单个命令预计均小于三分钟；实际最长浏览器回归约 74 秒，没有启动需要轮询的持久长 Job。

| 层级 | 结果 |
| --- | --- |
| Python `test_settings_ui.py test_cbc_steer.py test_steer_history_consistency.py -q` | 46 passed，exit 0；临时配置，覆盖新增 ID 持久、pause/lease、admission unknown/idempotency 等既有 Steer 协议 |
| Vitest shortcuts / ShortcutsSettings / InputRow / RichTextComposer / appSettingsStore | 8 files，171 passed，0 skipped，exit 0 |
| 本次修改的 TS/TSX/MJS 定向 ESLint | exit 0 |
| 全目录 ESLint 额外检查 | exit 1，140 errors / 19 warnings；errors 全部在未修改的既有 17 个 E2E 文件，未修改规则或扩大修复范围 |
| TypeScript `tsc -b` | exit 0 |
| Vite build + precompressed assets | exit 0；72 个压缩资源校验通过；原有 chunk size / router dynamic import 提示仍在 |
| 新真实 Chromium `shortcut-capture.e2e.mjs` | 12 组通过，exit 0，独立临时 persistent profile |
| 原有真实 Chromium `shortcuts.e2e.mjs` | 11 passed / 0 failed，exit 0 |
| PowerShell runner | ParseFile 语法检查通过，新增 `-Scenario shortcut-capture.e2e.mjs` |

新浏览器验证包括：Ctrl+4/5、Ctrl+Shift+4/F10、Alt+4、F8；最后一行冲突提示可见；保留键/无效键提示；Tab/Escape；保存失败回滚、清除/重置；录制不触发 global 且结束立即恢复；富文本/textarea 选区换行和 native undo；真实配置 PUT/GET/刷新；已保存 Ctrl/Mod 冲突零请求；Steer accepted/unknown 请求分层；真实 Enter 和自定义 Alt+Enter 持久入队；恢复全部默认值后 Shift+Enter 换行。

原回归包括动态插件、IME CDP composition 与合成 composing guard、真实 Monaco/xterm DOM 焦点、无 Session、锁定入队失败恢复、附件/URL 顺序、长按、切换 Session、queue lock/pause、Stop report/通知持久化。xterm engine 在该 fixture 中未启动，不能作为 PTY 按键投递证明。

证据均在本树：

- `packages/web/test-results/capture-browser/capture-results.json`：新 12 组结果、profile、请求体、持久队列回执；`inline-conflict.png` 为就地提示截图。
- `packages/web/test-results/capture-legacy/browser-results.json`：旧 11 组结果和截图。
- `packages/web/test-results/capture-vitest.log`、`capture-build.log`、`capture-lint.json`。
- `packages/web/test-results/capture-baseline/server-identity.json`、`server.out.log`、`server.err.log`：端口 19341，自有服务 PID 32528，明确 checkout/runtime/sessionDir。
- `packages/web/test-results/capture-baseline/cleanup-owned-processes.json` / `cleanup.json`：逐个核对祖先关系、创建时间和命令后清理自有 17 个进程（含早期 wrapper），19341 listener 已释放。

依赖：prepared 的 canonical junction 缺少 xterm。本树改为各依赖目录只读 junction overlay，其中 `@xterm` 只读复用 practical 已有版本；没有安装、复制或修改共享依赖，没有修改依赖清单/lockfile。

## 请求拦截、真实后端与未验证项

- Steer 浏览器成功/unknown 案例采用隔离 WS running fixture + 请求拦截，证明快捷键复用原 Session Steer 路径和 messageId、不会双发、unknown 后不会重投；不证明 provider 已消费。
- Send 浏览器使用真实后端，queuePaused=true 的回执、GET queue 和稳定 item ID 证明持久入队。最终 fixture 统一使用 server.py 中的确定性 fake CBC CLI；没有把入队当成 provider 消费。早期 Codex fixture 曾启动隔离 wrapper，队列保持 paused、Session history 为 0；最终场景改为 fake CLI 并清理自有进程。
- 真实 provider 消费、OS 物理键盘、真实 Windows IME 候选交互、其他浏览器/Mac 键盘布局未验证；IME/code/layout 边界有单元和 Chromium CDP 验证。
- 新 runner 的完整启动/关闭流程未另行重跑；本次使用同一隔离 server.py 手动启动、核验 PID/command/listener，运行相同脚本，最后仅清理自己的实例。旧端口/用户 Session/既有服务未用于测试。

复跑新场景：在本树执行 `powershell -NoProfile -ExecutionPolicy Bypass -File packages/web/e2e/run-shortcuts.ps1 -Scenario shortcut-capture.e2e.mjs`。该 runner 自选非 8767/8768 端口，创建临时数据与 profile，并核验自有进程身份后清理。
