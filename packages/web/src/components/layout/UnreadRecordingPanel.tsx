import { useEffect, useRef, useState } from 'react';
import {
  sanitizeUnreadRecording,
  toggleUnreadRecordingScope,
  useAppSettingsStore,
} from '@/stores/appSettingsStore';
import type { UnreadRecordingScope } from '@/stores/appSettingsStore';
import { reapplyUnreadRecordingSettings } from '@/services/api';

const SCOPE_ROWS: Array<{ scope: UnreadRecordingScope; label: string; hint: string }> = [
  { scope: 'all', label: 'All sessions', hint: 'Every Session records done unread (legacy default).' },
  { scope: 'none', label: 'No sessions', hint: 'No Session records unread anywhere; excluded Sessions stay at 0.' },
  { scope: 'bell-on', label: 'Bell on', hint: 'Sessions whose completion bell (msgBridge) is on.' },
  { scope: 'bell-off', label: 'Bell off', hint: 'Sessions whose completion bell (msgBridge) is off.' },
  { scope: 'pinned', label: 'Pinned', hint: 'Every pinned Session (Session pin, not Job pins).' },
];

/**
 * App Settings → Notification → Unread recording.
 *
 * The persisted scope set decides which Sessions record done unread at all.
 * Excluded Sessions keep an authoritative count 0 on every surface; a newly
 * included Session counts from new completions only. The save resolves only
 * after the server wrote the choice, so a failure never shows as saved.
 */
export function UnreadRecordingPanel() {
  const saved = useAppSettingsStore((s) => s.notifications.unreadRecording);
  const save = useAppSettingsStore((s) => s.saveUnreadRecording);
  const [draft, setDraft] = useState(() => sanitizeUnreadRecording(saved));
  const [busy, setBusy] = useState(false);
  const [reapplying, setReapplying] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const live = useRef(true);
  const edited = useRef(false);

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  useEffect(() => {
    if (!edited.current) setDraft(sanitizeUnreadRecording(saved));
  }, [saved]);

  const toggle = (scope: UnreadRecordingScope) => {
    edited.current = true;
    setDraft((current) => toggleUnreadRecordingScope(current, scope));
    setError('');
    setNotice('');
  };
  const dirty = draft.join(',') !== sanitizeUnreadRecording(saved).join(',');
  const submit = async () => {
    if (busy || !dirty) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await save(draft);
      if (live.current) {
        edited.current = false;
        setNotice('Saved. Excluded Sessions keep no unread; re-included Sessions count from new completions only.');
      }
    } catch (reason) {
      if (live.current) setError(reason instanceof Error ? reason.message : 'Unread recording could not be saved');
    } finally {
      if (live.current) setBusy(false);
    }
  };
  // Re-apply the persisted policy even when nothing changed: clears every
  // per-Session override (Session menu "Enable unread tracking") and aligns
  // excluded Sessions again.
  const submitReapply = async () => {
    if (busy || reapplying) return;
    setReapplying(true);
    setError('');
    setNotice('');
    try {
      const result = await reapplyUnreadRecordingSettings();
      if (live.current) {
        const failed = result.failedSessions?.length ?? 0;
        setNotice(
          result.superseded
            ? 'A newer settings apply superseded this reapply.'
            : failed > 0
              ? `Settings reapplied; ${failed} Session(s) could not be aligned and will retry.`
              : 'Settings reapplied. Per-Session overrides were cleared.',
        );
      }
    } catch (reason) {
      if (live.current) setError(reason instanceof Error ? reason.message : 'Settings could not be reapplied');
    } finally {
      if (live.current) setReapplying(false);
    }
  };

  return (
    <div className="mb-5 rounded-md border border-border-muted bg-bg-primary p-3 space-y-3" data-testid="unread-recording-panel">
      <h3 className="text-xs font-semibold text-text-primary">Unread recording</h3>
      <p className="text-[11px] text-text-secondary">
        Choose which Sessions record done unread. Excluded Sessions stay at zero on every client,
        and re-including one counts from new completions only — older unread is never restored.
      </p>
      <div className="space-y-2 text-xs" role="group" aria-label="Unread recording scopes">
        {SCOPE_ROWS.map((row) => (
          <label key={row.scope} className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={draft.includes(row.scope)}
              disabled={busy}
              data-testid={`unread-recording-${row.scope}`}
              onChange={() => toggle(row.scope)}
            />
            <span className="min-w-0">
              <span className="block text-text-primary">{row.label}</span>
              <span className="block text-[10px] text-text-tertiary">{row.hint}</span>
            </span>
          </label>
        ))}
      </div>
      <p className="text-[11px] text-text-tertiary">
        Categories combine as a union; Bell on and Bell off are mutually exclusive. Choosing a
        category clears All / No sessions, and choosing All / No sessions clears the categories.
        A Session menu can enable tracking for one Session only; saving here or reapplying covers
        those per-Session choices.
      </p>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      {notice && <p role="status" className="text-xs text-text-secondary">{notice}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || !dirty}
          onClick={() => { void submit(); }}
          data-testid="unread-recording-save"
          className="rounded border border-border-default px-3 py-1.5 text-xs disabled:opacity-50"
        >
          {busy ? 'Saving…' : 'Save unread recording'}
        </button>
        <button
          type="button"
          disabled={busy || reapplying}
          onClick={() => { void submitReapply(); }}
          data-testid="unread-recording-reapply"
          className="rounded border border-border-default px-3 py-1.5 text-xs disabled:opacity-50"
        >
          {reapplying ? 'Reapplying…' : 'Reapply settings'}
        </button>
      </div>
    </div>
  );
}
