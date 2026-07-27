# WorkBuddy 前端实现指南 - 编辑器与快速加载

## 目录
1. [架构总览](#架构总览)
2. [编辑器实现](#编辑器实现)
3. [文件树实现](#文件树实现)
4. [快速加载技术](#快速加载技术)
5. [消息流 (SSE) 实现](#消息流-sse-实现)
6. [CSS 片段精选](#css-片段精选)

---

## 架构总览

WorkBuddy 的前端是一个 Vite + React 18 + Tailwind CSS 构建的 SPA，核心设计原则是:

```
入口轻量 → 核心库按需加载 → 重组件延迟加载 → SSE 流式数据
```

**Bundle 拆分策略:**
- `index-hqKvQFI7.js` (1.9MB): 主应用 (UI 组件、状态管理、业务逻辑)
- `vendor-DpYitQz5.js` (132KB): React + ReactDOM 18.3.1
- `markdown-Ce2Umeb2.js` (161KB): react-markdown 渲染器
- Monaco Editor: **不在 bundle 中**，通过 CDN 动态加载

---

## 编辑器实现

### 1. Monaco Editor 加载方式

WorkBuddy 使用 `@monaco-editor/loader` 从 CDN 按需加载 Monaco:

```typescript
// 配置 (在应用初始化时)
import loader from '@monaco-editor/loader';

// CDN 配置
loader.config({
  paths: {
    vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs'
  }
});

// 或者使用 urls 方式 (推荐)
loader.config({
  urls: {
    monacoBase: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs'
  }
});
```

### 2. 编辑器组件核心实现

从逆向分析中提取的编辑器创建模式:

```typescript
import { useRef, useEffect, useCallback } from 'react';
import loader from '@monaco-editor/loader';

interface EditorProps {
  content: string;
  language?: string;
  filePath?: string;
  theme?: 'dark' | 'light';
  onContentChange?: (content: string) => void;
}

// Monaco 主题配置 (从 WorkBuddy 提取)
const darkTheme: editor.IStandaloneEditorConstructionOptions = {
  background: '#121314',
  foreground: '#f5f5f6',
  cursor: '#ffffff',
  // ...
};

const lightTheme = {
  background: '#ffffff',
  foreground: '#1e1e1e',
  cursor: '#000000',
  // ...
};

function CodeEditor({ content, language = 'text', filePath, theme = 'dark' }: EditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import('monaco-editor') | null>(null);

  // 初始化编辑器
  useEffect(() => {
    let disposed = false;

    async function init() {
      // 动态加载 Monaco (首次调用才会真正下载)
      const monaco = await loader.init();
      if (disposed) return;

      monacoRef.current = monaco;

      // 创建或获取 model
      const uri = monaco.Uri.parse(filePath || `file:///untitled`);
      let model = monaco.editor.getModel(uri);
      if (!model) {
        model = monaco.editor.createModel(content, language, uri);
      }

      // 创建编辑器实例
      const editor = monaco.editor.create(containerRef.current!, {
        model,
        automaticLayout: true,  // 自动响应容器大小变化
        theme: theme === 'dark' ? darkTheme as any : lightTheme as any,
        minimap: { enabled: true },
        scrollBeyondLastLine: false,
        fontSize: 14,
        lineNumbers: 'on',
        renderWhitespace: 'selection',
        tabSize: 2,
      });

      editorRef.current = editor;
    }

    init();

    return () => {
      disposed = true;
      editorRef.current?.dispose();
    };
  }, []); // 只在挂载时创建一次

  // 更新主题
  useEffect(() => {
    if (monacoRef.current) {
      monacoRef.current.editor.setTheme(
        theme === 'dark' ? 'dark-theme' : 'light-theme'
      );
    }
  }, [theme]);

  // 更新语言
  useEffect(() => {
    const editor = editorRef.current;
    const model = editor?.getModel();
    if (model && monacoRef.current) {
      monacoRef.current.editor.setModelLanguage(model, language);
    }
  }, [language]);

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />;
}
```

### 3. Diff 编辑器实现

WorkBuddy 的 Changes/Changes 视图使用 diff 编辑器:

```typescript
function DiffEditor({
  original,
  modified,
  originalLanguage,
  modifiedLanguage,
  originalPath,
  modifiedPath,
}: DiffEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const diffEditorRef = useRef<editor.IStandaloneDiffEditor | null>(null);

  useEffect(() => {
    async function init() {
      const monaco = await loader.init();

      // 创建 diff editor
      const diffEditor = monaco.editor.createDiffEditor(containerRef.current!, {
        automaticLayout: true,
        readOnly: true,
        renderSideBySide: true,  // 并排比较
        ignoreTrimWhitespace: false,
      });

      // 创建或获取 original model
      let originalModel = monaco.editor.getModel(
        monaco.Uri.parse(originalPath || 'original://')
      );
      if (!originalModel) {
        originalModel = monaco.editor.createModel(
          original,
          originalLanguage || 'text',
          monaco.Uri.parse(originalPath || 'original://')
        );
      }

      // 创建或获取 modified model
      let modifiedModel = monaco.editor.getModel(
        monaco.Uri.parse(modifiedPath || 'modified://')
      );
      if (!modifiedModel) {
        modifiedModel = monaco.editor.createModel(
          modified,
          modifiedLanguage || 'text',
          monaco.Uri.parse(modifiedPath || 'modified://')
        );
      }

      diffEditor.setModel({
        original: originalModel,
        modified: modifiedModel,
      });

      diffEditorRef.current = diffEditor;
    }

    init();

    return () => {
      diffEditorRef.current?.dispose();
    };
  }, []);

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />;
}
```

### 4. Model 池管理

WorkBuddy 通过 Model 引用计数管理编辑器的文件模型:

```typescript
// Model 管理工具
function getOrCreateModel(
  monaco: typeof import('monaco-editor'),
  content: string,
  language: string,
  filePath: string
): editor.ITextModel {
  const uri = monaco.Uri.parse(filePath);
  let model = monaco.editor.getModel(uri);

  if (model) {
    // 更新已有 model 内容 (用于外部文件变更)
    if (model.getValue() !== content) {
      model.setValue(content);
    }
  } else {
    model = monaco.editor.createModel(content, language, uri);
  }

  return model;
}

// Model 销毁 (当 Tab 关闭时)
function disposeModelIfUnused(
  monaco: typeof import('monaco-editor'),
  filePath: string
) {
  const uri = monaco.Uri.parse(filePath);
  const model = monaco.editor.getModel(uri);
  if (model && !model.isDisposed()) {
    model.dispose();
  }
}
```

---

## 文件树实现

WorkBuddy 的文件树是**纯自定义实现**，不使用 react-window 等虚拟化库。

### 核心结构

```typescript
interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileNode[];
  expanded?: boolean;
}

function FileTree({ rootPath, onFileSelect }: FileTreeProps) {
  const [tree, setTree] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const treeRef = useRef<HTMLDivElement>(null);

  // 从后端加载文件列表
  const loadTree = useCallback(async (dirPath: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/files?path=${encodeURIComponent(dirPath)}`);
      const data = await response.json();
      setTree(data.files);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // 切换目录展开/折叠
  const toggleExpand = useCallback((nodePath: string) => {
    setTree(prev => toggleNode(prev, nodePath));
  }, []);

  // 递归切换节点
  function toggleNode(nodes: FileNode[], targetPath: string): FileNode[] {
    return nodes.map(node => {
      if (node.path === targetPath) {
        return { ...node, expanded: !node.expanded };
      }
      if (node.children) {
        return { ...node, children: toggleNode(node.children, targetPath) };
      }
      return node;
    });
  }

  // 渲染树节点 (递归)
  function renderNode(node: FileNode, depth: number = 0) {
    const isSelected = node.path === selectedPath;
    const paddingLeft = 8 + depth * 16;

    return (
      <div key={node.path}>
        <div
          className={`file-tree-item ${isSelected ? 'selected' : ''}`}
          style={{ paddingLeft }}
          onClick={() => {
            if (node.type === 'directory') {
              toggleExpand(node.path);
            } else {
              setSelectedPath(node.path);
              onFileSelect(node.path);
            }
          }}
        >
          {/* 缩进辅助线 */}
          {Array.from({ length: depth }, (_, i) => (
            <div
              key={i}
              className="file-tree-indent-guide"
              style={{ left: 8 + i * 16 }}
            />
          ))}

          {/* 展开/折叠箭头 */}
          {node.type === 'directory' ? (
            <span className={`file-tree-chevron ${node.expanded ? 'expanded' : ''}`}>
              ▶
            </span>
          ) : (
            <span className="file-tree-chevron-spacer" />
          )}

          {/* 文件/目录名 */}
          <span className="file-tree-item-name">{node.name}</span>
        </div>

        {/* 展开的子节点 */}
        {node.expanded && node.children?.map(child => renderNode(child, depth + 1))}
      </div>
    );
  }

  return (
    <div className="file-tree" ref={treeRef}>
      {/* 标题栏 */}
      <div className="file-tree-title-bar">
        <span className="file-tree-title-text">EXPLORER</span>
        <div className="file-tree-title-actions">
          <button className="file-tree-title-action" onClick={() => loadTree(rootPath)}>
            ↻
          </button>
        </div>
      </div>

      {/* 树内容 */}
      <div className="file-tree-content">
        {loading && <div className="file-tree-loading">Loading...</div>}
        {error && (
          <div className="file-tree-empty">
            Failed to load
            <button className="file-tree-retry-btn" onClick={() => loadTree(rootPath)}>
              Retry
            </button>
          </div>
        )}
        {!loading && !error && tree.length === 0 && (
          <div className="file-tree-empty">Empty folder</div>
        )}
        {!loading && !error && tree.map(node => renderNode(node))}
      </div>
    </div>
  );
}
```

### 文件树重命名功能

```typescript
// 内联重命名
function RenameInput({
  initialName,
  onRename,
  onCancel,
}: {
  initialName: string;
  onRename: (newName: string) => void;
  onCancel: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(initialName);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      onRename(value);
    } else if (e.key === 'Escape') {
      onCancel();
    }
  };

  return (
    <input
      ref={inputRef}
      className="file-tree-rename-input"
      value={value}
      onChange={e => setValue(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={() => onRename(value)}
    />
  );
}
```

---

## 快速加载技术

### 1. Vite Module Preloading

这是 WorkBuddy 快速加载的**核心技术**之一:

```html
<!-- index.html -->
<script type="module" crossorigin src="/assets/index-hqKvQFI7.js"></script>
<link rel="modulepreload" crossorigin href="/assets/markdown-Ce2Umeb2.js">
<link rel="modulepreload" crossorigin href="/assets/vendor-DpYitQz5.js">
<link rel="stylesheet" crossorigin href="/assets/index-CNL64lkN.css">
```

**工作原理:**
- `modulepreload` 在 HTML 解析阶段就启动下载
- 浏览器会解析模块的 import 依赖图
- 确保依赖在主模块执行前已经可用
- 比 `preload` 更适合 ES modules (因为 preload 不会解析模块依赖)

**在你的 Vite 项目中使用:**
```typescript
// vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // Vite 会自动处理 code splitting 和 modulepreload
    rollupOptions: {
      output: {
        // 手动拆包策略
        manualChunks: {
          vendor: ['react', 'react-dom'],
          markdown: ['react-markdown', 'remark-gfm', 'rehype-highlight'],
        },
      },
    },
  },
});
```

### 2. Dynamic Import (代码分割)

Monaco Editor 不在主 bundle 中:

```typescript
// 延迟加载编辑器组件
const MonacoEditor = lazy(() => import('./MonacoEditor'));

// 延迟加载终端
const Terminal = lazy(() => import('./Terminal'));

// 延迟加载 Canvas 画布
const Canvas = lazy(() => import('./Canvas'));

// 使用 Suspense 包裹
function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/editor" element={<MonacoEditor />} />
        <Route path="/terminal" element={<Terminal />} />
      </Routes>
    </Suspense>
  );
}
```

### 3. Monaco Loader 异步初始化

```typescript
// 首次调用 init() 时才真正下载 Monaco
// 此时已完成首屏渲染，不会阻塞用户交互
import loader from '@monaco-editor/loader';

// 在组件挂载时异步加载
useEffect(() => {
  let cancelled = false;

  async function loadMonaco() {
    const monaco = await loader.init();  // 开始下载 Monaco (~2MB+)
    if (cancelled) return;

    // 创建编辑器
    monaco.editor.create(container, options);
  }

  // 延迟启动，让首屏先渲染完毕
  const timer = setTimeout(loadMonaco, 50);

  return () => {
    cancelled = true;
    clearTimeout(timer);
  };
}, []);
```

### 4. CSS Containment 优化

```css
/* 隔离组件重绘影响范围 */
.file-tree-content {
  contain: layout style paint;
  /* --tw-contain-layout: ; */
  /* --tw-contain-paint: ; */
}

/* 告诉浏览器这部分的渲染是独立的 */
.editor-view {
  contain: layout style paint;
  content-visibility: auto;
  contain-intrinsic-size: 100% 100%;
}
```

### 5. 资源预连接

```html
<!-- 提前建立 CDN 连接 -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preconnect" href="https://cdn.jsdelivr.net">

<!-- DNS 预解析 -->
<link rel="dns-prefetch" href="https://cdn.jsdelivr.net">
```

---

## 消息流 (SSE) 实现

WorkBuddy 使用 SSE 而非 WebSocket 进行 AI 消息的实时流式传输。

### SSE Client 实现

```typescript
class ACPConnection {
  private baseUrl: string;
  private connectionId: string | null = null;
  private sessionToken: string | null = null;
  private abortController: AbortController | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private token: string | null = null;

  // 建立 SSE 连接
  async tryEstablishGetSse(retryCount = 0) {
    const headers: Record<string, string> = {
      'Accept': 'text/event-stream',
    };

    if (this.connectionId) {
      headers['acp-connection-id'] = this.connectionId;
    }

    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`;
    }

    try {
      const response = await fetch(`${this.baseUrl}/api/v1/acp`, {
        headers,
        signal: this.abortController?.signal,
      });

      if (response.ok) {
        this.readSseStream(response);
      } else {
        this.scheduleReconnect(++retryCount);
      }
    } catch (error: any) {
      if (error.name !== 'AbortError') {
        console.warn('SSE connection failed:', error.message);
        this.scheduleReconnect(++retryCount);
      }
    }
  }

  // 读取 SSE 流
  async readSseStream(response: Response) {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || ''; // 保留不完整的最后一条

        for (const event of events) {
          this.parseSseEvent(event);
        }
      }
    } catch (error: any) {
      if (error.name !== 'AbortError') {
        console.error('SSE stream error:', error);
      }
    }

    // 连接断开，触发重连
    this.scheduleReconnect(0);
  }

  // 解析 SSE 事件
  private parseSseEvent(raw: string) {
    const lines = raw.split('\n');
    let eventType = '';
    let data = '';

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        data = line.slice(6).trim();
      }
    }

    if (data) {
      try {
        const parsed = JSON.parse(data);
        this.handleEvent(eventType, parsed);
      } catch {
        // 非 JSON 数据 (如纯文本 chunk)
        this.handleEvent(eventType, data);
      }
    }
  }

  // 处理事件
  private handleEvent(type: string, data: any) {
    switch (type) {
      case 'agent_message_chunk':
        // 追加到当前 assistant 消息
        onMessageChunk(data);
        break;
      case 'agent_thought_chunk':
        // 追加到 thinking 区域
        onThoughtChunk(data);
        break;
      case 'available_commands_update':
        // 更新可用命令列表
        onCommandsUpdate(data);
        break;
    }
  }

  // 指数退避重连
  private scheduleReconnect(retry: number) {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }

    // 最大 10 次，60 秒封顶
    if (retry < 10) {
      const delay = Math.min(2000 * Math.pow(2, retry), 60000);
      this.reconnectTimer = setTimeout(() => {
        this.tryEstablishGetSse(retry);
      }, delay);
    }
  }

  // 断开连接
  disconnect() {
    this.abortController?.abort();
    this.abortController = null;

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}
```

### 消息状态管理 (Zustand 风格)

```typescript
interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  thinkingStartTime?: number;
  thinkingDuration?: number;
  timestamp: number;
  streaming: boolean;
  subagentTimeline?: SubMessage[];
}

