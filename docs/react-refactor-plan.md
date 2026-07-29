# Pan 前端 React 18 + Vite + Tailwind 重构方案

## 设计决策

| 维度 | 选择 | 理由（长期可维护性） |
|------|------|-------------------|
| 移动端 | 合并为单一 SPA | 一套代码、一次测试、响应式 |
| CSS | **Tailwind CSS 4** | CSS-first 配置、无死 CSS、`dark:` 深色模式、CodeBuddy 同款 |
| Markdown | react-markdown + rehype-highlight + rehype-katex | 安全无 XSS、可注入自定义组件、React 生态原生 |
| 状态管理 | Zustand | 1KB、无 Context 重渲染、WS handler 外部可访问 |
| 迁移策略 | **双前端长期共存** | 旧前端 `/` 稳定运行，新前端 `/react/*` 持续迭代，永不下线旧版 |
| 开发工作流 | Vite dev server + 代理 FastAPI | HMR 是长期开发效率的前提 |
| 后端挂载 | 新 `packages/web/dist/` | 干净断开旧约定，两套独立部署 |
| 包管理器 | **pnpm** | 速度快、磁盘高效、严格依赖 |
| Node 版本 | 锁定 LTS（.nvmrc） | 团队协作一致性 |
| API 类型 | **openapi-typescript 自动生成** | 后端 FastAPI OpenAPI → 前端 TS 类型，避免漂移 |
| 虚拟滚动 | **@tanstack/react-virtual** | 长消息列表性能保障 |
| 错误监控 | Sentry（可选） | 生产环境错误追踪 |

---

## 共存架构

```
同一台服务器，同一组后端 API，两套前端并存

URL                 → 前端            → 状态
/                   旧 Vanilla app     → 稳定后备，按需适配
/react/*            新 React SPA      → 主要开发目标
/api/*              共享后端 API       → 面向新前端演进
/ws                 共享 WebSocket     → 面向新前端演进
```

**后端开发优先级**：后端以新 React 前端为第一目标。若后端 API 演进导致旧前端兼容问题，**调整旧前端进行适配**，不回退后端变更。旧前端是跟随方，不是制约方。

**server.py 变化极简**，只需新增两个路由：
```python
# 新增：React SPA 入口
app.mount("/react", StaticFiles(directory=REACT_DIST, html=True), name="react")

# 保留：旧前端不变
app.mount("/static", StaticFiles(directory=STATIC_DIR))
@app.get("/")  # 返回旧 index.html —— 不变
```

---

## 新项目结构

```
packages/web/
├── index.html                 # ← 旧入口（保持不动）
├── mobile.html                # ← 旧入口（保持不动，后续删除）
├── ts/app.ts                  # ← 旧源码（保持不动）
├── static/                    # ← 旧产物（保持不动）
│   ├── css/styles.css
│   └── js/app.js
│
├── src/                       # ← 新 React 源码
│   ├── main.tsx               #    入口：ReactDOM.createRoot
│   ├── App.tsx                #    根组件 + ErrorBoundary + 路由
│   ├── router.tsx             #    react-router-dom 配置（含 basename）
│   ├── index.html             #    Vite 入口 HTML
│   ├── index.css              #    Tailwind 4 入口（@import "tailwindcss"）
│   │
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── TopBar.tsx
│   │   │   ├── SettingsPanel.tsx
│   │   │   └── ChatLayout.tsx
│   │   ├── chat/
│   │   │   ├── ChatMessages.tsx    # 使用 @tanstack/react-virtual
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── ToolGroup.tsx
│   │   │   ├── ThinkingBlock.tsx
│   │   │   ├── InputRow.tsx
│   │   │   └── MarkdownRenderer.tsx
│   │   ├── session/
│   │   │   ├── SessionList.tsx
│   │   │   ├── SessionItem.tsx
│   │   │   ├── NewSessionModal.tsx
│   │   │   ├── ImportModal.tsx
│   │   │   └── SessionMenu.tsx
│   │   ├── worker/
│   │   │   └── WorkerDot.tsx
│   │   ├── ui/
│   │   │   ├── Toast.tsx
│   │   │   ├── Button.tsx
│   │   │   └── Modal.tsx
│   │   └── ErrorBoundary.tsx       # 新增：错误边界
│   │
│   ├── stores/
│   │   ├── sessionStore.ts
│   │   ├── workerStore.ts
│   │   ├── uiStore.ts
│   │   └── adapterStore.ts
│   │
│   ├── hooks/
│   │   ├── useWebSocket.ts          # 指数退避重连
│   │   ├── useSessionHistory.ts
│   │   ├── useMarkdown.ts
│   │   └── useMediaQuery.ts
│   │
│   ├── services/
│   │   ├── api.ts                   # fetch 封装
│   │   └── ws.ts                    # WS 单例
│   │
│   ├── types/
│   │   ├── api.d.ts                 # ← openapi-typescript 自动生成
│   │   └── index.ts                 # ← 前端独有类型
│   │
│   ├── utils/
│   │   ├── format.ts
│   │   └── clipboard.ts
│   │
│   └── editor/                # ← 未来 Monaco 编辑器模块
│       ├── EditorView.tsx
│       ├── FileTree.tsx
│       ├── FileTabs.tsx
│       └── CodeEditor.tsx
│
├── dist/                      # ← Vite 构建产物（gitignored）
│   ├── index.html
│   └── assets/
│
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json              # 根引用
├── tsconfig.app.json          # 应用源码
├── tsconfig.node.json         # Node 构建
├── eslint.config.js
├── .prettierrc
├── .nvmrc                     # Node 版本锁定
└── package.json
```

