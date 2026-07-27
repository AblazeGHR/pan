import { useEffect } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useEditorStore } from '@/stores/editorStore';
import { FileTree } from '@/components/editor/FileTree';
import { EditorPane } from '@/components/editor/EditorPane';

export default function EditorView() {
  const currentSession = useSessionStore((s) => s.currentSession);

  useEffect(() => {
    if (currentSession?.id && currentSession?.workdir) {
      useEditorStore.getState().setRoot(currentSession.id, currentSession.workdir);
    }
  }, [currentSession?.id, currentSession?.workdir]);

  if (!currentSession) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-tertiary text-sm">
        Select a session to browse its working directory
      </div>
    );
  }

  if (!currentSession.workdir) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-tertiary text-sm">
        This session has no working directory
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <FileTree workdir={currentSession.workdir} />
      <EditorPane />
    </div>
  );
}
