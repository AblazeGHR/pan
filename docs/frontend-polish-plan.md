# Pan 前端细化计划（feat/frontend-polish）

> 1 级 meta-agent：agent-frontend-polish（dsV4flash）
> 侦察日期：2026-08-26。仅侦察+规划，具体实现派发 hy3 worker。

## 一、前端现状摘要

### React SPA（/react/*，开发主目标）

- 技术栈：React 19 + Vite 6 + Zustand 5 + Tailwind 4 + react-router 7
- 入口链：`src/main.tsx` → `src/router.tsx`（`/` ChatView、`/editor` EditorView）→ `src/App.tsx`（Layout：Sidebar / DetailPanel / CommandPalette / ToastContainer）
- Store（8）：sessionStore（会话 CRUD + 消息分页 + 多选 + 草稿 + unread + 并发守卫）、workerStore、uiStore、adapterStore、queueStore、detailStore、editorStore、appSettingsStore
- Services：`api.ts`（HTTP 封装）、`ws.ts`（广播订阅 + 重连）
- 组件（30+）：chat/、layout/、session/、detail/、editor/、ui/、worker/
- 已有能力：树形/分组会话列表 + 虚拟滚动 + 多选批量删除；Markdown 渲染（GFM + KaTeX + highlight.js，双主题）；thinking/tool 分组；发送队列面板；命令面板（Ctrl+B/1/2）；Monaco 编辑器（文件树 + 多标签）；QQ postbox；Manage/Import/NewSession 弹窗；暗/亮双主题（data-theme）+ TUI 扁平模式；移动端适配（safe-area、<640px bottom-sheet、hamburger、visualViewport）
- 测试：17 个测试文件（ChatMessages/InputRow/MarkdownRenderer/SessionList×3/useWebSocket/各 store 等），vitest

### Legacy（/，稳定备份）

- `packages/web/ts/app.ts` 3261 行单文件（编译产物 static/js/app.js 为 gitignored）
- 决策（docs/阶段计划与进度.md D4）：**冻结，只修 bug**，不投入细化

### 后端 API 能力（前端可挖掘）

- 56 HTTP 端点：Session / Worker（含 interrupt/takeover/restart）/ 编排（handoff/assign/report-subscribe/claim/unclaim）/ QQ（subscribe/unsubscribe/contacts）/ Character/Memory（memory/index、search、stats、inject）/ FS / Adapter 导入（cbc、kimi）
- WS：`/ws` 广播 worker.* 12 种 + session.* 5 种 + error；`/ws/agent` 订阅过滤 + 重连补发
- **空白点：memory/search、cbc/sessions 搜索等 API 前端零使用**

## 二、细化方向与优先级

| 优先级 | 方向 | 现状判断 | 涉及文件 |
|---|---|---|---|
| P0 | 消息流式渲染/交互增强 | 流式布局抖动、长代码块/表格无折叠复制、无重新生成 | ChatMessages.tsx、MessageBubble.tsx、MarkdownRenderer.tsx |
| P0 | 会话列表增强 | 有树形/分组/多选，缺搜索过滤、置顶/归档 | SessionList.tsx、SessionItem.tsx、sessionStore.ts |
| P1 | 记忆/搜索面板（最大空白） | 后端 memory/* API 前端零使用 | 新建 components/memory/*、services/api.ts |
| P1 | 设置面板深化 | adapter/模型/effort 配置 UI 较粗 | AppSettingsModal.tsx、SettingsPopover.tsx、adapterStore.ts |
| P1 | QQ postbox 体验 | 有订阅弹窗，缺联系人搜索/状态展示 | PostboxModal.tsx、api.ts |
| P2 | 编辑器体验 | 缺脏状态提示/保存确认/查找替换 | EditorPane.tsx、CodeEditor.tsx、editorStore.ts |
| P2 | Worker/Detail 管理 | 状态可视与 interrupt/takeover 弱 | DetailPanel.tsx、WorkerDot.tsx |
| P2 | 主题/性能 | 有双主题缺跟随系统；bundle 偏大 | uiStore.ts、index.css、vite.config.ts |

## 三、任务分解

每项派发 1 个 `general-purpose` worker（**model=hy3**），一次一个方向：

1. **P0-1 消息交互**：ChatMessages 流式稳定（虚拟滚动/滚动锚定）、MarkdownRenderer 代码块折叠+复制、消息操作（复制/重新生成）
2. **P0-2 会话列表**：搜索/过滤条、置顶/归档字段（前端本地 + sessionStore 扩展）、分组间移动
3. **P1-1 记忆/搜索面板**：新建 memory 面板组件 + api.ts 对接 memory/search、stats；会话全文搜索入口
4. **P1-2 设置/Postbox**：AppSettingsModal 补 adapter 配置与模型列表；PostboxModal 联系人搜索 + 订阅状态
5. **P2 批量打磨**：编辑器脏状态/保存确认；DetailPanel worker 操作；主题跟随系统 + bundle 分析

## 四、验收与约束

- 每项 worker 交付：改动文件清单 + `cd packages/web && pnpm build` 通过 + 相关 vitest 通过
- Legacy 改动仅限 bug 修复（npx tsc --noEmit 校验）
- 只改本 worktree（feat/frontend-polish）；不动运行中服务；push 等用户指示
- worker 产出由本 meta-agent 复核后自行 commit（本分支）