---

## 组件分解清单

| 组件 | 路径 | 替换 app.ts 行号 | Zustand Store |
|------|------|-----------------|---------------|
| `App` + `ChatLayout` | `src/App.tsx` + `components/layout/ChatLayout.tsx` | init() 1770-2080 | sessionStore, workerStore, uiStore |
| `Sidebar` | `components/layout/Sidebar.tsx` | renderSessionList 492-553, sidebar logic | sessionStore |
| `SessionList` | `components/session/SessionList.tsx` | renderSessionList 492-553 | sessionStore |
| `SessionItem` | `components/session/SessionItem.tsx` | session DOM 492-553 | sessionStore |
| `TopBar` | `components/layout/TopBar.tsx` | top bar logic (scattered) | sessionStore, workerStore |
| `SettingsPanel` | `components/layout/SettingsPanel.tsx` | 1087-1269 | adapterStore, workerStore |
| `ChatMessages` | `components/chat/ChatMessages.tsx` | 728-859 (chunked render) | sessionStore |
| `MessageBubble` | `components/chat/MessageBubble.tsx` | 861-1085 (_renderMsgEl) | — (props only) |
| `ToolGroup` | `components/chat/ToolGroup.tsx` | _renderToolGroup | — (props only) |
| `ThinkingBlock` | `components/chat/ThinkingBlock.tsx` | thinking DOM logic | uiStore |
| `InputRow` | `components/chat/InputRow.tsx` | send() 1350-1434, input drafts | sessionStore |
| `MarkdownRenderer` | `components/chat/MarkdownRenderer.tsx` | renderMarkdown() 192-250 | — (useMarkdown hook) |
| `NewSessionModal` | `components/session/NewSessionModal.tsx` | newSession() 1435-1528 | sessionStore |
| `ImportModal` | `components/session/ImportModal.tsx` | fetchCbc* fetchKimi* 413-490 | sessionStore |
| `Toast` | `components/ui/Toast.tsx` | toast() 2081-2153 | uiStore |
| `WorkerDot` | `components/worker/WorkerDot.tsx` | worker dot DOM | workerStore |

---

## Zustand Store 设计

### sessionStore (`stores/sessionStore.ts`)
```typescript
interface SessionStore {
  // State
  sessions: Session[];
  currentSessionId: string | null;
  currentMessages: Message[];
  hasMoreMessages: boolean;
  multiSelectMode: boolean;
  selectedIds: Set<string>;
  inputDrafts: Record<string, string>;
  sessionUnread: Record<string, { thinking: number; tool: number }>;

  // Actions
  fetchSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<void>;
  loadOlderMessages: () => Promise<void>;
  createSession: (name: string, cwd: string, adapter: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  batchDeleteSessions: () => Promise<void>;
  renameSession: (id: string, name: string) => Promise<void>;
  setInputDraft: (id: string, draft: string) => void;
  addMessage: (msg: Message) => void;
  toggleMultiSelect: () => void;
  toggleBubbleView: () => void;
}
```

