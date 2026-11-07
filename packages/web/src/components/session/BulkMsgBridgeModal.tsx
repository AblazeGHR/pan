import { useCallback, useEffect, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { fetchSession, patchSession, setSessionMsgBridge } from '@/services/api';
import type { Session } from '@/types';
import { runBulkWithRollback, type BulkTarget } from '@/utils/bulkSessionSettings';

interface BulkMsgBridgeModalProps {
  open: boolean;
  onClose: () => void;
  /** Stable-order snapshot captured when the Actions menu opened. */
  targets: BulkTarget[];
  onApplied?: () => void;
}

interface MsgBridgeDraft {
  enabled: boolean;
  system: boolean;
  browser: boolean;
}

const EMPTY_DRAFT: MsgBridgeDraft = { enabled: false, system: false, browser: false };

/**
 * Bulk msgBridge for the select-mode Actions bar.
 *
 * Reads the representative (first stable-ordered selected Session) msgBridge
 * state, then applies the confirmed values to every selected Session through
 * the existing single-Session endpoints (which keep their expected-current /
 * CAS checks). Nothing is written before "Apply to all selected sessions";
 * a mid-run failure rolls the applied members back.
 */
export function BulkMsgBridgeModal({
  open,
  onClose,
  targets,
  onApplied,
}: BulkMsgBridgeModalProps) {
  const showToast = useUIStore((state) => state.showToast);
  const [representative, setRepresentative] = useState<Session | null>(null);
  const [draft, setDraft] = useState<MsgBridgeDraft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    const first = targets[0];
    setDraft(EMPTY_DRAFT);
    setRepresentative(null);
    setError('');
    setBusy(false);
    if (!first) return;
    let cancelled = false;
    setLoading(true);
    fetchSession(first.id)
      .then((session) => {
        if (cancelled) return;
        setRepresentative(session);
        setDraft({
          enabled: session.msgBridgeEnabled === true,
          system: session.notificationSettings?.system === true,
          browser: session.notificationSettings?.browser === true,
        });
      })
      .catch((failure) => {
        if (cancelled) return;
        setError(failure instanceof Error ? failure.message : 'Session metadata unavailable');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, targets]);

  const apply = useCallback(async () => {
    if (busy || !representative) return;
    setBusy(true);
    setError('');
    try {
      const details = await Promise.all(targets.map((target) => fetchSession(target.id)));
      const detailById = new Map(details.map((session) => [session.id, session]));
      const result = await runBulkWithRollback(targets, async (target, registerUndo) => {
        const previous = detailById.get(target.id);
        if (!previous) throw new Error('Session metadata unavailable');

        if ((previous.msgBridgeEnabled === true) !== draft.enabled) {
          await setSessionMsgBridge(target.id, draft.enabled);
          registerUndo(async () => {
            await setSessionMsgBridge(target.id, previous.msgBridgeEnabled === true);
          });
        }

        const previousSystem = previous.notificationSettings?.system === true;
        const previousBrowser = previous.notificationSettings?.browser === true;
        const notificationPatch: { system?: boolean; browser?: boolean } = {};
        if (previousSystem !== draft.system) notificationPatch.system = draft.system;
        if (previousBrowser !== draft.browser) notificationPatch.browser = draft.browser;
        if (Object.keys(notificationPatch).length > 0) {
          await patchSession(target.id, { notificationSettings: notificationPatch });
          const undoPatch: { system?: boolean; browser?: boolean } = {};
          if (notificationPatch.system !== undefined) undoPatch.system = previousSystem;
          if (notificationPatch.browser !== undefined) undoPatch.browser = previousBrowser;
          registerUndo(async () => {
            await patchSession(target.id, { notificationSettings: undoPatch });
          });
        }
      });

      if (result.error) {
        const rollbackNote = `${result.rolledBack} applied session(s) rolled back`;
        setError(
          `Bulk msgBridge stopped at ${result.failedName}: ${result.error}. ${rollbackNote}.${
            result.rollbackFailures.length > 0
              ? ` Rollback failed for ${result.rollbackFailures.join('; ')}.`
              : ''
          }`,
        );
        showToast(
          `Bulk msgBridge stopped at ${result.failedName}: ${result.error}. ${rollbackNote}.`,
          'error',
        );
      } else {
        showToast(`Updated msgBridge for ${targets.length} selected Sessions`);
        onApplied?.();
        onClose();
      }
      try {
        await useSessionStore.getState().loadSessions({ throwOnError: true });
      } catch {
        showToast('msgBridge settled, but the Session list refresh failed', 'error');
      }
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : String(failure);
      setError(message);
      showToast(message, 'error');
    } finally {
      setBusy(false);
    }
  }, [busy, representative, targets, draft, showToast, onApplied, onClose]);

  return (
    <Modal
      open={open}
      onClose={() => { if (!busy) onClose(); }}
      title="msgBridge for selected Sessions"
      size="md"
      mobileFullscreen
    >
      <div data-testid="bulk-msgbridge-modal" className="flex flex-col gap-3">
        <p className="text-xs text-text-secondary">
          Representative: {representative?.name || '…'} · {targets.length} selected Sessions.
          Values below start from the first selected Session in list order and apply to every
          selected Session after confirmation.
        </p>
        {loading && <p role="status" className="text-xs text-text-tertiary">Loading representative settings…</p>}
        {error && (
          <p role="alert" className="rounded border border-danger/30 bg-danger/10 px-2 py-1 text-xs text-danger">
            {error}
          </p>
        )}

        <label className="flex items-center gap-2 text-xs text-text-primary">
          <input
            type="checkbox"
            aria-label="msgBridge enabled"
            checked={draft.enabled}
            disabled={busy || loading}
            onChange={(event) =>
              setDraft((current) => ({ ...current, enabled: event.target.checked }))}
          />
          msgBridge enabled
        </label>
        <label className="flex items-center gap-2 text-xs text-text-primary">
          <input
            type="checkbox"
            aria-label="System completion notifications"
            checked={draft.system}
            disabled={busy || loading || !draft.enabled}
            onChange={(event) =>
              setDraft((current) => ({ ...current, system: event.target.checked }))}
          />
          System completion notifications
        </label>
        <label className="flex items-center gap-2 text-xs text-text-primary">
          <input
            type="checkbox"
            aria-label="Browser completion notifications"
            checked={draft.browser}
            disabled={busy || loading || !draft.enabled}
            onChange={(event) =>
              setDraft((current) => ({ ...current, browser: event.target.checked }))}
          />
          Browser completion notifications
        </label>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" size="sm" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="bulk-apply"
            disabled={busy || loading || !representative}
            onClick={() => void apply()}
          >
            Apply to all selected sessions
          </Button>
        </div>
      </div>
    </Modal>
  );
}
