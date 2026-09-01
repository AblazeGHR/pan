import { useRef, useCallback, useEffect, useState } from 'react';
import { useSessionStore, useCurrentSession } from '@/stores/sessionStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useUIStore } from '@/stores/uiStore';
import { useAdapterStore } from '@/stores/adapterStore';
import { useQueueStore } from '@/stores/queueStore';
import { SendQueuePanel } from '@/components/chat/SendQueuePanel';
import { SettingsPopover } from '@/components/chat/SettingsPopover';
import { ModelSelect } from '@/components/ui/ModelSelect';
import { ChevronDown, ChevronUp, CornerUpRight, Settings } from 'lucide-react';
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

  const current = sessionModel || defaultModel;

  // 复用带搜索过滤的 ModelSelect（与 SettingsPopover 保持一致），仅通过
  // buttonClassName / menuClassName 适配 pill 外观与向上展开的交互。
  return (
    <div data-model-pill className="relative">
      <ModelSelect
        value={current}
        options={models}
        onChange={(m) => onApply('model', m)}
        buttonClassName={PILL_CLASS + ' font-semibold'}
        menuClassName="absolute left-0 bottom-full mb-1 z-40 min-w-[160px] w-max"
      />
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
  const [open, setOpen] = useState(false);
  const current = sessionMode || defaultMode;
  const active = modes.find((m) => m.value === current);
  // Keep the collapsed toolbar pill compact; the expanded menu still shows
  // the adapter's full label and its CLI hint.
  const label = (active?.label || current).replace(/\s*\(.*$/, '').trim();

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-perm-pill]')) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  if (!show) return null;

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
  const { steer } = useWorkerStore();
  const { showToast } = useUIStore();
  const [settingsOpen, setSettingsOpen] = useState(false);
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
  }, [currentSession, loadConfig]);

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

      // Every user message goes to the server queue.  Clear the input only
      // after the server returns a durable queueItemId; a network failure is
      // not an offline accepted queue state.
      const ok = await enqueue(text);
      if (ok) {
        if (inputRef.current) inputRef.current.value = '';
        setInputDraft(currentSessionId, '');
      }
    },
    [
      currentSessionId,
      showToast,
      setInputDraft,
      enqueue,
    ],
  );

  const handleSteer = useCallback(
    async (text: string) => {
      if (!currentSessionId || !text.trim() || !currentSession?.workerId) return;
      try {
        await steer(currentSession.workerId, text);
        if (inputRef.current) inputRef.current.value = '';
        setInputDraft(currentSessionId, '');
        addMessage({ role: 'user', content: text });
      } catch (e) {
        showToast((e as Error).message || 'Steer failed', 'error');
      }
    },
    [currentSessionId, currentSession, steer, setInputDraft, addMessage, showToast],
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
  // Effort only makes sense with thinking enabled (mirrors SettingsPopover).
  const showEffort =
    supportsSetting(config, 'effort') &&
    (!showThinking || !!currentSession?.alwaysThinkingEnabled);
  const modelEfforts = config?.modelEfforts?.[currentSession?.model || config?.defaultModel || ''];
  const effortValues = modelEfforts ? ['', ...modelEfforts] : config?.effortValues || [];
  // opencode's effort list starts with "" (unset sentinel); filter it out so
  // the dropdown never renders a blank <option>, and surface it as a clear
  // "默认" placeholder instead.
  const validEffortValues = effortValues.filter(
    (v) => v && String(v).trim() !== '',
  );
  const hadEmptyEffort = effortValues.length !== validEffortValues.length;
  const currentEffort =
    currentSession?.effort && validEffortValues.includes(currentSession.effort.trim())
      ? currentSession.effort
      : hadEmptyEffort
        ? ''
        : validEffortValues[0] ?? '';
  const canSteer =
    currentSession?.adapter === 'codex' &&
    currentSession.workerStatus === 'running' &&
    !!currentSession.workerId;

  return (
    <div className="shrink-0 w-full border-t border-border-default bg-bg-primary">
      {/* 待发送队列面板（默认折叠，^ 按钮展开） */}
      <SendQueuePanel />

      {/* 左列：settings gear（有会话时）+ 队列开关 ^ 上下垂直紧凑堆叠，节省一行。
          右侧内容列：pill 行 + textarea/Send 行。 */}
      <div className="flex gap-2 px-3 pt-2 pb-[max(16px,var(--safe-bottom))] md:pb-3">
        {/* 左列竖排：gear 在上、^ 在下，gap-1 紧挨 */}
        <div className="flex flex-col gap-1 shrink-0 self-start">
          {currentSession && (
            <div data-settings-popover className="relative">
              <button
                onClick={() => setSettingsOpen((v) => !v)}
                title="Session settings"
                aria-label="Session settings"
                className={`flex h-7 w-7 items-center justify-center rounded border transition-colors ${
                  settingsOpen
                    ? 'border-accent/50 bg-accent/10 text-accent'
                    : 'border-border-default bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                }`}
              >
                <Settings size={14} />
              </button>
              <SettingsPopover
                open={settingsOpen}
                onClose={() => setSettingsOpen(false)}
              />
            </div>
          )}
          {/* ^ 队列开关：非空时高亮 + 角标 */}
          <div className="relative">
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
        </div>

        {/* 右侧内容列 */}
        <div className="flex-1 min-w-0 flex flex-col gap-2">
          {currentSession && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <ModelPill
                sessionModel={currentSession.model || ''}
                defaultModel={config?.defaultModel || ''}
                models={config?.models || []}
                show={showModelPill}
                onApply={applySetting}
              />
              <div className="hidden md:flex">
                <PermissionPill
                  sessionMode={currentSession.permissionMode || null}
                  defaultMode={config?.defaultPermissionMode || ''}
                  modes={config?.permissionModes || []}
                  show={showPermPill}
                  onApply={applySetting}
                />
              </div>
              <ThinkingToggle
                enabled={currentSession.alwaysThinkingEnabled}
                show={showThinking}
                onApply={applySetting}
              />
              {showEffort && validEffortValues.length > 0 && (
                <select
                  value={currentEffort}
                  onChange={(e) => applySetting('effort', e.target.value)}
                  className="rounded-md border border-border-default bg-bg-tertiary px-1 py-1 text-xs text-text-primary focus:outline-none focus:border-accent"
                  title="Effort"
                >
                  {hadEmptyEffort && <option value="">默认</option>}
                  {validEffortValues.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          {/* Textarea + Send row */}
          <div className="flex gap-2">
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
              {canSteer && (
                <button
                  onClick={() => handleSteer(inputRef.current?.value || '')}
                  className="inline-flex items-center gap-1 rounded border border-accent/50 bg-accent/10 px-2 py-1 text-xs font-medium text-accent hover:bg-accent/20 transition-colors"
                  title="Send an instruction to the running Codex turn"
                >
                  <CornerUpRight size={13} />
                  Steer
                </button>
              )}
              <button
                onClick={() => handleSend(inputRef.current?.value || '')}
                className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover transition-colors self-end"
              >
                Send
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