### workerStore (`stores/workerStore.ts`)
```typescript
interface WorkerStore {
  workers: Record<string, WorkerInfo>;
  currentWorkerId: string | null;

  startWorker: (sessionId: string) => Promise<void>;
  killWorker: (workerId: string) => Promise<void>;
  interruptWorker: (workerId: string) => Promise<void>;
  restartWorker: (workerId: string) => Promise<void>;
  updateWorker: (data: Partial<WorkerInfo>) => void;
}
```

### uiStore (`stores/uiStore.ts`)
```typescript
interface UIStore {
  settingsOpen: boolean;
  toastQueue: ToastMessage[];
  bubbleViewEnabled: boolean;
  rendering: boolean;

  toggleSettings: () => void;
  showToast: (msg: string, type?: 'info' | 'error') => void;
  dismissToast: (id: string) => void;
  setRendering: (v: boolean) => void;
}
```

### adapterStore (`stores/adapterStore.ts`)
```typescript
interface AdapterStore {
  adapters: AdapterConfig[];
  currentModel: string;
  currentMode: string;
  thinkEnabled: boolean;
  effort: string;

  fetchAdapters: () => Promise<void>;
  applySettings: (sessionId: string, settings: Partial<Settings>) => Promise<void>;
}
```

---

## Hooks 设计

| Hook | 文件 | 职责 | 替换 app.ts |
|------|------|------|------------|
| `useWebSocket` | `hooks/useWebSocket.ts` | 建立/重连 WS、分发 `session`/`worker`/`stream` 事件到对应 store | connectWs() 268-362 |
| `useSessionHistory` | `hooks/useSessionHistory.ts` | 监听滚动到顶部 → 加载更早消息、chunked render | loadOlderMessages 561-673、chunk render 728-859 |
| `useMarkdown` | `hooks/useMarkdown.ts` | memoized markdown → HTML pipeline（marked → hljs → katex） | renderMarkdown() 192-250 |
| `useMediaQuery` | `hooks/useMediaQuery.ts` | Tailwind 断点同步（检测移动端） | UAParser 逻辑 |

### useWebSocket 设计要点
```typescript
function useWebSocket() {
  // 单例 WS 连接（每个页面独立连接，不需要跨页面共享）
  // 自动重连：指数退避（1s → 2s → 4s → ... 最大 30s）
  // 心跳：每 30s 发 ping，超时则重连
  // 消息路由:
  //   session.update    → sessionStore.getState().updateSession(...)
  //   stream.chunk      → sessionStore.getState().appendChunk(...)
  //   stream.end        → sessionStore.getState().finalizeMessage(...)
  //   worker.status     → workerStore.getState().updateWorker(...)
  // 关键：在 WS 回调中用 useStore.getState() 而非 useStore()
  //       否则 React 组件不会重渲染
}
```

**重连策略改进**（指数退避 + 抖动）：
```typescript
const MAX_RETRY_DELAY = 30_000;
const BASE_RETRY_DELAY = 1_000;

function getRetryDelay(attempt: number): number {
  const delay = Math.min(BASE_RETRY_DELAY * 2 ** attempt, MAX_RETRY_DELAY);
  // 加抖动避免雪崩
  return delay + Math.random() * 1000;
}
```

---

## 构建配置

### package.json
```json
{
  "name": "pan-web",
  "private": true,
  "type": "module",
  "packageManager": "pnpm@9.7.0",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint .",
    "format": "prettier --write .",
    "test": "vitest",
    "gen:api": "openapi-typescript http://localhost:8767/api/openapi.json -o src/types/api.d.ts",
    "analyze": "vite-bundle-visualizer"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.0.0",
    "zustand": "^5.0.0",
    "react-markdown": "^9.0.1",
    "remark-gfm": "^4.0.0",
    "rehype-highlight": "^7.0.0",
    "rehype-katex": "^7.0.0",
    "katex": "^0.16.9",
    "highlight.js": "^11.9.0",
    "@tanstack/react-virtual": "^3.10.0",
    "@monaco-editor/react": "^4.6.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^6.0.0",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/vite": "^4.0.0",
    "typescript": "^5.6.0",
    "eslint": "^9.10.0",
    "prettier": "^3.3.3",
    "@eslint/js": "^9.10.0",
    "typescript-eslint": "^8.5.0",
    "eslint-plugin-react-hooks": "^5.0.0",
    "eslint-plugin-react-refresh": "^0.4.12",
    "eslint-config-prettier": "^9.1.0",
    "vitest": "^2.1.0",
    "@testing-library/react": "^16.0.0",
    "jsdom": "^25.0.0",
    "openapi-typescript": "^7.3.0",
    "vite-bundle-visualizer": "^1.2.0"
  },
  "engines": {
    "node": ">=20.0.0"
  }
}
```

