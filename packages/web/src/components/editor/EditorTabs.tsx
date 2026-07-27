import { useEditorStore } from '@/stores/editorStore';

export function EditorTabs() {
  const openPaths = useEditorStore((s) => s.openPaths);
  const activePath = useEditorStore((s) => s.activePath);
  const dirty = useEditorStore((s) => s.dirty);
  const setActive = useEditorStore((s) => s.setActive);
  const closeFile = useEditorStore((s) => s.closeFile);

  if (openPaths.length === 0) return null;

  const getFileName = (p: string) => {
    const idx = p.lastIndexOf('/');
    return idx === -1 ? p : p.substring(idx + 1);
  };

  return (
    <div className="flex items-stretch h-8 bg-bg-secondary border-b border-border-default flex-shrink-0 overflow-x-auto">
      {openPaths.map((p) => {
        const isActive = p === activePath;
        const isDirty = dirty.has(p);
        return (
          <div
            key={p}
            className={`flex items-center gap-1.5 px-3 text-xs cursor-pointer border-r border-border-default flex-shrink-0 select-none group ${
              isActive
                ? 'bg-bg-primary text-text-primary border-b-2 border-b-accent -mb-px'
                : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-tertiary'
            }`}
            onClick={() => setActive(p)}
            onAuxClick={(e) => {
              if (e.button === 1) {
                e.preventDefault();
                closeFile(p);
              }
            }}
          >
            {/* Dirty indicator */}
            <span
              className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                isDirty ? 'bg-text-tertiary' : 'bg-transparent'
              }`}
            />
            {/* File name */}
            <span className="truncate max-w-[120px]">{getFileName(p)}</span>
            {/* Close button */}
            <button
              className={`ml-0.5 p-0.5 rounded hover:bg-bg-hover text-text-tertiary hover:text-text-primary flex-shrink-0 ${
                isActive ? 'visible' : 'invisible group-hover:visible'
              }`}
              onClick={(e) => {
                e.stopPropagation();
                closeFile(p);
              }}
              title="Close"
            >
              ✕
            </button>
          </div>
        );
      })}
    </div>
  );
}
