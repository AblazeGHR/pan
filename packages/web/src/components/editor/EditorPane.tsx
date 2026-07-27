import { useEditorStore } from '@/stores/editorStore';
import { EditorTabs } from './EditorTabs';
import { CodeEditor } from './CodeEditor';

interface EditorPaneProps {
  isMobile?: boolean;
  onToggleTree?: () => void;
}

export function EditorPane({ isMobile, onToggleTree }: EditorPaneProps) {
  const activePath = useEditorStore((s) => s.activePath);
  const contents = useEditorStore((s) => s.contents);

  const content = activePath ? contents[activePath] ?? null : null;

  return (
    <div className="flex flex-col flex-1 min-w-0 bg-bg-primary">
      {/* Tab bar row — with hamburger on mobile */}
      <div className="flex items-stretch">
        {isMobile && (
          <button
            onClick={onToggleTree}
            className="flex items-center px-3 bg-bg-secondary border-r border-b border-border-default hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors flex-shrink-0"
            title="Toggle file tree"
          >
            ☰
          </button>
        )}
        <div className="flex-1 min-w-0">
          <EditorTabs />
        </div>
      </div>
      <CodeEditor path={activePath} content={content} />
    </div>
  );
}
