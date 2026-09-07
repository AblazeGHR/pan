import { create } from 'zustand';
import type { Monaco } from '@monaco-editor/react';
import type { FileNode } from '@/types';
import { listFiles, readFile, writeFile, renameFs, deleteFs } from '@/services/api';
import { useUIStore } from '@/stores/uiStore';

// Module-level ref for Monaco model disposal on tab close.
let monacoRef: Monaco | null = null;
export function setMonacoRef(m: Monaco) {
  monacoRef = m;
}

// File mutations for the same session/root must reach the filesystem in the
// order in which the store actions were called. This covers save, rename, and
// delete together: a save of the old path can never finish after a subsequent
// rename/delete has committed and recreate that old path.
const fileOperationQueues = new Map<string, Promise<void>>();

// A path mutation invalidates saves which are submitted after that mutation,
// even when those saves are already waiting behind an in-flight operation.
// This is deliberately separate from the open-read generations: a save must
// not recreate a source path after rename/delete has committed.
const invalidatedFilePaths = new Map<string, Set<string>>();

function enqueueFileOperation(key: string, operation: () => Promise<void>): Promise<void> {
  const previous = fileOperationQueues.get(key) ?? Promise.resolve();
  const current = previous.then(operation, operation);
  fileOperationQueues.set(key, current);
  void current.then(
    () => {
      if (fileOperationQueues.get(key) === current) fileOperationQueues.delete(key);
    },
    () => {
      if (fileOperationQueues.get(key) === current) fileOperationQueues.delete(key);
    },
  );
  return current;
}

function invalidateFilePath(root: RootSnapshot, path: string): void {
  const paths = invalidatedFilePaths.get(rootKey(root)) ?? new Set<string>();
  paths.add(path);
  invalidatedFilePaths.set(rootKey(root), paths);
}

function clearFilePathInvalidation(root: RootSnapshot, path: string): void {
  const paths = invalidatedFilePaths.get(rootKey(root));
  if (!paths) return;
  paths.delete(path);
  if (paths.size === 0) invalidatedFilePaths.delete(rootKey(root));
}

function isFilePathInvalidated(root: RootSnapshot, path: string): boolean {
  return invalidatedFilePaths.get(rootKey(root))?.has(path) ?? false;
}

function operationErrorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '未知错误';
}

const openInvalidationGenerations = new Map<string, number>();

// Vitest resets the Zustand state between cases, but these module-level
// guards intentionally outlive that state in the browser. Keep a small reset
// hook for store tests so one synthetic root cannot affect another case.
export function resetEditorStoreOperationState(): void {
  fileOperationQueues.clear();
  invalidatedFilePaths.clear();
  openInvalidationGenerations.clear();
}

function rootKey(root: RootSnapshot): string {
  return [root.sessionId, root.workdir ?? ''].join('\u0000');
}

function openInvalidationKey(root: RootSnapshot, path: string): string {
  return `${rootKey(root)}\u0000${path}`;
}

function currentOpenInvalidation(root: RootSnapshot, path: string): number {
  return openInvalidationGenerations.get(openInvalidationKey(root, path)) ?? 0;
}

function invalidateOpen(root: RootSnapshot, path: string): void {
  const key = openInvalidationKey(root, path);
  openInvalidationGenerations.set(key, currentOpenInvalidation(root, path) + 1);
}

function disposeMonacoModels(paths: string[]) {
  if (!monacoRef) return;
  for (const path of paths) {
    try {
      const uri = monacoRef.Uri.parse(path);
      const model = monacoRef.editor.getModel(uri);
      if (model && !model.isDisposed()) model.dispose();
    } catch {
      // ignore
    }
  }
}

interface EditorStore {
  // state
  sessionId: string | null;
  workdir: string | null;
  rootGeneration: number;
  /** Monotonic latest-request-wins sequence for tree/list responses. */
  treeRequestGeneration: number;
  /** Monotonic latest-open-wins sequence for file content responses. */
  openRequestGeneration: number;
  tree: FileNode[];
  treeLoading: boolean;
  expanded: Set<string>;
  selectedPath: string | null;
  openPaths: string[];
  activePath: string | null;
  dirty: Set<string>;
  contents: Record<string, string>;
  mdViewMode: Record<string, 'edit' | 'preview' | 'split'>;
  pendingConfirmation: EditorConfirmationRequest | null;

