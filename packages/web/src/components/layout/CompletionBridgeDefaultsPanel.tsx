import { QqContactName, QqContactSearch } from '@/components/shared/QqContactSearch';
import { matchesQqContact } from '@/utils/qqContactSearch';
import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchQqChannels, fetchQqContacts } from '@/services/api';
import { sanitizeCompletionBridge, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { CompletionBridgeDefaults } from '@/stores/appSettingsStore';

interface ContactOption { target: string; name: string }

export function CompletionBridgeDefaultsPanel() {
  const saved = useAppSettingsStore((s) => s.notifications.completionBridge);
  const save = useAppSettingsStore((s) => s.saveCompletionBridge);
  const [draft, setDraft] = useState(() => sanitizeCompletionBridge(saved));
  const [query, setQuery] = useState('');
  const [contacts, setContacts] = useState<ContactOption[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [contactError, setContactError] = useState('');
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');
  const live = useRef(true);
  const edited = useRef(false);

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  useEffect(() => {
    if (!edited.current) setDraft(sanitizeCompletionBridge(saved));
  }, [saved]);

  const needsContacts = draft.qqReport || draft.qqSubscribe;
  useEffect(() => {
    if (!needsContacts) return;
    let cancelled = false;
    setLoading(true);
    setContactError('');
    void (async () => {
      try {
        const bots = await fetchQqChannels();
        const connected = bots.filter((bot) => bot.connected);
        if (connected.length === 0) throw new Error('QQ bots are unavailable. Saved contacts remain listed; reconnect the bot before lighting the bell.');
        const rows: ContactOption[] = [];
        let failed = false;
        for (const bot of connected) {
          try {
            const list = await fetchQqContacts(bot.bot_uin || undefined);
            for (const contact of list) {
              const base = `${contact.chatType === 2 ? 'group' : 'user'}:${contact.peerUin}`;
              rows.push({ target: bot.bot_uin ? `${base}@${bot.bot_uin}` : base, name: contact.peerName || contact.peerUin });
            }
          } catch { failed = true; }
        }
        if (!cancelled) {
          setContacts([...new Map(rows.map((row) => [row.target, row])).values()]);
          if (failed) setContactError('Some bot contacts could not load. Saved contacts remain removable.');
        }
      } catch (reason) {
        if (!cancelled) setContactError(reason instanceof Error ? reason.message : 'QQ contacts could not load');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [needsContacts]);

  const options = useMemo(() => {
    const rows = new Map(contacts.map((row) => [row.target, row]));
    for (const target of draft.qqTargets) {
      if (!rows.has(target)) rows.set(target, { target, name: 'Saved contact (availability checked when the bell is enabled)' });
    }
    return [...rows.values()];
  }, [contacts, draft.qqTargets]);

  const filtered = options.filter((row) => matchesQqContact(query, row.name, row.target));
  const selected = options.filter((row) => draft.qqTargets.includes(row.target));

  const invalid = needsContacts && draft.qqTargets.length === 0;
  const update = (patch: Partial<CompletionBridgeDefaults>) => {
    edited.current = true;
    setDraft((current) => ({ ...current, ...patch }));
    setError('');
    setNotice('');
  };
  const submit = async () => {
    if (busy || invalid) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await save(draft);
      if (live.current) {
        edited.current = false;
        setNotice('Saved. These defaults apply the next time an unlit bell is enabled.');
      }
    } catch (reason) {
      if (live.current) setError(reason instanceof Error ? reason.message : 'Defaults could not be saved');
    } finally {
      if (live.current) setBusy(false);
    }
  };

  return (
    <div className="mb-5 rounded-md border border-border-muted bg-bg-primary p-3 space-y-3">
      <h3 className="text-xs font-semibold text-text-primary">Completion bell defaults</h3>
      <p className="text-[11px] text-text-secondary">Choose one or more connections to start when an unlit Session bell is enabled. Editing defaults preserves all running connections.</p>
      <div className="flex flex-wrap gap-4 text-xs">
        {([['system', 'System'], ['browser', 'Browser'], ['qqReport', 'QQ Report'], ['qqSubscribe', 'QQ Subscribe']] as const).map(([key, label]) => (
          <label key={key} className="flex items-center gap-2">
            <input type="checkbox" checked={draft[key]} disabled={busy} onChange={(e) => update({ [key]: e.target.checked })} />{label}
          </label>
        ))}
      </div>
      <p className="text-[11px] text-text-tertiary">Browser delivery also requires browser notification permission. QQ Report sends task results; QQ Subscribe receives inbox reminders.</p>
      {needsContacts && <div className="space-y-2">
        <div className="text-xs text-text-primary">Default QQ contacts · {draft.qqTargets.length} selected</div>
        {loading && <p role="status" className="text-xs text-text-secondary">Loading contacts…</p>}
        {contactError && <p className="text-xs text-danger" role="status">{contactError}</p>}
        <QqContactSearch value={query} onChange={setQuery} disabled={busy} />
        {selected.length > 0 && <div className="flex flex-wrap gap-1" aria-label="Selected QQ contacts">
          {selected.map((row) => <button key={row.target} type="button" disabled={busy}
            aria-label={`Remove ${row.name} (${row.target})`}
            onClick={() => update({ qqTargets: draft.qqTargets.filter((target) => target !== row.target) })}
            className="max-w-full rounded border border-border-default px-2 py-1 text-left text-[11px] break-words disabled:opacity-50">
            {row.name} · {row.target} ×
          </button>)}
        </div>}
        <div className="max-h-48 overflow-y-auto space-y-1">
          {filtered.length === 0 && !loading && <p role="status" className="py-2 text-xs text-text-tertiary">{query.trim() ? 'No contacts match this search.' : 'No QQ contacts available.'}</p>}
          {filtered.map((row) => <label key={row.target} className="flex min-w-0 items-start gap-2 rounded py-1 text-xs">
            <input type="checkbox" className="mt-0.5 shrink-0" disabled={busy} checked={draft.qqTargets.includes(row.target)} onChange={(e) => update({ qqTargets: e.target.checked ? [...draft.qqTargets, row.target] : draft.qqTargets.filter((target) => target !== row.target) })} />
            <span className="min-w-0"><QqContactName name={row.name} number={row.target} /><span className="block break-all text-[10px] text-text-tertiary">{row.target}</span></span>
          </label>)}
        </div>
        {invalid && <p id="completion-defaults-error" role="alert" className="text-xs text-danger">Select at least one QQ contact before saving QQ Report or Subscribe as a default.</p>}
      </div>}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      {notice && <p role="status" className="text-xs text-text-secondary">{notice}</p>}
      <button type="button" disabled={busy || invalid} aria-describedby={invalid ? 'completion-defaults-error' : undefined} onClick={() => { void submit(); }} className="rounded border border-border-default px-3 py-1.5 text-xs disabled:opacity-50">{busy ? 'Saving…' : 'Save completion defaults'}</button>
    </div>
  );
}
