import { QqContactName, QqContactSearch } from '@/components/shared/QqContactSearch';
import { matchesQqContact } from '@/utils/qqContactSearch';
import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchQqChannels, fetchQqContacts } from '@/services/api';
import { sanitizeSourceNotification, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { SourceNotificationSettings, NotificationSource } from '@/stores/appSettingsStore';
import { lockerNotificationPermission, requestLockerNotificationPermission } from '@/services/lockerDesktopNotify';

interface ContactOption { target: string; name: string }

const SAVED_NOTICE = 'Saved. Only new events are forwarded; historical events are not replayed.';

/** A source needs at least one QQ contact once it is enabled with QQ Report. */
function isInvalidSource(value: SourceNotificationSettings) {
  return value.enabled && value.qqReport && value.qqTargets.length === 0;
}

/** Skip a write that would only restate the committed value (and rotate its epoch). */
function sameSettings(a: SourceNotificationSettings, b: SourceNotificationSettings) {
  return a.enabled === b.enabled && a.system === b.system && a.browser === b.browser
    && a.qqReport === b.qqReport && a.qqTargets.length === b.qqTargets.length
    && a.qqTargets.every((target, index) => target === b.qqTargets[index]);
}

export function SourceNotificationPanel({ source }: { source: NotificationSource }) {
  const saved = useAppSettingsStore((s) => s.notifications[source]);
  const save = useAppSettingsStore((s) => s.saveSourceNotification);
  const [draft, setDraft] = useState(() => sanitizeSourceNotification(saved));
  const [query, setQuery] = useState('');
  const [contacts, setContacts] = useState<ContactOption[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [contactError, setContactError] = useState('');
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');
  const [permission, setPermission] = useState(lockerNotificationPermission);
  const live = useRef(true);
  const edited = useRef(false);
  /** Latest draft awaiting persistence; only the newest value is written after the in-flight save. */
  const pending = useRef<SourceNotificationSettings | null>(null);
  const saving = useRef(false);

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  useEffect(() => {
    if (!edited.current) setDraft(sanitizeSourceNotification(saved));
  }, [saved]);

  useEffect(() => {
    if (!draft.qqReport) return;
    let cancelled = false;
    setLoading(true);
    setContactError('');
    void (async () => {
      try {
        const bots = await fetchQqChannels();
        const connected = bots.filter((bot) => bot.connected);
        if (connected.length === 0) throw new Error('QQ bots are unavailable. Saved contacts remain listed; reconnect the bot before forwarding notifications.');
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
  }, [draft.qqReport]);

  const options = useMemo(() => {
    const rows = new Map(contacts.map((row) => [row.target, row]));
    for (const target of draft.qqTargets) {
      if (!rows.has(target)) rows.set(target, { target, name: 'Saved contact (reconnect bot before sending)' });
    }
    return [...rows.values()];
  }, [contacts, draft.qqTargets]);

  const filtered = options.filter((row) => matchesQqContact(query, row.name, row.target));
  const selected = options.filter((row) => draft.qqTargets.includes(row.target));

  const needsContacts = draft.qqReport;
  const invalid = isInvalidSource(draft);

  /** Write queued drafts one at a time so a fast sequence can never land out of order. */
  const persist = async () => {
    if (saving.current) return;
    saving.current = true;
    setBusy(true);
    try {
      while (pending.current) {
        const value = pending.current;
        pending.current = null;
        await save(source, value);
        if (!live.current) return;
        if (!pending.current) edited.current = false;
        setError('');
        setNotice(SAVED_NOTICE);
      }
    } catch (reason) {
      // Keep the draft the user asked for; nothing has been dropped, and the next edit
      // re-sends the whole desired state through the same save path.
      pending.current = null;
      if (live.current) {
        setNotice('');
        setError(reason instanceof Error ? reason.message : 'Defaults could not be saved');
      }
    } finally {
      saving.current = false;
      if (live.current) setBusy(false);
    }
  };

  const update = (patch: Partial<SourceNotificationSettings>) => {
    const next = { ...draft, ...patch };
    edited.current = true;
    setDraft(next);
    setError('');
    setNotice('');
    // An invalid combination stays visible as a draft; it is persisted once it becomes valid.
    if (isInvalidSource(next)) return;
    if (sameSettings(next, sanitizeSourceNotification(saved))) { edited.current = false; return; }
    pending.current = next;
    void persist();
  };

  return (
    <div data-testid={`notification-source-${source}`} className="mb-5 rounded-md border border-border-muted bg-bg-primary p-3 space-y-3">
      <h3 className="text-xs font-semibold text-text-primary">{source === 'lockerUnread' ? 'Locker new unread' : 'New errors'}</h3>
      <p className="text-[11px] text-text-secondary">Forward new events from the server, including while the browser is closed. Matching Session and global routes are delivered once per event and target.</p>
      <div className="flex flex-wrap gap-4 text-xs">
        {([['enabled', 'Enabled'], ['system', 'System'], ['browser', 'Browser'], ['qqReport', 'QQ Report']] as const).map(([key, label]) => (
          <label key={key} className="flex items-center gap-2">
            <input type="checkbox" checked={draft[key]} disabled={busy} onChange={(e) => update({ [key]: e.target.checked })} />{label}
          </label>
        ))}
      </div>
      <p className="text-[11px] text-text-tertiary">Changes are saved as soon as you make them. Browser requires notification permission and an authenticated open page. System and QQ Report run on the server. QQ forwards notifications only.</p>
      {draft.browser && <div className="flex items-center gap-2 text-xs">
        <button type="button" disabled={permission === 'unsupported'} className="rounded border border-border-default px-2 py-1"
          onClick={async () => setPermission(await requestLockerNotificationPermission())}>Request browser permission</button>
        <span>Browser permission: {permission}</span>
      </div>}
      {needsContacts && <div className="space-y-2">
        <div className="text-xs text-text-primary">QQ Report contacts · {draft.qqTargets.length} selected</div>
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
            <input type="checkbox" className="mt-0.5 shrink-0" disabled={busy} checked={draft.qqTargets.includes(row.target)}
              aria-describedby={invalid ? `${source}-error` : undefined}
              onChange={(e) => update({ qqTargets: e.target.checked ? [...draft.qqTargets, row.target] : draft.qqTargets.filter((target) => target !== row.target) })} />
            <span className="min-w-0"><QqContactName name={row.name} number={row.target} /><span className="block break-all text-[10px] text-text-tertiary">{row.target}</span></span>
          </label>)}
        </div>
        {invalid && <p id={`${source}-error`} role="alert" className="text-xs text-danger">Select at least one QQ Report contact. That change was not saved.</p>}
      </div>}
      {source === 'newError' && <ErrorNotificationExclusions />}
      {busy && <p role="status" className="text-xs text-text-secondary">Saving…</p>}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      {notice && <p role="status" className="text-xs text-text-secondary">{notice}</p>}
    </div>
  );
}
import { ErrorNotificationExclusions } from './ErrorNotificationExclusions';
