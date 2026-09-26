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

// File mutations for the same session must reach the filesystem in the order
// the store actions were called. This covers save, rename, and delete together:
// a save of the old path can never finish after a subsequent rename/delete has
// committed and recreate that old path. Paths are absolute (each editor root
// contributes its own subtree), so a single per-session queue is enough.
const fileOperationQueues = new Map<string, Promise<void>>();

// A path mutation invalidates saves submitted after that mutation, even when
// those saves are already waiting behind an in-flight operation. This is
// deliberately separate from the open-read generations: a save must not
// recreate a source path after rename/delete has committed.
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

function invalidateFilePath(sessionId: string, path: string): void {
  const paths = invalidatedFilePaths.get(sessionId) ?? new Set<string>();
  paths.add(path);
  invalidatedFilePaths.set(sessionId, paths);
}

function clearFilePathInvalidation(sessionId: string, path: string): void {
  const paths = invalidatedFilePaths.get(sessionId);
  if (!paths) return;
  paths.delete(path);
  if (paths.size === 0) invalidatedFilePaths.delete(sessionId);
}

function isFilePathInvalidated(sessionId: string, path: string): boolean {
  return invalidatedFilePaths.get(sessionId)?.has(path) ?? false;
}

function operationErrorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '未知错误';
}

function openFileErrorMessage(error: unknown): string {
  const message = operationErrorMessage(error);
  if (/not a file|not found|no such file|does not exist|enoent|http 404/i.test(message)) {
    return `文件不存在${message ? `：${message}` : ''}`;
  }
  return message || '无法打开文件';
}

const openInvalidationGenerations = new Map<string, number>();

function openInvalidationKey(sessionId: string, path: string): string {
  return `${sessionId}\u0000${path}`;
}

function currentOpenInvalidation(sessionId: string, path: string): number {
  return openInvalidationGenerations.get(openInvalidationKey(sessionId, path)) ?? 0;
}

function invalidateOpen(sessionId: string, path: string): void {
  const key = openInvalidationKey(sessionId, path);
  openInvalidationGenerations.set(key, currentOpenInvalidation(sessionId, path) + 1);
}

// Vitest resets the Zustand state between cases, but these module-level
// guards intentionally outlive that state in the browser. Keep a small reset
// hook for store tests so one synthetic session cannot affect another case.
export function resetEditorStoreOperationState(): void {
  fileOperationQueues.clear();
  invalidatedFilePaths.clear();
  openInvalidationGenerations.clear();
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

/** Root kinds the editor can browse for one Session. */
export type EditorRootKind = 'cwd' | 'workspace' | 'temp';

/**
 * One directory root shown in the Editor sidebar. Roots are keyed by
 * `${kind}:${path}`, so overlapping paths across kinds stay distinct and never
 * share tree/expansion state.
 */
export interface EditorRoot {
  /** Stable identity: `${kind}:${normalizedPath}`. */
  id: string;
  kind: EditorRootKind;
  /** Absolute server path, forward-slash normalized. */
  path: string;
  /** Display name (basename, or the full path when it has none). */
  label: string;
  /** Owning Workspace id, for `kind === 'workspace'`. */
  workspaceId?: string;
}

interface EditorRootTree {
  nodes: FileNode[];
  loading: boolean;
}

/** Shared empty reference so selectors can avoid new array identities. */
export const EMPTY_FILE_NODES: FileNode[] = [];

/** Composite expansion key so the same absolute dir in two roots expands apart. */
export function expansionKey(rootId: string, path: string): string {
  return `${rootId}\u0000${path}`;
}

/** Normalize a server path to forward slashes without trimming a bare root. */
export function normalizeEditorPath(raw: string): string {
  let path = raw.trim().replace(/\\/g, '/');
  path = path.replace(/\/+$/, '');
  if (/^[A-Za-z]:$/.test(path)) path += '/';
  if (path === '') path = '/';
  return path;
}

function pathBasename(path: string): string {
  const parts = path.split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1]! : path;
}

