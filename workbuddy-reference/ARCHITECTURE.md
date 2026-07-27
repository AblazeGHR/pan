# WorkBuddy Web Frontend - Architecture Analysis

## Overview

WorkBuddy 的 Web 前端是一个 **Vite 构建的 React 18 SPA**，运行在 Electron BrowserWindow 中。
它实现了 AI 编程助手的全功能 IDE 界面，包括代码编辑器、文件管理、终端、画布布局等。

---

## Tech Stack

| 类别 | 技术 | 版本/细节 |
|------|------|-----------|
| 框架 | React | 18.3.1 (production build) |
| 构建 | Vite | ES module output, code splitting |
| 样式 | Tailwind CSS | v4 (utility classes + CSS variables) |
| 状态管理 | Zustand | useSyncExternalStore pattern |
| 代码编辑器 | Monaco Editor | 0.55.1 (CDN: jsdelivr) |
| 加载器 | @monaco-editor/loader | Dynamic CDN import |
| Markdown | react-markdown | rehype/remark plugins |
| 终端 | xterm.js + Ghostty WASM | WebAssembly terminal |
| 图标 | Devopicons, File Icons, Font Awesome, Octicons | woff2 |
| 通信协议 | ACP (Agent Communication Protocol) | SSE streaming |
| PWA | Workbox | Service Worker cache |

---

## 核心架构

### 1. Entry Point (`index.html`)

```
Vite 生产的 HTML:
  - <html class="dark"> (强制暗色主题)
  - PWA: manifest.webmanifest + apple-mobile-web-app-capable
  - Google Fonts: Poppins (preconnect + preload)
  - 模块加载顺序:
    1. index-hqKvQFI7.js (主 bundle, ~2MB)
    2. markdown-Ce2Umeb2.js (markdown, modulepreload)
    3. vendor-DpYitQz5.js (vendor, modulepreload)
```

### 2. 关键性能优化技术

#### A. Module Preloading
```html
<link rel="modulepreload" crossorigin href="/assets/markdown-Ce2Umeb2.js">
<link rel="modulepreload" crossorigin href="/assets/vendor-DpYitQz5.js">
```
**原理**: `modulepreload` 在浏览器解析 HTML 时立即开始下载依赖 chunk，不等待主 JS 解析。
这比 `preload` 更适合 ES modules，因为它会同时解析模块依赖图。

#### B. Dynamic Import (Code Splitting)
```javascript
// UI Controller 懒加载
const { uiController } = await import("./ui-controller")

// Terminal 懒加载
const terminal = await import("./terminal")
```

#### C. SSE Streaming (替代 WebSocket)
WorkBuddy 使用 **Server-Sent Events** 而非 WebSocket 进行实时通信:

```
关键特性:
- 端点: /api/v1/acp (ACP Protocol)
- 自动重连: 指数退避 (2s, 4s, 8s... 最大 60s)
- AbortController 管理: sseAbortController
- 消息类型:
  - agent_message_chunk: AI 消息流
  - agent_thought_chunk: AI 思考过程
  - available_commands_update: 可用命令更新
```

SSE 连接实现要点:
```javascript
async tryEstablishGetSse(retry = 0) {
  const headers = {
    Accept: "text/event-stream",
    "acp-connection-id": this.connectionId
  };
  const response = await fetch("/api/v1/acp", { headers, signal: abortController.signal });
  this.readSseStream(response);
}

readSseStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  // 逐块读取并解析 SSE 事件
}
```

#### D. CSS Containment
```css
--tw-contain-size    /* 元素尺寸独立，不影响外部布局 */
--tw-contain-layout  /* 内部布局不影响外部 */
--tw-contain-paint   /* 子元素不会溢出绘制范围 */
```

#### E. Tailwind CSS v4
- 使用 CSS 变量而非 JS 注入了新版本特性
- 自动 tree-shaking: 只生成使用到的工具类
- 零运行时 JS 开销

### 3. 组件架构

#### 侧边栏 (Sidebar)
```
Navigation: ["plugins","terminal","canvas","canvas-pane","remote-control",
             "settings","docs","editor","changes","metrics","workers",
             "logs","keybindings","stats","traces"]

结构:
- sidebar-header: Logo + Brand
- sidebar-nav: 导航项列表
- sidebar-content: 可滚动内容区
- sidebar-footer: 用户信息
```

