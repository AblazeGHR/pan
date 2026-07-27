import { useRef, useCallback, useEffect } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { wsClient } from '@/services/ws';

export function InputRow() {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const currentSession = useSessionStore((s) => s.currentSession);
  const inputDrafts = useSessionStore((s) => s.inputDrafts);
  const addMessage = useSessionStore((s) => s.addMessage);
  const setInputDraft = useSessionStore((s) => s.setInputDraft);
  const { startWorker } = useWorkerStore();
  const { showToast } = useUIStore();

  // Restore draft when session changes
  useEffect(() => {
    if (inputRef.current && currentSessionId) {
      inputRef.current.value = inputDrafts[currentSessionId] || '';
    } else if (inputRef.current) {
      inputRef.current.value = '';
    }
  }, [currentSessionId, inputDrafts]);

  const handleSend = useCallback(
    async (text: string) => {
      if (!currentSessionId) {
        showToast('Select a session first');
        return;
      }
      if (!text.trim()) return;

      const session = currentSession;
      if (session?.workerStatus === 'running' || session?.workerStatus === 'held') {
        showToast('Worker is busy');
        return;
      }

      // Clear input and draft
      if (inputRef.current) {
        inputRef.current.value = '';
      }
      setInputDraft(currentSessionId, '');

      // Add user message to local state
      addMessage({ role: 'user', content: text });

      const msg = {
        type: 'user_inject',
        sessionId: currentSessionId,
        text,
      };

      if (wsClient.isOpen) {
        wsClient.send(msg);
        return;
      }

      if (!currentSession?.workerId) {
        // No worker — spawn one
        try {
          await startWorker(currentSessionId);
          wsClient.send(msg);
        } catch (e) {
          showToast(
            'Spawn failed: ' + (e as Error).message,
            'error',
          );
        }
        return;
      }

      // WS not connected and worker exists — try anyway
      if (!wsClient.send(msg)) {
        showToast('Connection lost. Please refresh the page.', 'error');
      }
    },
    [
      currentSessionId,
      currentSession,
      showToast,
      addMessage,
      startWorker,
      setInputDraft,
    ],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const text = inputRef.current?.value || '';
      handleSend(text);
    }
  };

  return (
    <div className="border-t border-border-default bg-bg-primary p-3">
      <div className="flex gap-2">
        <textarea
          ref={inputRef}
          placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
          rows={2}
          className="flex-1 rounded border border-border-default bg-bg-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary resize-none focus:outline-none focus:border-accent"
          onKeyDown={handleKeyDown}
        />
        <button
          onClick={() => handleSend(inputRef.current?.value || '')}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover transition-colors self-end"
        >
          Send
        </button>
      </div>
    </div>
  );
}
