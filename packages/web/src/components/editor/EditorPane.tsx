import { useEditorStore, languageFromPath } from '@/stores/editorStore';
import { EditorTabs } from './EditorTabs';
import { CodeEditor } from './CodeEditor';
import { MarkdownRenderer } from '@/components/chat/MarkdownRenderer';
import { Eye, Pencil, Columns2 } from 'lucide-react';

export function EditorPane() {
  const activePath = useEditorStore((s) => s.activePath);
  const contents = useEditorStore((s) => s.contents);
  const mdViewMode = useEditorStore((s) => s.mdViewMode);
  const setMdViewMode = useEditorStore((s) => s.setMdViewMode);

  const content = activePath ? contents[activePath] ?? null : null;

  const isMarkdown =
    activePath
      ? languageFromPath(activePath) === 'markdown'
      : false;

  const currentMode = isMarkdown
    ? (mdViewMode[activePath!] || 'preview')
    : 'edit';

  const modes: Array<{ key: 'edit' | 'preview' | 'split'; icon: React.ReactNode; label: string }> = [
    { key: 'edit', icon: <Pencil size={13} />, label: 'Edit' },
    { key: 'preview', icon: <Eye size={13} />, label: 'Preview' },
    { key: 'split', icon: <Columns2 size={13} />, label: 'Split' },
  ];

  return (
    <div className="flex flex-col flex-1 min-w-0 bg-bg-primary">
      <div className="flex items-stretch">
        <div className="flex-1 min-w-0">
          <EditorTabs />
        </div>
        {isMarkdown && activePath && (
          <div className="flex items-center gap-0.5 px-1.5 bg-bg-secondary border-b border-border-default">
            {modes.map((m) => (
              <button
                key={m.key}
                onClick={() => setMdViewMode(activePath, m.key)}
                className={`p-1 rounded transition-colors ${
                  currentMode === m.key
                    ? 'text-accent bg-accent/10'
                    : 'text-text-tertiary hover:text-text-primary'
                }`}
                title={m.label}
              >
                {m.icon}
              </button>
            ))}
          </div>
        )}
      </div>

      {!activePath ? (
        <div className="flex-1 flex items-center justify-center text-text-tertiary text-sm">
          Open a file to start editing
        </div>
      ) : currentMode === 'split' ? (
        <div className="flex-1 flex min-h-0">
          <div className="flex-1 min-w-0 border-r border-border-default">
            <CodeEditor path={activePath} content={content} />
          </div>
          <div className="flex-1 min-w-0 overflow-auto bg-bg-primary">
            <div className="p-4">
              <MarkdownRenderer content={content ?? ''} />
            </div>
          </div>
        </div>
      ) : currentMode === 'preview' ? (
        <div className="flex-1 overflow-auto bg-bg-primary">
          <div className="p-4 max-w-3xl mx-auto">
            <MarkdownRenderer content={content ?? ''} />
          </div>
        </div>
      ) : (
        <CodeEditor path={activePath} content={content} />
      )}
    </div>
  );
}