  // actions
  setRoot: (sessionId: string, workdir: string) => Promise<void>;
  refreshTree: (dirPath?: string) => Promise<void>;
  toggleDir: (path: string) => Promise<void>;
  openFile: (path: string) => Promise<void>;
  closeFile: (path: string) => void;
  setActive: (path: string) => void;
  requestSave: (repath?: string) => void;
  requestDelete: (path: string) => void;
  confirmPendingOperation: () => Promise<void>;
  cancelPendingOperation: () => void;
  markDirty: (path: string, content: string) => void;
  saveFile: (repath?: string) => Promise<void>;
  renameFile: (from: string, to: string) => Promise<void>;
  deleteFile: (path: string) => Promise<void>;
  downloadFile: (path: string) => void;
  setMdViewMode: (path: string, mode: 'edit' | 'preview' | 'split') => void;
}

interface RootSnapshot {
  sessionId: string;
  workdir: string | null;
  generation: number;
}

export interface EditorConfirmationRequest {
  kind: 'save' | 'delete';
  path: string;
  sessionId: string;
  workdir: string | null;
  rootGeneration: number;
}

interface TreeRequestSnapshot extends RootSnapshot {
  requestGeneration: number;
}

interface OpenFileSnapshot extends RootSnapshot {
  requestGeneration: number;
  path: string;
  invalidationGeneration: number;
}

function captureRoot(
  state: Pick<EditorStore, 'sessionId' | 'workdir' | 'rootGeneration'>,
): RootSnapshot | null {
  if (!state.sessionId) return null;
  return {
    sessionId: state.sessionId,
    workdir: state.workdir,
    generation: state.rootGeneration,
  };
}

function isCurrentRoot(
  state: Pick<EditorStore, 'sessionId' | 'workdir' | 'rootGeneration'>,
  snapshot: RootSnapshot | null,
): boolean {
  return Boolean(
    snapshot &&
    state.sessionId === snapshot.sessionId &&
    state.workdir === snapshot.workdir &&
    state.rootGeneration === snapshot.generation,
  );
}

function isCurrentTreeRequest(
  state: Pick<EditorStore, 'sessionId' | 'workdir' | 'rootGeneration' | 'treeRequestGeneration'>,
  snapshot: TreeRequestSnapshot,
): boolean {
  return isCurrentRoot(state, snapshot) && state.treeRequestGeneration === snapshot.requestGeneration;
}

function isCurrentOpenRequest(
  state: Pick<EditorStore, 'sessionId' | 'workdir' | 'rootGeneration' | 'openRequestGeneration'>,
  snapshot: OpenFileSnapshot,
): boolean {
  return (
    isCurrentRoot(state, snapshot) &&
    state.openRequestGeneration === snapshot.requestGeneration &&
    currentOpenInvalidation(snapshot, snapshot.path) === snapshot.invalidationGeneration
  );
}

// Language detection from file extension
function languageFromPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase();
  if (!ext) return 'plaintext';
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    py: 'python', rs: 'rust', go: 'go', java: 'java', cpp: 'cpp', c: 'c',
    h: 'c', hpp: 'cpp', cs: 'csharp', rb: 'ruby', php: 'php',
    html: 'html', css: 'css', scss: 'scss', less: 'less',
    json: 'json', xml: 'xml', yaml: 'yaml', yml: 'yaml',
    md: 'markdown', sql: 'sql', sh: 'shell', bash: 'shell', bat: 'bat',
    toml: 'ini', ini: 'ini', dockerfile: 'dockerfile',
  };
  return map[ext] || 'plaintext';
}

// Recursively build tree nodes from flat entries
async function fetchTree(
  sessionId: string,
  dirPath: string,
  prefix: string,
): Promise<FileNode[]> {
  const entries = await listFiles(sessionId, dirPath);
  const nodes: FileNode[] = [];
  for (const e of entries) {
    const fullPath = prefix ? `${prefix}/${e.name}` : e.name;
    const node: FileNode = {
      ...e,
      path: fullPath,
      children: undefined,
      expanded: false,
    };
    if (e.type === 'dir') {
      node.children = [];
    }
    nodes.push(node);
  }
  return nodes;
}