**改进点**:
- Tailwind 4 + `@tailwindcss/vite` 插件（无需 postcss.config.js）
- React 19 + react-router-dom 7
- 新增 `@tanstack/react-virtual` 处理长消息列表
- 新增 `openapi-typescript` 自动生成 API 类型
- 新增 `gen:api` 脚本：从 FastAPI OpenAPI 生成 TS 类型
- 新增 `analyze` 脚本：bundle 体积分析
- 锁定 `packageManager` 字段（pnpm）

### vite.config.ts
```typescript
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

export default defineConfig(({ mode }) => {
  const isProd = mode === 'production';
  // 共存模式：生产构建部署到 /react/ 路径下
  const base = isProd ? '/react/' : '/';

  return {
    plugins: [react(), tailwindcss()],
    base,
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://localhost:8767',
          changeOrigin: true,
        },
        // 业务 WebSocket：注意区分 Vite HMR 自己的 WS（默认走 /__vite_ping）
        '/ws': {
          target: 'ws://localhost:8767',
          ws: true,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      sourcemap: true,
      rollupOptions: {
        output: {
          manualChunks: {
            'react-vendor': ['react', 'react-dom', 'react-router-dom'],
            'markdown-vendor': ['react-markdown', 'remark-gfm', 'rehype-highlight', 'rehype-katex'],
            'monaco-vendor': ['@monaco-editor/react'],
          },
        },
      },
    },
  };
});
```

**关键改进点**:
- 使用 `@tailwindcss/vite` 插件（Tailwind 4 原生 Vite 集成，无需 postcss.config.js）
- `base` 根据 mode 切换：开发 `/`，生产 `/react/`
- 手动 chunk 分割：react / markdown / monaco 各自独立 chunk
- 显式注释 Vite HMR WS 与业务 WS 的区别（避免混淆）

### tailwind.config.js（Tailwind 4 CSS-first 风格，仅做主题扩展）
```javascript
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class', // 还是支持 .dark 类切换
  content: ['./src/**/*.{ts,tsx}', './index.html'],
  theme: {
    extend: {
      colors: {
        // 保留少量自定义颜色（Tailwind 4 推荐用 CSS @theme 在 CSS 中定义）
        'bg-primary': '#0d1117',
        'bg-secondary': '#161b22',
        'bg-tertiary': '#21262d',
        'text-primary': '#e6edf3',
        'text-secondary': '#8b949e',
        'accent': '#58a6ff',
        'accent-hover': '#79c0ff',
        'border-default': '#30363d',
        'success': '#3fb950',
        'warning': '#d29922',
        'danger': '#f85149',
      },
    },
  },
  plugins: [],
};
```

**Tailwind 4 关键变化**：
- 不再需要 `postcss.config.js` 和 `autoprefixer`（Vite 插件处理）
- 主题可以直接在 CSS 中用 `@theme` 定义（更现代）
- 启动时在 `src/index.css` 第一行写 `@import "tailwindcss";` 即可

### tsconfig 拆分（应用层 + Node 层）

**tsconfig.json**（根，仅做引用）
```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}
```

**tsconfig.app.json**（应用源码）
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedIndexedAccess": true,
    "verbatimModuleSyntax": true,
    "isolatedModules": true,
    "useDefineForClassFields": true,
    "resolveJsonModule": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "sourceMap": true,
    "types": ["vite/client"],
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    },
    "noEmit": true
  },
  "include": ["src"]
}
```

**tsconfig.node.json**（vite.config.ts 等构建脚本）
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "types": ["node"]
  },
  "include": ["vite.config.ts"]
}
```

**改进点**:
- 拆分 app/node 配置（Vite 官方推荐）
- 新增 `noUncheckedIndexedAccess`（更严格）
- 新增 `vite/client` 类型（import.meta.env、import.meta.glob）
- 新增 `verbatimModuleSyntax`（强制 type-only imports）
- `noEmit: true`（Vite 负责编译，tsc 只做类型检查）

