import { create } from 'zustand';
import type { FileNode } from '@/types';
import { listFiles, readFile, writeFile, renameFs, deleteFs } from '@/services/api';

// Module-level ref for Monaco model disposal on tab close.
// Type is 'any' because monaco-editor is loaded dynamically via @monaco-editor/react.
let monacoRef: any = null;
export function setMonacoRef(m: any) {
  monacoRef = m;
}

interface EditorStore {
  // state
  sessionId: string | null;
  tree: FileNode[];
  treeLoading: boolean;
  expanded: Set<string>;
  selectedPath: string | null;
  openPaths: string[];
  activePath: string | null;
  dirty: Set<string>;
  contents: Record<string, string>;

  // actions
  setRoot: (sessionId: string, workdir: string) => Promise<void>;
  refreshTree: (dirPath?: string) => Promise<void>;
  toggleDir: (path: string) => Promise<void>;
  openFile: (path: string) => Promise<void>;
  closeFile: (path: string) => void;
  setActive: (path: string) => void;
  markDirty: (path: string, content: string) => void;
  saveFile: (repath?: string) => Promise<void>;
  renameFile: (from: string, to: string) => Promise<void>;
  deleteFile: (path: string) => Promise<void>;
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
  tree: [],
  treeLoading: false,
  expanded: new Set(),
  selectedPath: null,
  openPaths: [],
  activePath: null,
  dirty: new Set(),
  contents: {},

  setRoot: async (sessionId: string, _workdir: string) => {
    set({ sessionId, treeLoading: true, tree: [], expanded: new Set(), selectedPath: null });
    try {
      const rootNodes = await fetchTree(sessionId, '', '');
      set({ tree: rootNodes, treeLoading: false });
    } catch {
      set({ treeLoading: false });
    }
  },

  refreshTree: async (dirPath?: string) => {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      const nodes = await fetchTree(sessionId, dirPath || '', dirPath || '');
      set((s) => {
        if (!dirPath) {
          return { tree: nodes };
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
        return { tree: replaceInTree(s.tree) };
      });
    } catch {
      // silently fail
    }
  },

  toggleDir: async (path: string) => {
    const { sessionId, expanded, tree } = get();
    if (!sessionId) return;

    const isExpanded = expanded.has(path);

    if (!isExpanded) {
      // Expand — lazy load children if empty
      const node = findNode(tree, path);
      if (node && node.children && node.children.length === 0) {
        try {
          const children = await fetchTree(sessionId, path, path);
          set((s) => ({
            tree: replaceNode(s.tree, path, { children }),
            expanded: new Set([...s.expanded, path]),
          }));
          return;
        } catch {
          // keep collapsed on error
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
    const { sessionId, openPaths } = get();
    if (!sessionId) return;

    set({ selectedPath: path });

    // Already open — just switch active
    if (openPaths.includes(path)) {
      set({ activePath: path });
      return;
    }

    // Fetch content
    try {
      const content = await readFile(sessionId, path);
      set((s) => ({
        openPaths: [...s.openPaths, path],
        activePath: path,
        contents: { ...s.contents, [path]: content },
      }));
    } catch {
      // show error — file may not be readable
    }
  },

  closeFile: (path: string) => {
    set((s) => {
      const newOpen = s.openPaths.filter((p) => p !== path);
      const newDirty = new Set(s.dirty);
      newDirty.delete(path);
      const { [path]: _, ...newContents } = s.contents;
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
      if (monacoRef) {
        try {
          const uri = monacoRef.Uri.parse(path);
          const model = monacoRef.editor.getModel(uri);
          if (model && !model.isDisposed()) {
            model.dispose();
          }
        } catch {
          // ignore
        }
      }
      return {
        openPaths: newOpen,
        dirty: newDirty,
        contents: newContents,
        activePath: newActive,
      };
    });
  },

  setActive: (path: string) => {
    set({ activePath: path, selectedPath: path });
  },

  markDirty: (path: string, content: string) => {
    set((s) => ({
      dirty: new Set([...s.dirty, path]),
      contents: { ...s.contents, [path]: content },
    }));
  },

  saveFile: async (repath?: string) => {
    const { sessionId, activePath, contents } = get();
    if (!sessionId) return;
    const path = repath || activePath;
    if (!path) return;

    const content = contents[path];
    if (content === undefined) return;

    try {
      await writeFile(sessionId, path, content);
      set((s) => {
        const next = new Set(s.dirty);
        next.delete(path);
        return { dirty: next };
      });
    } catch {
      // show error
    }
  },

  renameFile: async (from: string, to: string) => {
    const { sessionId, openPaths, dirty, contents } = get();
    if (!sessionId) return;

    try {
      await renameFs(sessionId, from, to);
      // Update open paths and dirty
      const newOpen = openPaths.map((p) => (p === from ? to : p));
      const newDirty = new Set<string>();
      for (const p of dirty) {
        newDirty.add(p === from ? to : p);
      }
      const newContents: Record<string, string> = {};
      for (const [k, v] of Object.entries(contents)) {
        newContents[k === from ? to : k] = v;
      }
      set((s) => ({
        openPaths: newOpen,
        activePath: s.activePath === from ? to : s.activePath,
        dirty: newDirty,
        contents: newContents,
      }));
      // Refresh parent dir
      const parentPath = from.includes('/') ? from.substring(0, from.lastIndexOf('/')) : '';
      get().refreshTree(parentPath);
    } catch {
      // show error
    }
  },

  deleteFile: async (path: string) => {
    const { sessionId } = get();
    if (!sessionId) return;

    try {
      await deleteFs(sessionId, path);
      // Close if open
      get().closeFile(path);
      // Refresh parent dir
      const parentPath = path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : '';
      get().refreshTree(parentPath);
    } catch {
      // show error
    }
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

function replaceNode(
  nodes: FileNode[],
  path: string,
  patch: Partial<FileNode>,
): FileNode[] {
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