interface ChatStore {
  timeline: Message[];
  addUserMessage: (content: string) => void;
  addMessageChunk: (chunk: string) => void;
  addThinkingChunk: (chunk: string) => void;
  finalizeAssistantMessage: () => void;
}

// 使用 Zustand 实现
import { create } from 'zustand';

let messageCounter = 0;

const useChatStore = create<ChatStore>((set) => ({
  timeline: [],

  addUserMessage: (content) =>
    set((state) => ({
      timeline: [
        ...state.timeline,
        {
          id: `msg-${++messageCounter}`,
          role: 'user',
          content,
          timestamp: Date.now(),
          streaming: false,
        },
      ],
    })),

  addMessageChunk: (chunk) =>
    set((state) => {
      const timeline = [...state.timeline];
      const lastIdx = timeline.length - 1;
      const last = timeline[lastIdx];

      // 如果最后一条是正在 stream 的 assistant 消息，追加内容
      if (last && last.role === 'assistant' && last.streaming) {
        timeline[lastIdx] = {
          ...last,
          content: last.content + chunk,
        };
      } else {
        // 创建新的 assistant 消息
        timeline.push({
          id: `msg-${++messageCounter}`,
          role: 'assistant',
          content: chunk,
          timestamp: Date.now(),
          streaming: true,
        });
      }

      return { timeline };
    }),

  finalizeAssistantMessage: () =>
    set((state) => ({
      timeline: state.timeline.map((msg) =>
        msg.streaming ? { ...msg, streaming: false } : msg
      ),
    })),
}));
```

---

## CSS 片段精选

### 编辑器布局

```css
/* 编辑器整体布局 */
.editor-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
}