### postcss.config.js（Tailwind 4 不再需要）
- Tailwind 4 通过 `@tailwindcss/vite` 插件处理，无需 PostCSS 配置
- 如果要保留 PostCSS（比如用 autoprefixer 给旧浏览器），可创建：
```javascript
export default { plugins: {} };
```

### .eslintrc（flat config 完整版）
```javascript
// eslint.config.js
import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import prettier from 'eslint-config-prettier';

export default tseslint.config(
  { ignores: ['dist/', 'static/', 'node_modules/'] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { window: 'readonly', document: 'readonly' },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  prettier.configs.disable
);
```

### .prettierrc
```json
{
  "semi": true,
  "singleQuote": true,
  "tabWidth": 2,
  "trailingComma": "all",
  "printWidth": 100
}
```

### .nvmrc（Node 版本锁定）
```
20
```

### .gitignore 新增条目
```
# React 前端构建产物
packages/web/dist/
packages/web/node_modules/
packages/web/.vite/

# 自动生成的 API 类型
packages/web/src/types/api.d.ts

# 测试覆盖率
packages/web/coverage/

# 编辑器
.vscode/
.idea/
```

---

## server.py 修改

**修改范围极小（约 15 行）**：

```python
# 1. 新增常量和判断
REACT_DIST_DIR = _WEB_DIR / "dist"
REACT_EXISTS = REACT_DIST_DIR.exists()  # 优雅降级：构建产物不存在时不挂载

# 2. 新增：React SPA 入口（共存模式，路径前缀 /react）
if REACT_EXISTS:
    app.mount("/react", StaticFiles(directory=str(REACT_DIST_DIR), html=True), name="react")
    # SPA fallback：/react/* 未匹配文件时返回 index.html（让前端路由处理）
    @app.get("/react/{full_path:path}")
    async def react_spa_fallback(full_path: str):
        if not (REACT_DIST_DIR / full_path).is_file():
            return FileResponse(REACT_DIST_DIR / "index.html")
        return FileResponse(REACT_DIST_DIR / full_path)

# 3. 旧前端保持不变
@app.get("/")  # ← 不变，仍返回 index.html / mobile.html
async def root():
    ...

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)))  # ← 不变
```

**关键改进点**:
- ✅ **优雅降级**：构建产物不存在时不挂载，避免启动报错
- ✅ **SPA fallback**：`/react/chat/123` 等前端路由能正常工作（之前会 404）
- ✅ **零改动旧逻辑**：`@app.get("/")` 和 `/static` 完全不变

**入口切换**（长期保留旧前端，按需切换默认入口）：
```python
# 将 React 设为默认入口时：
# app.mount("/", StaticFiles(directory=str(REACT_DIST_DIR), html=True))
# 旧前端仍可通过 /legacy 或其他路径访问
```

### ⚠️ React Router basename 配置（关键 bug 修复）

生产环境部署在 `/react/` 路径下，React Router 必须同步配置 basename，否则所有路由 404：

```typescript
// src/router.tsx
import { createBrowserRouter } from 'react-router-dom';

const isProd = import.meta.env.PROD;
const basename = isProd ? '/react' : '/';  // 生产用 /react，开发用 /

export const router = createBrowserRouter(
  [
    { path: '/', element: <ChatLayout /> },
    { path: '/editor', element: <EditorView /> },
    // ...
  ],
  { basename }
);
```

---

## 迁移顺序

### Phase 1: 基础设施搭建（可逆，不碰旧代码）

```
Step 1: 创建 .nvmrc (Node 20)、.gitignore 更新
Step 2: 创建 package.json、运行 pnpm install
Step 3: 创建 vite.config.ts、tsconfig 三件套（根 + app + node）
Step 4: 创建 tailwind.config.js、src/index.css（@import "tailwindcss"）
Step 5: 创建 src/index.html（Vite 入口，<!DOCTYPE html><div id="root">）
Step 6: 创建 src/main.tsx（ReactDOM.createRoot 空壳）
Step 7: 创建 src/App.tsx（只显示 "Hello React"）
Step 8: 创建 eslint.config.js、.prettierrc
Step 9: 运行 pnpm dev，验证 Vite 启动 + HMR
Step 10: 运行 pnpm build，验证 dist/ 输出
Step 11: 运行 pnpm gen:api，验证能从 FastAPI 拉取 OpenAPI 生成类型
Step 12: server.py 添加 /react 路由 + SPA fallback
Step 13: 浏览器访问 /react/，验证看到 "Hello React"
```