#### 编辑器 (Monaco-based)
```
Editor View:
- editor-sidebar: 文件资源管理器 (file tree, quick open, recent files)
- editor-main: Monaco 编辑器主区域
- themes: 支持 dark/light 切换
- 功能: 文件创建/删除/重命名, 快速打开, 代码高亮, 多 Tab

Monaco 加载方式:
@monaco-editor/loader 按需从 CDN 加载:
  paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs" }
```

#### 终端 (xterm.js + Ghostty)
```
- xterm.js 负责终端渲染
- Ghostty WebAssembly 实现 VT 解析 (更快)
- 支持多个终端会话 (tile-based)
- Canvas 画布布局管理器
```

#### 消息流 (SSE-based)
```
消息状态管理:
- timeline: 消息时间线数组
- streaming: 标记是否正在流式传输
- subagentTimeline: 子代理消息时间线
- 累加式更新: 每个 chunk 追加到现有消息

消息类型:
- agent_message_chunk: 追加到当前 assistant 消息
- agent_thought_chunk: 追加到 thinking 区域
- 完成标记: streaming 设为 false
```

#### Changes/Diff Viewer
```
changes-timeline: 变更时间线视图
changes-diff-scroll: 差异滚动组件
changes-badge: 创建/修改/删除标记
```

### 4. 状态管理 (Zustand)

```javascript
// 使用 useSyncExternalStore + subscribe pattern
const state = useSyncExternalStore(
  store.subscribe,
  () => selector(store.getState()),
  () => selector(store.getInitialState())
);
```

### 5. 主题系统

```css
/* Dark Theme (default) */
--color-bg-primary: #121314
--color-bg-secondary: #1e1f22
--color-bg-tertiary: #242526
--color-bg-hover: #2d2e30
--color-border-default: #3f4044
--color-border-muted: #2d2e30
--color-text-primary: #f5f5f6
--color-text-secondary: #a0a0a8
--color-text-tertiary: #6a6a72
--color-accent-brand: #6c5ce7

/* Light Theme */
[data-theme=light] {
  --color-bg-primary: #fefefe
  /* ... */
}
```

---

## 关键技术要点 (复刻建议)

### 编辑器快速加载的关键

1. **Monaco Editor 不打包进 bundle**
   - 通过 CDN 动态加载 Monaco 核心
   - 入口 bundle 不含编辑器代码 (~2MB 主要用于 UI/业务逻辑)
   - Monaco 的 ~2MB+ workers 从 CDN 按需加载

2. **Module Preloading 优化瀑布流**
   - `modulepreload` 让浏览器尽早发现依赖
   - 减少 JavaScript 解析阻塞时间

3. **动态导入避免首屏阻塞**
   - 编辑器、终端等重组件延迟加载
   - 只有当前视图需要的代码才加载

4. **SSE 替代 WebSocket**
   - SSE 更轻量，基于 HTTP
   - 自动重连内置在协议中
   - 更好的代理/防火墙兼容性

### 推荐的复刻方向

1. **先搭建基础框架**: Vite + React 18 + Tailwind CSS v4
2. **Monaco Editor 集成**: 使用 @monaco-editor/loader 从 CDN 加载
3. **文件树**: 实现虚拟滚动文件浏览器
4. **SSE 流式通信**: 实现 chunk-based 消息流
5. **Sidebar 导航**: 实现可折叠的多面板布局

---

## 文件清单

| 文件 | 大小 | 用途 |
|------|------|------|
| index.html | 1.8KB | 入口 HTML, modulepreload 配置 |
| assets/index-hqKvQFI7.js | 1,930KB | 主应用 bundle |
| assets/index-DixJ0kqk.js | 6KB | 入口模块 (DOM query) |
| assets/index-CNL64lkN.css | 177KB | Tailwind CSS + 自定义样式 |
| assets/vendor-DpYitQz5.js | 132KB | React DOM vendor |
| assets/markdown-Ce2Umeb2.js | 161KB | react-markdown 渲染器 |
| sandbox_proxy.html | 5KB | 沙箱代理页面 |
| sw.js | 2KB | Service Worker |
| workbox-fed2bdfe.js | 21KB | Workbox PWA 库 |
