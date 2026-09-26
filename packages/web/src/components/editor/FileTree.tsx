import { useState, useRef, useEffect } from 'react';
import type { FileNode } from '@/types';
import { EMPTY_FILE_NODES, expansionKey, useEditorStore } from '@/stores/editorStore';
import { Download, File, Folder, FolderOpen, Pencil, X } from 'lucide-react';

interface FileTreeProps {
  /** Editor root whose tree this renders (see EditorRoot.id). */
  rootId: string;
}

function FileTreeItem({
  rootId,
  node,
  depth,
}: {
  rootId: string;
  node: FileNode;
  depth: number;
}) {
  const expanded = useEditorStore((s) => s.expanded);
  const selectedPath = useEditorStore((s) => s.selectedPath);
  const toggleDir = useEditorStore((s) => s.toggleDir);
  const openFile = useEditorStore((s) => s.openFile);
  const renameFile = useEditorStore((s) => s.renameFile);
  const requestDelete = useEditorStore((s) => s.requestDelete);
  const downloadFile = useEditorStore((s) => s.downloadFile);

  const isExpanded = expanded.has(expansionKey(rootId, node.path));
  const isSelected = selectedPath === node.path;
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(node.name);
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renaming) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [renaming]);

  const handleClick = () => {
    if (node.type === 'dir') {
      void toggleDir(rootId, node.path);
    } else {
      void openFile(node.path);
    }
  };

  const handleRename = () => {
    const newName = renameValue.trim();
    if (!newName || newName === node.name) {
      setRenaming(false);
      return;
    }
    // Absolute node paths are forward-slash normalized; keep a bare filesystem
    // root ('/' or 'D:/') as the parent instead of resolving to its drive only.
    const slash = node.path.lastIndexOf('/');
    const parentPath = slash < 0 ? '' : slash === 0 ? '/' : node.path.slice(0, slash);
    const newPath = parentPath
      ? parentPath.endsWith('/')
        ? `${parentPath}${newName}`
        : `${parentPath}/${newName}`
      : newName;
    void renameFile(node.path, newPath);
    setRenaming(false);
  };

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    requestDelete(node.path);
  };

  return (
    <div>
      <div
        className={`flex items-center gap-1 py-0.5 cursor-pointer text-xs hover:bg-bg-hover/50 group ${
          isSelected ? 'bg-accent/20 text-text-primary' : 'text-text-secondary'
        }`}
        style={{ paddingLeft: `${8 + depth * 16}px`, paddingRight: '8px' }}
        onClick={handleClick}
      >
        {node.type === 'dir' ? (
          <span
            className={`w-3 text-center flex-shrink-0 transition-transform ${
              isExpanded ? 'rotate-90' : ''
            }`}
          >
            <FolderOpen size={12} className="text-text-tertiary" />
          </span>
        ) : (
          <span className="w-3 flex-shrink-0" />
        )}

        {node.type === 'dir' ? (
          <Folder size={12} className="text-text-tertiary flex-shrink-0" />
        ) : (
          <File size={12} className="text-text-tertiary flex-shrink-0" />
        )}

        {renaming ? (
          <input
            ref={renameInputRef}
            className="bg-bg-tertiary border border-accent rounded px-1 py-0 text-xs w-full outline-none text-text-primary"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRename();
              if (e.key === 'Escape') setRenaming(false);
            }}
            onBlur={handleRename}
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span className="truncate flex-1">{node.name}</span>
        )}

        {!renaming && (
          <div className="hidden group-hover:flex items-center gap-0.5 flex-shrink-0">
            {node.type === 'file' && (
              <button
                className="text-text-tertiary hover:text-text-primary p-0.5"
                onClick={(e) => {
                  e.stopPropagation();
                  downloadFile(node.path);
                }}
                title="Download"
              >
                <Download size={10} />
              </button>
            )}
            <button
              className="text-text-tertiary hover:text-text-primary p-0.5"
              onClick={(e) => {
                e.stopPropagation();
                setRenaming(true);
                setRenameValue(node.name);
              }}
              title="Rename"
            >
              <Pencil size={10} />
            </button>
            <button
              className="text-text-tertiary hover:text-danger p-0.5"
              onClick={handleDelete}
              title="Delete"
            >
              <X size={10} />
            </button>
          </div>
        )}
      </div>

      {isExpanded && node.children && node.children.length > 0 && (
        <div>
          {node.children.map((child) => (
            <FileTreeItem key={child.path} rootId={rootId} node={child} depth={depth + 1} />
          ))}
        </div>
      )}

      {isExpanded && node.children && node.children.length === 0 && (
        <div
          className="text-[11px] text-text-tertiary italic"
          style={{ paddingLeft: `${8 + (depth + 1) * 16}px` }}
        >
          empty
        </div>
      )}
    </div>
  );
}

export function FileTree({ rootId }: FileTreeProps) {
  const tree = useEditorStore((s) => s.rootTrees[rootId]?.nodes ?? EMPTY_FILE_NODES);
  const treeLoading = useEditorStore((s) => s.rootTrees[rootId]?.loading ?? false);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex-1 overflow-y-auto">
        {treeLoading && (
          <div className="px-3 py-4 text-xs text-text-tertiary">Loading...</div>
        )}
        {!treeLoading && tree.length === 0 && (
          <div className="px-3 py-4 text-xs text-text-tertiary">Empty directory</div>
        )}
        {!treeLoading &&
          tree.map((node) => (
            <FileTreeItem key={node.path} rootId={rootId} node={node} depth={0} />
          ))}
      </div>
    </div>
  );
}