function joinChildPath(prefix: string, name: string): string {
  if (!prefix) return name;
  return prefix.endsWith('/') ? `${prefix}${name}` : `${prefix}/${name}`;
}

function buildRoots(
  workdir: string | null,
  workspaceId: string | null,
  workspaceDirs: string[],
  tempDirs: string[],
): EditorRoot[] {
  const roots: EditorRoot[] = [];
  const seen = new Set<string>();
  const push = (kind: EditorRootKind, raw: string, owner?: string | null) => {
    const path = normalizeEditorPath(raw);
    const id = `${kind}:${path}`;
    if (seen.has(id)) return;
    seen.add(id);
    roots.push({
      id,
      kind,
      path,
      label: kind === 'cwd' ? 'CWD' : pathBasename(path),
      ...(kind === 'workspace' && owner ? { workspaceId: owner } : {}),
    });
  };
  if (workdir) push('cwd', workdir);
  for (const dir of workspaceDirs) push('workspace', dir, workspaceId);
  for (const dir of tempDirs) push('temp', dir);
  return roots;
}

function reconcileTrees(
  roots: EditorRoot[],
  previous: Record<string, EditorRootTree>,
): Record<string, EditorRootTree> {
  const next: Record<string, EditorRootTree> = {};
  for (const root of roots) {
    next[root.id] = previous[root.id] ?? { nodes: EMPTY_FILE_NODES, loading: false };
  }
  return next;
}

function rootContainsPath(root: EditorRoot, path: string): boolean {
  if (path === root.path) return true;
  const base = root.path.endsWith('/') ? root.path : `${root.path}/`;
  return path.startsWith(base);
}

/** Absolute directory holding `path` inside `root`. */
function parentDirWithinRoot(root: EditorRoot, path: string): string {
  const relative = path === root.path ? '' : path.slice(root.path.length).replace(/^\/+/, '');
  const parentRelative = relative.includes('/')
    ? relative.slice(0, relative.lastIndexOf('/'))
    : '';
  if (!parentRelative) return root.path;
  return `${root.path.replace(/\/+$/, '')}/${parentRelative}`;
}

interface EditorStore {
  // state
  sessionId: string | null;
  workdir: string | null;
  rootGeneration: number;
  /** Directory roots for the current Session (cwd + workspace + temp). */
  roots: EditorRoot[];
  /** Per-root tree state, keyed by `EditorRoot.id`. */
  rootTrees: Record<string, EditorRootTree>;
  /** Per-root latest-request-wins sequence for tree/list responses. */
  rootTreeGenerations: Record<string, number>;
  /** Composite (`rootId\u0000path`) expansion keys. */
  expanded: Set<string>;
  /** Monotonic latest-open-wins sequence for file content responses. */
  openRequestGeneration: number;
  /** Temp dirs are browser memory only: no persistence, shown for every Session. */
  tempDirs: string[];
  /** Effective Workspace of the current Session (null = ungrouped/unknown). */
  workspaceId: string | null;
  /** Shared dirs of the current Session's Workspace. */
  workspaceDirs: string[];
  selectedPath: string | null;
  openPaths: string[];
  activePath: string | null;
  dirty: Set<string>;
  contents: Record<string, string>;
  imagePreviews: Record<string, EditorImagePreviewSource>;
  mdViewMode: Record<string, 'edit' | 'preview' | 'split'>;
  /** A one-shot location request produced by a Markdown file link. */
  pendingLocation: EditorLocation | null;
  pendingConfirmation: EditorConfirmationRequest | null;