验证点：
- `pnpm dev` → localhost:5173 显示 React 页面
- `pnpm build` → dist/ 包含 index.html + assets/
- 访问 localhost:8767/react/ → 看到 React 页面
- API 代理正常（在 React 中 fetch `/api/sessions` 能拿到数据）

### Phase 2: 类型和状态层（无 UI 变化）

```
Step 14: pnpm gen:api → 生成 src/types/api.d.ts（API 类型）
Step 15: 创建 src/types/index.ts（前端独有类型：UI 状态、消息等）
Step 16: 创建 src/services/api.ts（fetch 封装 + 错误处理 + 重试）
Step 17: 创建 src/services/ws.ts（WS 单例 + 指数退避重连 + 心跳）
Step 18: 创建 4 个 Zustand stores: sessionStore, workerStore, uiStore, adapterStore
Step 19: 创建自定义 hooks: useWebSocket, useSessionHistory, useMarkdown, useMediaQuery
Step 20: 创建 src/components/ErrorBoundary.tsx
```

验证点：`pnpm dev` 无 TypeScript 错误，stores 初始化正常，WS 能连上后端。

### Phase 3: UI 组件逐模块开发（从易到难）

```
Step 14: 基础 UI 组件: Modal, Button, Toast, WorkerDot
Step 15: Layout 框架: ChatLayout + Sidebar + TopBar 骨架
Step 16: SessionList + SessionItem（替换 renderSessionList）
Step 17: InputRow（替换 send() + input drafts）
Step 18: MarkdownRenderer（替换 renderMarkdown）
Step 19: ChatMessages + MessageBubble + ToolGroup + ThinkingBlock
Step 20: SettingsPanel（替换 toggleSettings/syncPanel/applySettings）
Step 21: NewSessionModal + ImportModal
Step 22: ZapWrite（多选、删除、重命名等 CRUD）
```

每完成 2-3 个组件就 `pnpm build` 并在浏览器 `/react` 验证。

### Phase 4: 功能补齐测试

```
Step 23: WebSocket 实时消息流端到端测试
Step 24: 历史消息懒加载（滚动到底部）
Step 25: Worker 操作（start/kill/interrupt/restart/takeover）
Step 26: 导入 cbc/kimi 会话流程
Step 27: 移动端响应式适配
```

验证点：/react 完全可替代旧前端的所有功能。

### Phase 5: 长期双前端共存

旧版前端不下线，与新 React 前端长期共存：

```
Step 28: 保持当前架构不变：
   - /  → 旧 Vanilla 前端（生产稳定，永远不动）
   - /react/* → 新 React SPA（持续迭代）
Step 29: 更新 CODEBUDDY.md，标注双前端的单源规则：
   - 旧前端单源: ts/app.ts
   - 新前端单源: src/main.tsx
Step 30: 更新 scripts/pre-commit，同时校验两个前端：
   - 旧前端: npx tsc（ts/app.ts 编译）
   - 新前端: pnpm build（Vite 构建）
```

**入口切换方式**：通过修改 server.py 中的默认路由即可完成切换，无需删除旧代码。哪套前端作为 `/` 入口是可配置的选择，不涉及代码删除。

---

## Monaco 编辑器模块（未来扩展）

编辑器是初始需求但不在第一阶段实现，规划好目录结构即可：

```
src/editor/
├── EditorView.tsx    # 路由: /react/editor?path=/xxx
│   ├── FileTree.tsx  # 懒加载文件树 (POST /api/v1/fs/list)
│   ├── FileTabs.tsx  # 多标签栏
│   └── CodeEditor.tsx # Monaco Editor 包装
```

**Monaco 集成策略**：
- 使用 `@monaco-editor/react`（React 封装，自动处理 loader）
- **代码分割**：`React.lazy(() => import('./editor/EditorView'))`，Monaco 按需加载
- 文件内容通过 `GET /api/v1/files/download?path=...` 加载
- 文件保存通过 `POST /api/v1/files/upload?path=...`
- 纯前端编辑器，后端只要已有文件 API，无需额外后端改造

---

## 关键原则