export const useEditorStore = create<EditorStore>((set, get) => ({
  sessionId: null,
  workdir: null,
  rootGeneration: 0,
  treeRequestGeneration: 0,
  openRequestGeneration: 0,
  tree: [],
  treeLoading: false,
  expanded: new Set(),
  selectedPath: null,
  openPaths: [],
  activePath: null,
  dirty: new Set(),
  contents: {},
  mdViewMode: {},
  pendingConfirmation: null,

  setRoot: async (sessionId: string, workdir: string) => {
    const previous = get();
    const rootChanged = previous.sessionId !== sessionId || previous.workdir !== workdir;
    const rootGeneration = rootChanged ? previous.rootGeneration + 1 : previous.rootGeneration;
    const requestGeneration = previous.treeRequestGeneration + 1;
    const snapshot: TreeRequestSnapshot = {
      sessionId,
      workdir,
      generation: rootGeneration,
      requestGeneration,
    };

    if (rootChanged) disposeMonacoModels(previous.openPaths);

    set({
      sessionId,
      workdir,
      rootGeneration,
      treeRequestGeneration: requestGeneration,
      openRequestGeneration: previous.openRequestGeneration + (rootChanged ? 1 : 0),
      treeLoading: true,
      tree: [],
      expanded: new Set(),
      selectedPath: null,
      ...(rootChanged
        ? {
            openPaths: [],
            activePath: null,
            dirty: new Set<string>(),
            contents: {},
            mdViewMode: {},
            pendingConfirmation: null,
          }
        : {}),
    });
    try {
      const rootNodes = await fetchTree(sessionId, '', '');
      if (!isCurrentTreeRequest(get(), snapshot)) return;
      set({ tree: rootNodes, treeLoading: false });
    } catch {
      if (isCurrentTreeRequest(get(), snapshot)) {
        set({ treeLoading: false });
      }
    }
  },

  refreshTree: async (dirPath?: string) => {
    const previous = get();
    const root = captureRoot(previous);
    if (!root) return;
    const snapshot: TreeRequestSnapshot = {
      ...root,
      requestGeneration: previous.treeRequestGeneration + 1,
    };
    set({ treeRequestGeneration: snapshot.requestGeneration, treeLoading: true });
    try {
      const nodes = await fetchTree(snapshot.sessionId, dirPath || '', dirPath || '');
      if (!isCurrentTreeRequest(get(), snapshot)) return;
      set((s) => {
        if (!isCurrentTreeRequest(s, snapshot)) return {};
        if (!dirPath) {
          return { tree: nodes, treeLoading: false };
        }
        // Replace only the subtree under dirPath
        function replaceInTree(t: FileNode[]): FileNode[] {
          return t.map((n) => {
            if (n.path === dirPath) {
              return { ...n, children: nodes };
            }
            if (n.children) {
              return { ...n, children: replaceInTree(n.children) };
            }
            return n;
          });
        }
        return { tree: replaceInTree(s.tree), treeLoading: false };
      });
    } catch {
      if (isCurrentTreeRequest(get(), snapshot)) set({ treeLoading: false });
    }
  },

  toggleDir: async (path: string) => {
    const { expanded, tree } = get();

    const isExpanded = expanded.has(path);

    if (!isExpanded) {
      // Expand — lazy load children if empty
      const node = findNode(tree, path);
      if (!node || node.type !== 'dir') return;
      if (node && node.children && node.children.length === 0) {
        const previous = get();
        const root = captureRoot(previous);
        if (!root) return;
        const snapshot: TreeRequestSnapshot = {
          ...root,
          requestGeneration: previous.treeRequestGeneration + 1,
        };
        set({ treeRequestGeneration: snapshot.requestGeneration, treeLoading: true });
        try {
          const children = await fetchTree(snapshot.sessionId, path, path);
          if (!isCurrentTreeRequest(get(), snapshot)) return;
          set((s) => {
            if (!isCurrentTreeRequest(s, snapshot)) return {};
            return {
              tree: replaceNode(s.tree, path, { children }),
              expanded: new Set([...s.expanded, path]),
              treeLoading: false,
            };
          });
          return;
        } catch {
          if (isCurrentTreeRequest(get(), snapshot)) {
            set({ treeLoading: false });
          }
          // Keep the directory collapsed on error.
          return;
        }
      }
    }

    set((s) => {
      const next = new Set(s.expanded);
      if (isExpanded) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return { expanded: next };
    });
  },

  openFile: async (path: string) => {
    const state = get();
    const root = captureRoot(state);
    if (!root) return;
    const snapshot: OpenFileSnapshot = {
      ...root,
      requestGeneration: state.openRequestGeneration + 1,
      path,
      invalidationGeneration: currentOpenInvalidation(root, path),
    };

    set({ selectedPath: path, openRequestGeneration: snapshot.requestGeneration });

    // Already open — just switch active
    if (get().openPaths.includes(path)) {
      set({ activePath: path });
      return;
    }

    // Fetch content
    try {
      const content = await readFile(snapshot.sessionId, path);
      if (!isCurrentOpenRequest(get(), snapshot)) return;
      clearFilePathInvalidation(snapshot, path);
      set((s) => {
        if (!isCurrentOpenRequest(s, snapshot)) return {};
        return {
          openPaths: s.openPaths.includes(path) ? s.openPaths : [...s.openPaths, path],
          activePath: path,
          contents: { ...s.contents, [path]: content },
        };
      });
    } catch (error) {
      if (isCurrentOpenRequest(get(), snapshot)) {
        useUIStore.getState().showToast(`打开文件失败：${operationErrorMessage(error)}`, 'error');
      }
    }
  },

  closeFile: (path: string) => {
    const root = captureRoot(get());
    if (root) invalidateOpen(root, path);
    set((s) => {
      const newOpen = s.openPaths.filter((p) => p !== path);
      const newDirty = new Set(s.dirty);
      newDirty.delete(path);
      const newContents = { ...s.contents };
      delete newContents[path];
      let newActive = s.activePath;
      if (s.activePath === path) {
        // Activate nearest tab
        const idx = s.openPaths.indexOf(path);
        if (newOpen.length > 0) {
          newActive = newOpen[Math.min(idx, newOpen.length - 1)] ?? null;
        } else {
          newActive = null;
        }
      }
      // Dispose Monaco model
      disposeMonacoModels([path]);
      return {
        openPaths: newOpen,
        dirty: newDirty,
        contents: newContents,
        activePath: newActive,
        selectedPath: s.selectedPath === path ? newActive : s.selectedPath,
        pendingConfirmation: s.pendingConfirmation?.path === path ? null : s.pendingConfirmation,
      };
    });
  },

  setActive: (path: string) => {
    // Selecting an already-open tab supersedes any deferred read for a
    // different path. Without this generation bump, that old read could
    // later steal active/selected state and re-add its tab.
    set((s) => ({
      activePath: path,
      selectedPath: path,
      openRequestGeneration: s.openRequestGeneration + 1,
    }));
  },

  requestSave: (repath?: string) => {
    const state = get();
    const root = captureRoot(state);
    const path = repath || state.activePath;
    if (!root || !path || state.contents[path] === undefined) return;
    set({
      pendingConfirmation: {
        kind: 'save',
        path,
        sessionId: root.sessionId,
        workdir: root.workdir,
        rootGeneration: root.generation,
      },
    });
  },

  requestDelete: (path: string) => {
    const root = captureRoot(get());
    if (!root || !path) return;
    set({
      pendingConfirmation: {
        kind: 'delete',
        path,
        sessionId: root.sessionId,
        workdir: root.workdir,
        rootGeneration: root.generation,
      },
    });
  },

  confirmPendingOperation: async () => {
    const pending = get().pendingConfirmation;
    if (!pending) return;
    set({ pendingConfirmation: null });

    const current = captureRoot(get());
    if (
      !current ||
      current.sessionId !== pending.sessionId ||
      current.workdir !== pending.workdir ||
      current.generation !== pending.rootGeneration
    ) {
      useUIStore.getState().showToast('文件上下文已变化，操作已取消', 'error');
      return;
    }

    if (pending.kind === 'save') {
      await get().saveFile(pending.path);
    } else {
      await get().deleteFile(pending.path);
    }
  },

  cancelPendingOperation: () => {
    set({ pendingConfirmation: null });
  },

  markDirty: (path: string, content: string) => {
    set((s) => ({
      dirty: new Set([...s.dirty, path]),
      contents: { ...s.contents, [path]: content },
    }));
  },

  saveFile: async (repath?: string) => {
    const snapshot = captureRoot(get());
    if (!snapshot) return;
    const { activePath, contents } = get();
    const path = repath || activePath;
    if (!path) return;

    const content = contents[path];
    if (content === undefined) return;

    try {
      await enqueueFileOperation(rootKey(snapshot), async () => {
        // A rename/delete requested before this save may still be in flight.
        // Do not write the captured old path after that mutation completes.
        // Re-check the root at execution time as well, because a queued save
        // must not write into a root that the user has already left.
        if (!isCurrentRoot(get(), snapshot) || isFilePathInvalidated(snapshot, path)) return;
        await writeFile(snapshot.sessionId, path, content);
        if (!isCurrentRoot(get(), snapshot)) return;
        set((s) => {
          // Do not clear a newer draft created while the write was in flight.
          if (!isCurrentRoot(s, snapshot) || s.contents[path] !== content) return {};
          const next = new Set(s.dirty);
          next.delete(path);
          return { dirty: next };
        });
      });
    } catch (error) {
      if (isCurrentRoot(get(), snapshot)) {
        useUIStore.getState().showToast(`保存文件失败：${operationErrorMessage(error)}`, 'error');
      }
    }
  },

  renameFile: async (from: string, to: string) => {
    const snapshot = captureRoot(get());
    if (!snapshot) return;

    const current = get();
    const targetHasEditorState =
      current.openPaths.includes(to) ||
      current.dirty.has(to) ||
      Object.prototype.hasOwnProperty.call(current.contents, to) ||
      Object.prototype.hasOwnProperty.call(current.mdViewMode, to) ||
      current.activePath === to ||
      current.selectedPath === to;
    if (from !== to && targetHasEditorState) {
      useUIStore.getState().showToast(
        `无法重命名：目标路径已有打开或未保存的编辑器状态（${to}）`,
        'error',
      );
      return;
    }

    // Also invalidate same-path rename requests. A missing source must fail
    // closed and must not be followed by a deferred save that recreates it;
    // a successful existing-source no-op clears this marker before later
    // queued saves run.
    invalidateFilePath(snapshot, from);

    try {
      await enqueueFileOperation(rootKey(snapshot), async () => {
        await renameFs(snapshot.sessionId, from, to);
        if (!isCurrentRoot(get(), snapshot)) return;
        clearFilePathInvalidation(snapshot, to);
        invalidateOpen(snapshot, from);
        set((s) => {
          if (!isCurrentRoot(s, snapshot)) return {};

          // Re-read state after the await so concurrent tabs and drafts survive.
          const openPaths = s.openPaths
            .map((path) => (path === from ? to : path))
            .filter((path, index, paths) => paths.indexOf(path) === index);
          const dirty = new Set(s.dirty);
          if (dirty.delete(from)) dirty.add(to);

          const contents = { ...s.contents };
          if (Object.prototype.hasOwnProperty.call(contents, from)) {
            contents[to] = contents[from]!;
            delete contents[from];
          }

          const mdViewMode = { ...s.mdViewMode };
          if (Object.prototype.hasOwnProperty.call(mdViewMode, from)) {
            mdViewMode[to] = mdViewMode[from]!;
            delete mdViewMode[from];
          }

          return {
            openPaths,
            activePath: s.activePath === from ? to : s.activePath,
            selectedPath: s.selectedPath === from ? to : s.selectedPath,
            dirty,
            contents,
            mdViewMode,
          };
        });
        // Refresh parent dir
        const parentPath = from.includes('/') ? from.substring(0, from.lastIndexOf('/')) : '';
        void get().refreshTree(parentPath);
      });
    } catch (error) {
      useUIStore.getState().showToast(
        `重命名失败：${operationErrorMessage(error)}`,
        'error',
      );
    }
  },

  deleteFile: async (path: string) => {
    const snapshot = captureRoot(get());
    if (!snapshot) return;

    invalidateFilePath(snapshot, path);

    try {
      await enqueueFileOperation(rootKey(snapshot), async () => {
        await deleteFs(snapshot.sessionId, path);
        if (!isCurrentRoot(get(), snapshot)) return;
        invalidateOpen(snapshot, path);
        // Close if open
        get().closeFile(path);
        // Refresh parent dir
        const parentPath = path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : '';
        void get().refreshTree(parentPath);
      });
    } catch (error) {
      if (isCurrentRoot(get(), snapshot)) {
        useUIStore.getState().showToast(`删除文件失败：${operationErrorMessage(error)}`, 'error');
      }
    }
  },

  downloadFile: (path: string) => {
    const { sessionId } = get();
    if (!sessionId) return;

    // Attachment download via backend (binary-safe, no size cap).
    // Content-Disposition from server supplies the original filename.
    const params = new URLSearchParams({ session_id: sessionId, path });
    const a = document.createElement('a');
    a.href = `/api/fs/read?${params.toString()}&download=1`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  },

  setMdViewMode: (path, mode) => {
    set((s) => ({
      mdViewMode: { ...s.mdViewMode, [path]: mode },
    }));
  },
}));

// Tree helpers

function findNode(nodes: FileNode[], path: string): FileNode | null {
  for (const n of nodes) {
    if (n.path === path) return n;
    if (n.children) {
      const found = findNode(n.children, path);
      if (found) return found;
    }
  }
  return null;
}

function replaceNode(nodes: FileNode[], path: string, patch: Partial<FileNode>): FileNode[] {
  return nodes.map((n) => {
    if (n.path === path) {
      return { ...n, ...patch };
    }
    if (n.children) {
      return { ...n, children: replaceNode(n.children, path, patch) };
    }
    return n;
  });
}

export { languageFromPath };