/* 侧栏 (文件树) */
.editor-sidebar {
  width: 260px;
  height: 100%;
  background: var(--editor-sidebar-bg);
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* 主编辑区 */
.editor-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* 标签栏容器 */
.editor-tabs-wrapper {
  display: flex;
  align-items: stretch;
  height: 35px;
  background: var(--editor-tab-bg);
  border-bottom: 1px solid var(--editor-border);
  flex-shrink: 0;
}

/* Tab 标签 */
.editor-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  font-size: 13px;
  color: var(--color-text-tertiary);
  border-right: 1px solid var(--editor-border);
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}

.editor-tab.active {
  background: var(--editor-tab-active-bg);
  color: var(--color-text-primary);
  border-bottom: 2px solid var(--color-text-primary);
}
```

### 文件树悬停交互

```css
/* 缩进辅助线 */
.file-tree-indent-guide {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: transparent;
  pointer-events: none;
}

/* 悬停时显示辅助线 */
.file-tree-content:hover .file-tree-indent-guide {
  background: var(--editor-border);
}

.file-tree-item:hover > .file-tree-indent-guide:last-of-type {
  background: var(--color-text-tertiary);
}

/* 选中状态 */
.file-tree-item.selected {
  background: var(--editor-selection);
}

/* 聚焦时的蓝色轮廓 */
.file-tree:focus .file-tree-item.selected {
  outline: 1px solid var(--color-accent-blue);
  outline-offset: -1px;
}
```

### 暗色主题变量

```css
[data-theme='dark'] {
  --editor-sidebar-bg: #191a1b;
  --editor-bg: #121314;
  --editor-border: #3f4044;
  --editor-hover: #252526;
  --editor-selection: #2a2b2c;
  --editor-tab-bg: #1e1f22;
  --editor-tab-active-bg: #121314;
  --editor-titlebar-bg: #1e1f22;
  --editor-section-bg: #1e1f22;
  --editor-section-hover: #252526;
  --editor-input-bg: #313131;
  --color-text-primary: #f5f5f6;
  --color-text-secondary: #a0a0a8;
  --color-text-tertiary: #6a6a72;
  --color-bg-primary: #121314;
  --color-bg-secondary: #1e1f22;
  --color-bg-tertiary: #242526;
  --color-bg-hover: #2d2e30;
  --color-border-default: #3f4044;
  --color-border-muted: #2d2e30;
  --color-accent-blue: #4a9eff;
  --color-focus-ring: #4a9eff;
  --color-scrollbar: #424242;
  --color-scrollbar-hover: #525252;
}
```

---

## 推荐的复刻路线

### Phase 1: 基础框架 (1-2 天)
- [ ] Vite + React 18 + TypeScript 项目初始化
- [ ] Tailwind CSS v4 配置
- [ ] 暗色/亮色主题 CSS 变量
- [ ] Sidebar 导航组件

### Phase 2: Monaco 编辑器集成 (1-2 天)
- [ ] `@monaco-editor/loader` 引入
- [ ] 基础编辑器组件 (CodeEditor)
- [ ] Tab 多文件管理
- [ ] 文件树 (FileTree 组件)

### Phase 3: 流式消息 (1 天)
- [ ] SSE Client 实现
- [ ] Zustand 状态管理
- [ ] 消息渲染 (Markdown + 代码高亮)
- [ ] Thinking 折叠区域

### Phase 4: 高级功能 (按需)
- [ ] Diff 编辑器
- [ ] Terminal (xterm.js)
- [ ] Canvas 画布布局
- [ ] PWA + Service Worker
