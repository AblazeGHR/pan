import { useState, useRef, useEffect, useCallback } from 'react';
import type { FileNode, FsEntry } from '@/types';
import { useEditorStore } from '@/stores/editorStore';

interface FileTreeProps {
  sessionId: string;
  workdir: string;
}

function FileTreeItem({
  node,
  depth,
}: {
  node: FileNode;
  depth: number;
}) {
  const expanded = useEditorStore((s) => s.expanded);
  const selectedPath = useEditorStore((s) => s.selectedPath);
  const toggleDir = useEditorStore((s) => s.toggleDir);
  const openFile = useEditorStore((s) => s.openFile);
  const setActive = useEditorStore((s) => s.setActive);
  const renameFile = useEditorStore((s) => s.renameFile);
  const deleteFile = useEditorStore((s) => s.deleteFile);

  const isExpanded = expanded.has(node.path);
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
      toggleDir(node.path);
    } else {
      openFile(node.path);
    }
  };

  const handleRename = () => {
    const newName = renameValue.trim();
    if (!newName || newName === node.name) {
      setRenaming(false);
      return;
    }
    const parentPath = node.path.includes('/')
      ? node.path.substring(0, node.path.lastIndexOf('/'))
      : '';
    const newPath = parentPath ? `${parentPath}/${newName}` : newName;
    renameFile(node.path, newPath);
    setRenaming(false);
  };

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    const confirmed = window.confirm(`Delete "${node.name}"?`);
    if (confirmed) {
      deleteFile(node.path);
    }
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
        {/* Indent guides */}
        {/* Chevron or spacer */}
        {node.type === 'dir' ? (
          <span
            className={`w-3 text-center text-[10px] transition-transform flex-shrink-0 ${
              isExpanded ? 'rotate-90' : ''
            }`}
          >
            ▶
          </span>
        ) : (
          <span className="w-3 flex-shrink-0" />
        )}

        {/* File/dir name or rename input */}
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

        {/* Actions on hover */}
        {!renaming && (
          <div className="hidden group-hover:flex items-center gap-0.5 flex-shrink-0">
            <button
              className="text-text-tertiary hover:text-text-primary px-0.5"
              onClick={(e) => {
                e.stopPropagation();
                setRenaming(true);
                setRenameValue(node.name);
              }}
              title="Rename"
            >
              ✎
            </button>
            <button
              className="text-text-tertiary hover:text-danger px-0.5"
              onClick={handleDelete}
              title="Delete"
            >
              ✕
            </button>
          </div>
        )}
      </div>

      {/* Children */}
      {isExpanded && node.children && node.children.length > 0 && (
        <div>
          {node.children.map((child) => (
            <FileTreeItem key={child.path} node={child} depth={depth + 1} />
          ))}
        </div>
      )}

      {/* Empty dir indicator */}
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

export function FileTree({ sessionId, workdir }: FileTreeProps) {
  const tree = useEditorStore((s) => s.tree);
  const treeLoading = useEditorStore((s) => s.treeLoading);
  const refreshTree = useEditorStore((s) => s.refreshTree);

  return (
    <div className="w-60 h-full flex flex-col bg-bg-secondary border-r border-border-default flex-shrink-0">
      {/* Title bar */}
      <div className="flex items-center justify-between px-3 py-2 text-[11px] font-semibold text-text-tertiary uppercase tracking-wider border-b border-border-default">
        <span>Files</span>
        <button
          className="hover:text-text-primary transition-colors"
          onClick={() => refreshTree('')}
          title="Refresh"
        >
          ↻
        </button>
      </div>

      {/* Tree content */}
      <div className="flex-1 overflow-y-auto">
        {treeLoading && (
          <div className="px-3 py-4 text-xs text-text-tertiary">Loading...</div>
        )}
        {!treeLoading && tree.length === 0 && (
          <div className="px-3 py-4 text-xs text-text-tertiary">Empty directory</div>
        )}
        {!treeLoading &&
          tree.map((node) => (
            <FileTreeItem key={node.path} node={node} depth={0} />
          ))}
      </div>

      {/* Footer: workdir path */}
      <div className="px-3 py-1.5 text-[10px] text-text-tertiary border-t border-border-default truncate" title={workdir}>
        {workdir}
      </div>
    </div>
  );
}