1. **后端面向新前端开发** — 后端 API/WS 演进以 React 前端为第一目标，旧前端按需适配跟随
2. **旧前端永久保留** — `ts/app.ts` 持续维护，作为稳定后备运行，但不制约后端演进
3. **双前端长期共存** — `/` 旧 Vanilla 前端 + `/react/*` 新 React SPA
4. **组件从简开始** — 每完成一个组件就验证一个，不攒技术债
5. **Zustand 在 WS handler 外工作** — 这是选择 Zustand 而非 Context 的关键原因，WebSocket 回调可以直接 `useSessionStore.getState().xxx()`
6. **所有 API 路由不变** — React 和 Vanilla 共用完全相同的后端接口

---

## ⚠️ 关键改进点总结（v2）

### A. 生产部署路由 bug 修复
**原方案 bug**：vite.config.ts 的 `base: '/react/'` 只影响资源路径，React Router 不知道这个前缀，会 404。
**修复**：React Router 必须配置 `basename="/react"`，见上方 `router.tsx` 配置。

### B. Vite HMR WebSocket 与业务 WebSocket 不冲突
- Vite HMR 用独立 WS（默认 `ws://localhost:5173/__vite_ping`）
- 业务 WS 走 `/ws` 代理到 FastAPI
- 两者互不干扰，**不需要特殊处理**

### C. API 类型自动生成（取代手工 types/index.ts）
```bash
# 在 FastAPI 启动后运行：
pnpm gen:api
# → 从 http://localhost:8767/api/openapi.json 生成 src/types/api.d.ts
```
- 后端 FastAPI 自带 OpenAPI schema（`/api/openapi.json`）
- 前端用 `openapi-typescript` 自动生成类型
- **解决前后端类型漂移问题**（长期维护关键）
- 原方案的 `types/index.ts` 仍保留，但只放前端独有类型（如 UI 状态）

### D. 虚拟滚动具体方案
**原方案问题**：只写 "virtual scroll" 没指定库，长会话消息会卡死。
**修复**：用 `@tanstack/react-virtual`：
```typescript
// ChatMessages.tsx
import { useVirtualizer } from '@tanstack/react-virtual';

function ChatMessages() {
  const parentRef = useRef<HTMLDivElement>(null);
  const messages = useSessionStore(s => s.currentMessages);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 100,  // 每条消息预估高度
    overscan: 5,
  });

  return (
    <div ref={parentRef} style={{ overflow: 'auto', height: '100%' }}>
      <div style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map(vItem => (
          <MessageBubble key={vItem.key} message={messages[vItem.index]} />
        ))}
      </div>
    </div>
  );
}
```

### E. 双前端切换方式

旧前端不下线，入口切换通过环境变量控制：

```python
# server.py 通过环境变量控制默认入口
FRONTEND_MODE = os.environ.get("PAN_FRONTEND", "legacy")  # legacy | react

if FRONTEND_MODE == "react":
    app.mount("/", StaticFiles(directory=str(REACT_DIST_DIR), html=True))
else:
    # 旧前端作为默认入口（当前默认值）
    pass
```

无论默认入口是哪套，另一套始终可通过对应路径访问（`/` 或 `/react`）。

### F. bundle 体积监控
```bash
pnpm analyze
# → 生成可视化 bundle 结构图，及时发现体积问题
```

### G. 错误边界与监控
```typescript
// src/components/ErrorBoundary.tsx
import { Component, ReactNode } from 'react';

export class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 上报到 Sentry（可选）
    console.error('React Error Boundary:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return <div>页面出错了，<a href="/">返回旧版</a></div>;
    }
    return this.props.children;
  }
}
```

### H. CSS 变量与 Tailwind 主题分离
**策略**：旧 styles.css 与 React 前端各自独立维护主题，互不依赖。
- 旧 `styles.css` 保持不变（旧前端专属）
- React 前端用 Tailwind 4 的 CSS `@theme` 定义颜色（CSS-first 配置）
- 两套主题系统独立运行，不共享 CSS 变量

```css
/* src/index.css */
@import "tailwindcss";

@theme {
  --color-bg-primary: #0d1117;
  --color-bg-secondary: #161b22;
  --color-accent: #58a6ff;
  /* ... */
}

.dark {
  @theme {
    --color-bg-primary: #0d1117;
    /* 深色主题覆盖 */
  }
}
```
