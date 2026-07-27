import { useEffect, useState } from 'react';
import { useSessionStore, useCurrentSession } from '@/stores/sessionStore';
import { useEditorStore } from '@/stores/editorStore';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { FileTree } from '@/components/editor/FileTree';
import { EditorPane } from '@/components/editor/EditorPane';

export default function EditorView() {
  const currentSession = useCurrentSession();
  const { isMobile } = useMediaQuery();
  const [treeOpen, setTreeOpen] = useState(false);

  useEffect(() => {
    if (currentSession?.id && currentSession?.workdir) {
      useEditorStore.getState().setRoot(currentSession.id, currentSession.workdir);
    }
  }, [currentSession?.id, currentSession?.workdir]);

  // Close tree when switching to desktop
  useEffect(() => {
    if (!isMobile) setTreeOpen(false);
  }, [isMobile]);

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
    <div className="flex h-full relative">
      <FileTree
        workdir={currentSession.workdir}
        isOpen={treeOpen}
        onClose={() => setTreeOpen(false)}
      />
      <EditorPane
        isMobile={isMobile}
        onToggleTree={() => setTreeOpen((v) => !v)}
      />
    </div>
  );
}
