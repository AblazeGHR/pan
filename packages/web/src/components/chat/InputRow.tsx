import { useRef, useCallback, useEffect, useState } from 'react';
import { useSessionStore, useCurrentSession } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { useQueueStore } from '@/stores/queueStore';
import { SendQueuePanel } from '@/components/chat/SendQueuePanel';
import { wsClient } from '@/services/ws';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { AdapterConfig, PermissionMode } from '@/types';

const PILL_CLASS =
  'inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-border-default bg-bg-tertiary hover:bg-bg-hover cursor-pointer transition-colors';

const DROPDOWN_ITEM =
  'px-2 py-1 text-xs hover:bg-bg-hover cursor-pointer whitespace-nowrap';

// ── helpers ──

function supportsSetting(
  config: AdapterConfig | null,
  name: string,
): boolean {
  if (!config?.supportedSettings) return false;
  return config.supportedSettings.includes(name);
}

function permBorderClass(value: string): string {
  if (value === 'bypass') return 'border-danger/50';
  if (value === 'yolo' || value === 'acceptEdits') return 'border-warning/50';
  return 'border-border-default';
}

// ── pill sub-components ──

function ModelPill({
  sessionModel,
  defaultModel,
  models,
  show,
  onApply,
}: {
  sessionModel: string;
  defaultModel: string;
  models: string[];
  show: boolean;
  onApply: (key: string, value: string) => void;
}) {
  if (!show) return null;

  const [open, setOpen] = useState(false);
  const current = sessionModel || defaultModel;

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-model-pill]')) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div data-model-pill className="relative">
      <button className={PILL_CLASS} onClick={() => setOpen(!open)}>
        <span className="font-semibold">{current}</span>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="absolute bottom-full mb-1 left-0 min-w-[160px] rounded-md border border-border-default bg-bg-primary shadow-lg z-30">
          {models.map((m) => (
            <div
              key={m}
              className={
                DROPDOWN_ITEM +
                (m === current ? ' bg-accent/10 text-accent' : '')
              }
              onClick={() => {
                onApply('model', m);
                setOpen(false);
              }}
            >
              {m}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PermissionPill({
  sessionMode,
  defaultMode,
  modes,
  show,
  onApply,
}: {
  sessionMode: string | null;
  defaultMode: string;
  modes: PermissionMode[];
  show: boolean;
  onApply: (key: string, value: string) => void;
}) {
  if (!show) return null;

  const [open, setOpen] = useState(false);
  const current = sessionMode || defaultMode;
  const active = modes.find((m) => m.value === current);
  const label = active?.label || current;

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-perm-pill]')) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div data-perm-pill className="relative">
      <button
        className={PILL_CLASS + ' ' + permBorderClass(current)}
        onClick={() => setOpen(!open)}
      >
        <span>{label}</span>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="absolute bottom-full mb-1 left-0 min-w-[160px] rounded-md border border-border-default bg-bg-primary shadow-lg z-30">
          {modes.map((m) => (
            <div
              key={m.value}
              className={
                DROPDOWN_ITEM +
                (m.value === current ? ' bg-accent/10 text-accent' : '')
              }
              onClick={() => {
                onApply('permissionMode', m.value);
                setOpen(false);
              }}
            >
              {m.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ThinkingToggle({
  enabled,
  show,
  onApply,
}: {
  enabled: boolean;
  show: boolean;
  onApply: (key: string, value: boolean) => void;
}) {
  if (!show) return null;

  return (
    <button
      className={
        PILL_CLASS +
        (enabled ? ' bg-accent/10 border-accent/50 text-accent' : '')
      }
      onClick={() => onApply('alwaysThinkingEnabled', !enabled)}
    >
      Thinking
    </button>
  );
}

// ── main component ──

export function InputRow() {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const currentSession = useCurrentSession();
  const addMessage = useSessionStore((s) => s.addMessage);
  const setInputDraft = useSessionStore((s) => s.setInputDraft);
  const { startWorker } = useWorkerStore();
  const { showToast } = useUIStore();
  const enqueue = useQueueStore((s) => s.enqueue);
  const panelOpen = useQueueStore((s) => s.panelOpen);
  const togglePanel = useQueueStore((s) => s.togglePanel);
  // 队列计数（含编辑中的一条）：原始值比较，selector 稳定
  const queueCount = useQueueStore((s) => {
    if (!currentSessionId) return 0;
    const q = s.queues[currentSessionId];
    const e = s.edits[currentSessionId];
    return (q ? q.length : 0) + (e ? 1 : 0);
  });

  // ── Adapter settings ──
  const config = useAdapterStore((s) => s.getConfig());
  const loadConfig = useAdapterStore((s) => s.loadConfig);
  const applySettings = useAdapterStore((s) => s.applySettings);
  const { loadSessions } = useSessionStore();

  useEffect(() => {
    if (currentSession) {
      loadConfig(currentSession.adapter || 'cbc');
    }
  }, [currentSession?.id]);

  const applySetting = async (key: string, value: unknown) => {
    if (!currentSession) return;
    try {
      await applySettings(currentSession.id, undefined, { [key]: value });
      await loadSessions();
    } catch (e) {
      showToast((e as Error).message || 'Failed', 'error');
    }
  };

  // Restore draft when session changes. Reads from getState() so it does not
  // depend on `inputDrafts` (which would re-run — and reset the caret — on
  // every keystroke now that onChange persists drafts).
  useEffect(() => {
    if (!inputRef.current) return;
    const draft = currentSessionId
      ? useSessionStore.getState().inputDrafts[currentSessionId]
      : '';
    inputRef.current.value = draft || '';
  }, [currentSessionId]);

  const handleSend = useCallback(
    async (text: string) => {
      if (!currentSessionId) {
        showToast('Select a session first');
        return;
      }
      if (!text.trim()) return;

      const session = currentSession;
      if (session?.workerStatus === 'running' || session?.workerStatus === 'held') {
        // worker 忙：不再拒绝，改为入队（空闲后自动逐条/拼接发送）；入队不上屏
        const ok = enqueue(text);
        if (ok) {
          if (inputRef.current) {
            inputRef.current.value = '';
          }
          setInputDraft(currentSessionId, '');
        }
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
      enqueue,
    ],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const text = inputRef.current?.value || '';
      handleSend(text);
    }
  };

  const showModelPill = supportsSetting(config, 'model');
  const showPermPill = supportsSetting(config, 'permissionMode');
  const showThinking = supportsSetting(config, 'thinking');
  const hasToolbar = currentSession && (showModelPill || showPermPill || showThinking);

  return (
    <div className="border-t border-border-default bg-bg-primary">
      {/* 待发送队列面板（默认折叠，^ 按钮展开） */}
      <SendQueuePanel />

      {/* Toolbar row */}
      {hasToolbar && (
        <div className="flex items-center gap-1.5 px-3 pt-2 pb-0">
          <div className="flex items-center gap-1.5 flex-1 min-w-0">
            <ModelPill
              sessionModel={currentSession.model || ''}
              defaultModel={config?.defaultModel || ''}
              models={config?.models || []}
              show={showModelPill}
              onApply={applySetting}
            />
            <PermissionPill
              sessionMode={currentSession.permissionMode || null}
              defaultMode={config?.defaultPermissionMode || ''}
              modes={config?.permissionModes || []}
              show={showPermPill}
              onApply={applySetting}
            />
            <ThinkingToggle
              enabled={currentSession.alwaysThinkingEnabled}
              show={showThinking}
              onApply={applySetting}
            />
          </div>
        </div>
      )}

      {/* Textarea + Send row */}
      <div className="flex gap-2 px-3 pt-2 pb-[max(16px,var(--safe-bottom))] md:pb-3">
        {/* ^ 队列开关：非空时高亮 + 角标 */}
        <div className="relative shrink-0 self-start mt-0.5">
          <button
            onClick={togglePanel}
            title={
              queueCount > 0
                ? `发送队列（${queueCount} 条待发）`
                : '发送队列'
            }
            aria-label="发送队列"
            className={`flex h-9 w-9 items-center justify-center rounded border transition-colors ${
              panelOpen || queueCount > 0
                ? 'border-accent/50 bg-accent/10 text-accent'
                : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover'
            }`}
          >
            <ChevronUp
              size={16}
              className={`transition-transform duration-200 ${
                panelOpen ? 'rotate-180' : ''
              }`}
            />
          </button>
          {queueCount > 0 && (
            <span className="absolute -top-1.5 -right-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-medium leading-none text-white">
              {queueCount > 99 ? '99+' : queueCount}
            </span>
          )}
        </div>

        <textarea
          ref={inputRef}
          id="chatInput"
          placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
          rows={2}
          enterKeyHint="send"
          inputMode="text"
          autoCapitalize="sentences"
          className="flex-1 rounded border border-border-default bg-bg-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary resize-none focus:outline-none focus:border-accent"
          onChange={(e) => {
            if (currentSessionId) setInputDraft(currentSessionId, e.target.value);
          }}
          onKeyDown={handleKeyDown}
        />
        <div className="flex flex-col gap-1 items-end">
          <button
            onClick={() => handleSend(inputRef.current?.value || '')}
            className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover transition-colors self-end"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
