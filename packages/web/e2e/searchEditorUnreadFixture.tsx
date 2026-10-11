import { useState } from 'react';
import type { RefObject } from 'react';
import { createRoot } from 'react-dom/client';
import { WorkspaceRail } from '@/components/layout/WorkspaceRail';
import { FileTree } from '@/components/editor/FileTree';
import { EditorConfirmationModal } from '@/components/editor/EditorConfirmationModal';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useUIStore } from '@/stores/uiStore';
import { useEditorStore } from '@/stores/editorStore';
import { SessionHistorySearch } from '@/components/chat/SessionHistorySearch';
import { GlobalHistorySearch } from '@/components/chat/GlobalHistorySearch';
import type { ChatMessagesHandle } from '@/components/chat/ChatMessages';
import type { Session } from '@/types';
import '@/index.css';

useWorkspaceStore.setState({
  loaded: true,
  workspaces: [
    { id: 'w1', name: 'Inherited', createdAt: '' },
    { id: 'w2', name: 'Other', createdAt: '' },
  ],
});
useSessionStore.setState({
  sessions: [
    { id: 'root', workspaceIds: ['w1'], unreadDoneCount: 0, workerStatus: 'running' },
    { id: 'child', managedBy: 'root', workspaceIds: ['w2'], unreadDoneCount: 2 },
    { id: 'other', workspaceIds: ['w2'], unreadDoneCount: 0 },
  ] as Session[],
});
useUIStore.setState({ railExpanded: true });
useEditorStore.setState({
  sessionId: 'fixture',
  roots: [{ id: 'fixture', kind: 'cwd', path: '/fixture', label: 'CWD' }],
  rootTrees: {
    fixture: {
      nodes: [{ name: 'draft.ts', path: '/fixture/draft.ts', type: 'file', size: 5, modified: '' }],
      loading: false,
    },
  },
  expanded: new Set(),
  openPaths: ['/fixture/draft.ts'],
  activePath: '/fixture/draft.ts',
  selectedPath: '/fixture/draft.ts',
  dirty: new Set(['/fixture/draft.ts']),
  contents: { '/fixture/draft.ts': 'draft' },
  pendingConfirmation: null,
});

export function Fixture() {
  const mobile = new URLSearchParams(location.search).has('mobile');
  const [expanded, setExpanded] = useState(true);
  const [localOpen, setLocalOpen] = useState(false);
  const [globalOpen, setGlobalOpen] = useState(false);
  const chatRef = {
    current: { scrollToMessage: () => true },
  } as RefObject<ChatMessagesHandle | null>;
  if (new URLSearchParams(location.search).has('search'))
    return (
      <>
        <SessionHistorySearch
          chatRef={chatRef}
          isMobile={mobile}
          isOpen={localOpen}
          onOpenChange={setLocalOpen}
          onHighlightMessage={() => {}}
        />
        <GlobalHistorySearch
          chatRef={chatRef}
          isMobile={mobile}
          open={globalOpen}
          onOpenChange={setGlobalOpen}
          onHighlightMessage={() => {}}
        />
      </>
    );
  return (
    <div style={{ height: 500, display: 'flex' }}>
      <div style={{ width: mobile ? 172 : undefined }}>
        <WorkspaceRail
          mobileDrawer={mobile}
          mobileExpanded={expanded}
          onMobileExpandedChange={setExpanded}
        />
      </div>
      <div style={{ width: 200 }}>
        <FileTree rootId="fixture" />
        <EditorConfirmationModal />
        <button
          onClick={() =>
            useSessionStore.setState((state) => ({
              sessions: state.sessions.map((session) => ({ ...session, unreadDoneCount: 0 })),
            }))
          }
        >
          Mark all read
        </button>
      </div>
    </div>
  );
}
useSessionStore.setState({
  currentSessionId: 'root',
  currentMessages: [
    { role: 'assistant', content: 'needle', messageId: 'hit0' },
    { role: 'assistant', content: 'needle', messageId: 'hit1' },
    { role: 'assistant', content: 'needle', messageId: 'hit2' },
  ],
});
createRoot(document.getElementById('root')!).render(<Fixture />);