  // actions
  setRoot: (sessionId: string, workdir: string | null) => Promise<void>;
  /** Sync the current Session's Workspace shared dirs (persisted elsewhere). */
  setWorkspaceDirs: (workspaceId: string | null, dirs: string[]) => void;
  addTempDir: (path: string) => void;
  removeTempDir: (path: string) => void;
  /** (Re)load one root's tree; `dirPath` refreshes a subtree instead. */
  refreshRoot: (rootId: string, dirPath?: string) => Promise<void>;
  loadRootTree: (rootId: string) => Promise<void>;
  toggleDir: (rootId: string, path: string) => Promise<void>;
  openFile: (path: string, location?: EditorLocation) => Promise<boolean>;
  openImage: (path: string, preview: EditorImagePreviewSource) => boolean;
  consumePendingLocation: (location: EditorLocation) => void;
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

interface SessionSnapshot {
  sessionId: string;
  generation: number;
}

interface RootRequestSnapshot extends SessionSnapshot {
  rootId: string;
  requestGeneration: number;
}

export interface EditorConfirmationRequest {
  kind: 'save' | 'delete';
  path: string;
  sessionId: string;
  rootGeneration: number;
}

export interface EditorLocation {
  path: string;
  line: number;
  endLine?: number;
}

export interface EditorImagePreviewSource {
  src: string;
  displayName: string;
  /** Server-authorized same-origin opaque attachment URL; absent for workdir files. */
  downloadHref?: string;
}

interface OpenFileSnapshot extends SessionSnapshot {
  requestGeneration: number;
  path: string;
  invalidationGeneration: number;
}

const EDITOR_IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif']);

function isEditorImagePath(path: string): boolean {
  return EDITOR_IMAGE_EXTENSIONS.has(path.split('.').pop()?.toLowerCase() ?? '');
}

function captureSession(
  state: Pick<EditorStore, 'sessionId' | 'rootGeneration'>,
): SessionSnapshot | null {
  if (!state.sessionId) return null;
  return { sessionId: state.sessionId, generation: state.rootGeneration };
}

function isCurrentSession(
  state: Pick<EditorStore, 'sessionId' | 'rootGeneration'>,
  snapshot: SessionSnapshot | null,
): boolean {
  return Boolean(
    snapshot &&
    state.sessionId === snapshot.sessionId &&
    state.rootGeneration === snapshot.generation,
  );
}

function isCurrentRootRequest(
  state: Pick<EditorStore, 'sessionId' | 'rootGeneration' | 'roots' | 'rootTreeGenerations'>,
  snapshot: RootRequestSnapshot,
): boolean {
  return (
    isCurrentSession(state, snapshot) &&
    (state.rootTreeGenerations[snapshot.rootId] ?? 0) === snapshot.requestGeneration &&
    state.roots.some((root) => root.id === snapshot.rootId)
  );
}

function isCurrentOpenRequest(
  state: Pick<
    EditorStore,
    'sessionId' | 'rootGeneration' | 'openRequestGeneration'
  >,
  snapshot: OpenFileSnapshot,
): boolean {
  return (
    isCurrentSession(state, snapshot) &&
    state.openRequestGeneration === snapshot.requestGeneration &&
    currentOpenInvalidation(snapshot.sessionId, snapshot.path) === snapshot.invalidationGeneration
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

// Recursively build tree nodes from flat entries.
async function fetchTree(
  sessionId: string,
  dirPath: string,
  prefix: string,
): Promise<FileNode[]> {
  const entries = await listFiles(sessionId, dirPath);
  const nodes: FileNode[] = [];
  for (const e of entries) {
    const fullPath = joinChildPath(prefix, e.name);
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
  roots: [],
  rootTrees: {},
  rootTreeGenerations: {},
  expanded: new Set(),
  openRequestGeneration: 0,
  tempDirs: [],
  workspaceId: null,
  workspaceDirs: [],
  selectedPath: null,
  openPaths: [],
  activePath: null,
  dirty: new Set(),
  contents: {},
  imagePreviews: {},
  mdViewMode: {},
  pendingLocation: null,
  pendingConfirmation: null,

  setRoot: async (sessionId: string, workdir: string | null) => {
    const previous = get();
    const sessionChanged = previous.sessionId !== sessionId;
    const rootChanged = sessionChanged || previous.workdir !== workdir;
    const rootGeneration = rootChanged ? previous.rootGeneration + 1 : previous.rootGeneration;

    // Temp roots are browser memory and intentionally survive Session switches;
    // Workspace dirs are re-supplied by the view for the new Session.
    const workspaceDirs = sessionChanged ? [] : previous.workspaceDirs;
    const workspaceId = sessionChanged ? null : previous.workspaceId;
    const tempDirs = previous.tempDirs;
    const roots = buildRoots(workdir, workspaceId, workspaceDirs, tempDirs);
    const rootTrees = reconcileTrees(roots, sessionChanged ? {} : previous.rootTrees);
    const rootTreeGenerations = sessionChanged ? {} : { ...previous.rootTreeGenerations };

    if (rootChanged) disposeMonacoModels(previous.openPaths);

    set({
      sessionId,
      workdir,
      rootGeneration,
      workspaceId,
      workspaceDirs,
      roots,
      rootTrees,
      rootTreeGenerations,
      openRequestGeneration: previous.openRequestGeneration + (rootChanged ? 1 : 0),
      ...(rootChanged
        ? {
            expanded: new Set<string>(),
            selectedPath: null,
            openPaths: [],
            activePath: null,
            dirty: new Set<string>(),
            contents: {},
            imagePreviews: {},
            mdViewMode: {},
            pendingLocation: null,
            pendingConfirmation: null,
          }
        : {}),
    });

    if (rootChanged) {
      await Promise.all(roots.map((root) => get().loadRootTree(root.id)));
    } else {
      const cwd = roots.find((root) => root.kind === 'cwd');
      if (cwd) await get().loadRootTree(cwd.id);
    }
  },

  setWorkspaceDirs: (workspaceId: string | null, dirs: string[]) => {
    const previous = get();
    const workspaceDirs = dirs.map(normalizeEditorPath);
    const roots = buildRoots(previous.workdir, workspaceId, workspaceDirs, previous.tempDirs);
    const rootTrees = reconcileTrees(roots, previous.rootTrees);
    const newRootIds = roots
      .filter((root) => !previous.roots.some((existing) => existing.id === root.id))
      .map((root) => root.id);
    set({ workspaceId, workspaceDirs, roots, rootTrees });
    for (const rootId of newRootIds) void get().loadRootTree(rootId);
  },

  addTempDir: (path: string) => {
    const previous = get();
    const normalized = normalizeEditorPath(path);
    if (previous.tempDirs.includes(normalized)) return;
    const tempDirs = [...previous.tempDirs, normalized];
    const roots = buildRoots(previous.workdir, previous.workspaceId, previous.workspaceDirs, tempDirs);
    set({ tempDirs, roots, rootTrees: reconcileTrees(roots, previous.rootTrees) });
    const added = roots.find((root) => root.kind === 'temp' && root.path === normalized);
    if (added) void get().loadRootTree(added.id);
  },

  removeTempDir: (path: string) => {
    const previous = get();
    const normalized = normalizeEditorPath(path);
    if (!previous.tempDirs.includes(normalized)) return;
    const tempDirs = previous.tempDirs.filter((dir) => dir !== normalized);
    const roots = buildRoots(previous.workdir, previous.workspaceId, previous.workspaceDirs, tempDirs);
    set({ tempDirs, roots, rootTrees: reconcileTrees(roots, previous.rootTrees) });
  },

  loadRootTree: async (rootId: string) => {
    const state = get();
    if (!state.sessionId) return;
    const root = state.roots.find((item) => item.id === rootId);
    if (!root) return;
    const requestGeneration = (state.rootTreeGenerations[rootId] ?? 0) + 1;
    const snapshot: RootRequestSnapshot = {
      sessionId: state.sessionId,
      generation: state.rootGeneration,
      rootId,
      requestGeneration,
    };
    set((s) => ({
      rootTreeGenerations: { ...s.rootTreeGenerations, [rootId]: requestGeneration },
      rootTrees: {
        ...s.rootTrees,
        [rootId]: { ...(s.rootTrees[rootId] ?? { nodes: EMPTY_FILE_NODES }), loading: true },
      },
    }));
    try {
      const nodes = await fetchTree(snapshot.sessionId, root.path, root.path);
      if (!isCurrentRootRequest(get(), snapshot)) return;
      set((s) => {
        if (!isCurrentRootRequest(s, snapshot)) return {};
        return { rootTrees: { ...s.rootTrees, [rootId]: { nodes, loading: false } } };
      });
    } catch {
      if (isCurrentRootRequest(get(), snapshot)) {
        set((s) => ({
          rootTrees: {
            ...s.rootTrees,
            [rootId]: { ...(s.rootTrees[rootId] ?? { nodes: EMPTY_FILE_NODES }), loading: false },
          },
        }));
      }
    }
  },

  refreshRoot: async (rootId: string, dirPath?: string) => {
    const state = get();
    const root = state.roots.find((item) => item.id === rootId);
    if (!root || !state.sessionId) return;
    if (!dirPath || normalizeEditorPath(dirPath) === root.path) {
      await get().loadRootTree(rootId);
      return;
    }
    const requestGeneration = (state.rootTreeGenerations[rootId] ?? 0) + 1;
    const snapshot: RootRequestSnapshot = {
      sessionId: state.sessionId,
      generation: state.rootGeneration,
      rootId,
      requestGeneration,
    };
    set((s) => ({
      rootTreeGenerations: { ...s.rootTreeGenerations, [rootId]: requestGeneration },
      rootTrees: {
        ...s.rootTrees,
        [rootId]: { ...(s.rootTrees[rootId] ?? { nodes: EMPTY_FILE_NODES }), loading: true },
      },
    }));
    try {
      const nodes = await fetchTree(snapshot.sessionId, dirPath, dirPath);
      if (!isCurrentRootRequest(get(), snapshot)) return;
      set((s) => {
        if (!isCurrentRootRequest(s, snapshot)) return {};
        const tree = s.rootTrees[rootId];
        if (!tree) return {};
        return {
          rootTrees: {
            ...s.rootTrees,
            [rootId]: { nodes: replaceNode(tree.nodes, dirPath, { children: nodes }), loading: false },
          },
        };
      });
    } catch {
      if (isCurrentRootRequest(get(), snapshot)) {
        set((s) => ({
          rootTrees: {
            ...s.rootTrees,
            [rootId]: { ...(s.rootTrees[rootId] ?? { nodes: EMPTY_FILE_NODES }), loading: false },
          },
        }));
      }
    }
  },

  toggleDir: async (rootId: string, path: string) => {
    const key = expansionKey(rootId, path);
    const isExpanded = get().expanded.has(key);

    if (!isExpanded) {
      const state = get();
      const tree = state.rootTrees[rootId]?.nodes ?? EMPTY_FILE_NODES;
      const node = findNode(tree, path);
      if (!node || node.type !== 'dir') return;
      if (node.children && node.children.length === 0) {
        if (!state.sessionId) return;
        const requestGeneration = (state.rootTreeGenerations[rootId] ?? 0) + 1;
        const snapshot: RootRequestSnapshot = {
          sessionId: state.sessionId,
          generation: state.rootGeneration,
          rootId,
          requestGeneration,
        };
        set((s) => ({
          rootTreeGenerations: { ...s.rootTreeGenerations, [rootId]: requestGeneration },
          rootTrees: {
            ...s.rootTrees,
            [rootId]: { ...(s.rootTrees[rootId] ?? { nodes: EMPTY_FILE_NODES }), loading: true },
          },
        }));
        try {
          const children = await fetchTree(snapshot.sessionId, path, path);
          if (!isCurrentRootRequest(get(), snapshot)) return;
          set((s) => {
            if (!isCurrentRootRequest(s, snapshot)) return {};
            const current = s.rootTrees[rootId];
            if (!current) return {};
            return {
              rootTrees: {
                ...s.rootTrees,
                [rootId]: { nodes: replaceNode(current.nodes, path, { children }), loading: false },
              },
              expanded: new Set([...s.expanded, key]),
            };
          });
          return;
        } catch {
          if (isCurrentRootRequest(get(), snapshot)) {
            set((s) => ({
              rootTrees: {
                ...s.rootTrees,
                [rootId]: { ...(s.rootTrees[rootId] ?? { nodes: EMPTY_FILE_NODES }), loading: false },
              },
            }));
          }
          // Keep the directory collapsed on error.
          return;
        }
      }
    }

    set((s) => {
      const next = new Set(s.expanded);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return { expanded: next };
    });
  },

  openFile: async (path: string, location?: EditorLocation) => {
    const state = get();
    const root = captureSession(state);
    if (!root) return false;
    const snapshot: OpenFileSnapshot = {
      ...root,
      requestGeneration: state.openRequestGeneration + 1,
      path,
      invalidationGeneration: currentOpenInvalidation(root.sessionId, path),
    };

    set({ selectedPath: path, openRequestGeneration: snapshot.requestGeneration });

    if (isEditorImagePath(path)) {
      const imageUrl = `/api/fs/read?${new URLSearchParams({
        session_id: snapshot.sessionId,
        path,
        download: '1',
      }).toString()}`;
      return get().openImage(path, {
        src: imageUrl,
        displayName: path.split(/[\\/]/).pop() || path,
      });
    }

    // Already open — just switch active
    if (get().openPaths.includes(path)) {
      set({ activePath: path, pendingLocation: location ?? null });
      return true;
    }

    // Fetch content
    try {
      const content = await readFile(snapshot.sessionId, path);
      if (!isCurrentOpenRequest(get(), snapshot)) return false;
      clearFilePathInvalidation(snapshot.sessionId, path);
      set((s) => {
        if (!isCurrentOpenRequest(s, snapshot)) return {};
        return {
          openPaths: s.openPaths.includes(path) ? s.openPaths : [...s.openPaths, path],
          activePath: path,
          contents: { ...s.contents, [path]: content },
          pendingLocation: location ?? null,
        };
      });
      return true;
    } catch (error) {
      if (isCurrentOpenRequest(get(), snapshot)) {
        set({ pendingLocation: null });
        useUIStore.getState().showToast(`打开文件失败：${openFileErrorMessage(error)}`, 'error');
      }
      return false;
    }
  },

  openImage: (path, preview) => {
    const state = get();
    const root = captureSession(state);
    if (!root || !preview.src) return false;
    set((s) => ({
      selectedPath: path,
      openPaths: s.openPaths.includes(path) ? s.openPaths : [...s.openPaths, path],
      activePath: path,
      imagePreviews: { ...s.imagePreviews, [path]: preview },
      pendingLocation: null,
    }));
    return true;
  },

  consumePendingLocation: (location: EditorLocation) => {
    set((s) => {
      const pending = s.pendingLocation;
      if (
        !pending ||
        pending.path !== location.path ||
        pending.line !== location.line ||
        pending.endLine !== location.endLine
      ) return {};
      return { pendingLocation: null };
    });
  },

  closeFile: (path: string) => {
    const root = captureSession(get());
    if (root) invalidateOpen(root.sessionId, path);
    set((s) => {
      const newOpen = s.openPaths.filter((p) => p !== path);
      const newDirty = new Set(s.dirty);
      newDirty.delete(path);
      const newContents = { ...s.contents };
      delete newContents[path];
      const newImagePreviews = { ...s.imagePreviews };
      delete newImagePreviews[path];
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
        imagePreviews: newImagePreviews,
        activePath: newActive,
        selectedPath: s.selectedPath === path ? newActive : s.selectedPath,
        pendingLocation: s.pendingLocation?.path === path ? null : s.pendingLocation,
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
      pendingLocation: null,
      openRequestGeneration: s.openRequestGeneration + 1,
    }));
  },

  requestSave: (repath?: string) => {
    const state = get();
    const root = captureSession(state);
    const path = repath || state.activePath;
    if (!root || !path || state.contents[path] === undefined) return;
    set({
      pendingConfirmation: {
        kind: 'save',
        path,
        sessionId: root.sessionId,
        rootGeneration: root.generation,
      },
    });
  },

  requestDelete: (path: string) => {
    const root = captureSession(get());
    if (!root || !path) return;
    set({
      pendingConfirmation: {
        kind: 'delete',
        path,
        sessionId: root.sessionId,
        rootGeneration: root.generation,
      },
    });
  },

  confirmPendingOperation: async () => {
    const pending = get().pendingConfirmation;
    if (!pending) return;
    set({ pendingConfirmation: null });

    const current = captureSession(get());
    if (
      !current ||
      current.sessionId !== pending.sessionId ||
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
    const snapshot = captureSession(get());
    if (!snapshot) return;
    const { activePath, contents } = get();
    const path = repath || activePath;
    if (!path) return;

    const content = contents[path];
    if (content === undefined) return;

    try {
      await enqueueFileOperation(snapshot.sessionId, async () => {
        // A rename/delete requested before this save may still be in flight.
        // Do not write the captured old path after that mutation completes.
        // Re-check the session at execution time as well, because a queued
        // save must not write into a session the user has already left.
        if (!isCurrentSession(get(), snapshot) || isFilePathInvalidated(snapshot.sessionId, path)) return;
        await writeFile(snapshot.sessionId, path, content);
        if (!isCurrentSession(get(), snapshot)) return;
        set((s) => {
          // Do not clear a newer draft created while the write was in flight.
          if (!isCurrentSession(s, snapshot) || s.contents[path] !== content) return {};
          const next = new Set(s.dirty);
          next.delete(path);
          return { dirty: next };
        });
      });
    } catch (error) {
      if (isCurrentSession(get(), snapshot)) {
        useUIStore.getState().showToast(`保存文件失败：${operationErrorMessage(error)}`, 'error');
      }
    }
  },

  renameFile: async (from: string, to: string) => {
    const snapshot = captureSession(get());
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
    invalidateFilePath(snapshot.sessionId, from);

    try {
      await enqueueFileOperation(snapshot.sessionId, async () => {
        await renameFs(snapshot.sessionId, from, to);
        if (!isCurrentSession(get(), snapshot)) return;
        clearFilePathInvalidation(snapshot.sessionId, to);
        invalidateOpen(snapshot.sessionId, from);
        set((s) => {
          if (!isCurrentSession(s, snapshot)) return {};

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
        refreshRootsForPath(get, from);
      });
    } catch (error) {
      useUIStore.getState().showToast(
        `重命名失败：${operationErrorMessage(error)}`,
        'error',
      );
    }
  },

  deleteFile: async (path: string) => {
    const snapshot = captureSession(get());
    if (!snapshot) return;

    invalidateFilePath(snapshot.sessionId, path);

    try {
      await enqueueFileOperation(snapshot.sessionId, async () => {
        await deleteFs(snapshot.sessionId, path);
        if (!isCurrentSession(get(), snapshot)) return;
        invalidateOpen(snapshot.sessionId, path);
        // Close if open
        get().closeFile(path);
        refreshRootsForPath(get, path);
      });
    } catch (error) {
      if (isCurrentSession(get(), snapshot)) {
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

// Refresh every root that contains `path` after a rename/delete, so each
// affected tree reflects the change without touching unrelated roots.
function refreshRootsForPath(get: () => EditorStore, path: string): void {
  const state = get();
  for (const root of state.roots) {
    if (!rootContainsPath(root, path)) continue;
    void get().refreshRoot(root.id, parentDirWithinRoot(root, path));
  }
}

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
