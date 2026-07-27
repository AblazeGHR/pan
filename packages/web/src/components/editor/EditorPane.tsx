import { useEditorStore } from '@/stores/editorStore';
import { EditorTabs } from './EditorTabs';
import { CodeEditor } from './CodeEditor';

export function EditorPane() {
  const activePath = useEditorStore((s) => s.activePath);
  const contents = useEditorStore((s) => s.contents);

  const content = activePath ? contents[activePath] ?? null : null;

  return (
    <div className="flex flex-col flex-1 min-w-0 bg-bg-primary">
      <EditorTabs />
      <CodeEditor path={activePath} content={content} />
    </div>
  );
}
