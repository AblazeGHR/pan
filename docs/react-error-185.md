# React #185 诊断文档

> 最后更新：2026-07-27

## 问题描述

在 React 前端（`packages/web/src/`）中，加载消息量 >50 条的 session 时，浏览器抛出：

**Minified React error #185** — "Maximum update depth exceeded"（嵌套状态同步更新超过上限，约 50 层）。

- 短 session（≤50 条消息）**不触发**。
- 长 session（>50 条消息，`historyTruncated=true`）**稳定触发**。
- 触发时机：点击 session → `selectSession` 完成后的消息渲染阶段。
- 不依赖编辑器组件（EditorView/Comments/Store 均无关）。

## 根因定位现状

**尚未定位到的根因。** 5 轮修复消除了已知缺陷，但 #185 仍然存在。

## 关键数据流

```
用户点击 session
  │
  ▼
SessionList.onSelect → selectSession(id)
  ├─ 保存 input draft
  ├─ 找到 session 对象
  └─ set({ currentSessionId, currentMessages (50条), historyLoading: true })
     │
     ▼  [1次同步渲染 — 但 React 可能检测到多个内部更新]
     │
     ├─ ChatMessages 重渲染
     │   ├─ virtualizer count 从 0 变为 N
     │   ├─ measureElement ref 回调被 React 触发
     │   └─ useEffect: scrollToBottom() (line 79)
     │       └─ 设置 scrollTop → 可能触发 scroll 事件
     │
     └─ loadOlderMessages() [自动，因为 historyTruncated=true]
        │
        ├─ ...async fetch 50条历史...
        │
        └─ set({ sessions (新对象), currentMessages (100条), historyLoading: false })
           │
           ▼  [1次同步渲染 — 但可能 + virtualizer 内部多次]
           │
           ├─ virtualizer count 再次变化
           ├─ virtualizer 测量 → 内部 size 缓存更新 → React 再次渲染
           │
           └─ 如果 hasMoreMessages=true:
              scroll → 150ms延迟 → loadOlderMessages → 回到上一步
```

## 核心怀疑

**@tanstack/react-virtual 的 `useVirtualizer` 在 count 频繁变化时触发 sysnc measure → render → measure 循环。**

当前 `ChatMessages.tsx:18-23` 的配置：

```tsx
const virtualizer = useVirtualizer({
  count: grouped.length,
  getScrollElement: () => parentRef.current,
  estimateSize: () => 100,
  overscan: 5,
});
```

已知事实：
- `count` 在 session 加载期间从 0 → 50 → 100 → 150 → ... 每次 +50。
- 每次 count 变化，virtualizer 重算内部 item 列表。
- `measureElement` ref（line 115）在 React commit 阶段被调用，virtualizer 从此回调中更新尺寸缓存。
- 尺寸缓存更新可能触发 internalState 变化，通过 `useSyncExternalStore` 推送 React 重渲染。
- 在长 session 的连续加载中，这些步骤被**压缩在同一个 timer tick 内**，可能超过 React 的嵌套深度限制。

## 历次修复尝试

### R1 — 消除冗余重渲染（2026-07-27）

**改动**：
- `ChatMessages.tsx`: `useSessionStore()` → 独立 selectors
- `ChatMessages.tsx`: `useEffect` 加 `[currentMessages]` 依赖数组
- `ChatMessages.tsx`: 滚动恢复用 `requestAnimationFrame`
- `InputRow.tsx`: `useSessionStore()` → 独立 selectors

**结果**：未解决。

### R2 — 修复 sessionStore 的 subscribe 循环（2026-07-27）

**改动**：
- `sessionStore.ts`: 订阅 `subscribe`+`setState` → 纯 selector `useCurrentSession()` hook
- 更新 4 个消费者（TopBar, EditorView, SettingsPanel, InputRow）

**结果**：未解决。

### R3 — 封堵竞争窗口 + 移除 contain:strict（2026-07-27）

**改动**：
- `sessionStore.ts`: `selectSession` 两次 `set()` 合并为一次
- `sessionStore.ts`: 提前设 `historyLoading: true` 阻止 scroll handler
- `ChatMessages.tsx`: 移除 `contain: strict`（与 virtualizer 测量冲突）
- `useSessionHistory.ts`: 删除死代码

**结果**：未解决。

### R4 — SessionItem 点击修复（2026-07-27）

**改动**：
- `SessionItem.tsx`: 修复非多选模式点击不触发

**结果**：不相关（只是让 session 点击能用，与 #185 无关）。

## 排除项

以下被确认**不是**原因：
- `useSessionStore()` 全量订阅（R1 已修复）
- `useEffect` 无 deps（R1 已修复）
- subscribe + setState 嵌套（R2 已修复）
- `contain: strict` CSS（R3 已移除）
- `useSessionHistory.ts`（R3 已删除，本是死代码）
- `selectSession` 两次 set()（R3 已合并）
- 编辑器组件（EditorView, editorStore 等）— 短 session 不触发，说明聊天视图即可触发

## 可能的下一步方向

1. **消除 virtualizer count 变化** — 在 session 加载期间不更新 `currentMessages`，等所有历史消息合并后再一次性 set。避免 count 从 0 → 50 → 100 → ... 的多次变化。

2. **消除 virtualizer 的 measureElement 在加载期间的触发** — 用 `enable: false` 或类似 feature 暂停虚拟化测量直到加载完成。检查 @tanstack/react-virtual 文档中是否有 `enabled` prop。

3. **在 dev 模式下运行得到完整错误栈** — `vite dev` 运行后，React 会给出完整的调用栈而非 minified 错误，可以直接看到是哪个组件陷入了循环。

4. **临时绕过** — 在长 session 加载时不渲染 virtualizer，先用 "Loading..." 占位，等历史排序全部加载完再显示。

## 相关文件

| 路径 | 角色 |
|---|---|
| `src/components/chat/ChatMessages.tsx` | 聊天消息渲染 + virtualizer |
| `src/stores/sessionStore.ts` | 状态管理 + selectSession/loadOlderMessages |
| `src/components/chat/MessageBubble.tsx` | 消息项渲染（virtual item 内部） |
| `src/hooks/useWebSocket.ts` | WS 连接（可能触发额外 loadSessions） |

## 环境

- React 19 + react-router-dom 7 + zustand 5 + @tanstack/react-virtual 3
- Vite 6 构建，生产模式
