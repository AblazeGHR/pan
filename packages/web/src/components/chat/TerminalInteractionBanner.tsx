import { useMemo, useState } from 'react';
import { Terminal, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { wsClient } from '@/services/ws';
import { sendSessionWorkerControl } from '@/services/api';
import type { TerminalInteraction } from '@/types';

function TerminalForm({ interaction }: { interaction: TerminalInteraction }) {
  const removeInteraction = useUIStore((s) => s.removeTerminalInteraction);
  const showToast = useUIStore((s) => s.showToast);
  const [value, setValue] = useState('');
  const [sending, setSending] = useState(false);

  const sendControl = async (control: Record<string, unknown>) => {
    setSending(true);
    const sent = wsClient.send({
      type: 'worker_control',
      sessionId: interaction.sessionId,
      control,
    });
    if (!sent) {
      try {
        await sendSessionWorkerControl(interaction.sessionId, control);
      } catch (error) {
        showToast((error as Error).message || '终端输入未发送', 'error');
        setSending(false);
        return false;
      }
    }
    setSending(false);
    return true;
  };

  const submit = async () => {
    if (!value) return;
    const ok = await sendControl({
      type: 'terminal_input',
      process_id: interaction.processId,
      text: value,
    });
    if (ok) {
      setValue('');
      removeInteraction(interaction.sessionId, interaction.itemId);
    }
  };

  const terminate = async () => {
    const ok = await sendControl({
      type: 'terminal_terminate',
      process_id: interaction.processId,
    });
    if (ok) removeInteraction(interaction.sessionId, interaction.itemId);
  };

  return (
    <div className="rounded-lg border border-accent/50 bg-bg-secondary px-3 py-2 shadow-panel">
      <div className="flex items-start gap-2">
        <Terminal size={17} className="mt-0.5 flex-shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-text-primary">Codex is waiting for terminal input</div>
          {interaction.stdin && (
            <pre className="mt-1 max-h-20 overflow-auto whitespace-pre-wrap break-all rounded bg-bg-primary px-2 py-1 font-mono text-xs text-text-secondary">
              {interaction.stdin}
            </pre>
          )}
          <div className="mt-2 flex gap-1">
            <input
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
              }}
              placeholder="Type input and press Enter"
              disabled={sending}
              className="min-w-0 flex-1 rounded border border-border-default bg-bg-primary px-2 py-1 text-xs text-text-primary placeholder:text-text-tertiary"
            />
            <Button size="sm" variant="primary" onClick={() => void submit()} disabled={sending || !value}>
              Send
            </Button>
            <Button size="sm" variant="danger" onClick={() => void terminate()} disabled={sending}>
              Stop
            </Button>
          </div>
        </div>
        <button
          type="button"
          className="p-1 text-text-tertiary hover:text-text-primary"
          title="Stop terminal process"
          onClick={() => void terminate()}
          disabled={sending}
        >
          <X size={15} />
        </button>
      </div>
    </div>
  );
}

export function TerminalInteractionBanner() {
  const showCodexTerminalInput = useAppSettingsStore((s) => s.showCodexTerminalInput);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const interactions = useUIStore((s) => s.terminalInteractions);
  const visibleInteractions = useMemo(
    () => currentSessionId
      ? interactions.filter((interaction) => interaction.sessionId === currentSessionId)
      : [],
    [currentSessionId, interactions],
  );

  if (!showCodexTerminalInput || visibleInteractions.length === 0) return null;
  return (
    <div className="mx-3 mb-2 space-y-2">
      {visibleInteractions.map((interaction) => (
        <TerminalForm key={`${interaction.workerId}:${interaction.itemId}`} interaction={interaction} />
      ))}
    </div>
  );
}
