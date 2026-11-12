import { settingsText } from '@/components/layout/settingsCatalog';
import { useEffect, useState } from 'react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { lockerRequest } from '@/views/lockerModel';

interface ErrorType { id: string; label: string }

/** Both entrances edit the same committed store value; failed writes change nothing. */
export function ErrorNotificationExclusions({ scope = '' }: { scope?: string }) {
  const value = useAppSettingsStore((s) => s.errorNotificationExclusions);
  const save = useAppSettingsStore((s) => s.saveErrorNotificationExclusions);
  const [catalog, setCatalog] = useState<ErrorType[]>([]);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void lockerRequest<{ types: ErrorType[] }>('/api/notifications/error-types', 'GET', undefined, controller.signal)
      .then((result) => setCatalog(result.types))
      .catch((reason) => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, []);
  const update = async (next: typeof value) => {
    setBusy(true); setError('');
    try { await save(next); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save exclusions'); }
    finally { setBusy(false); }
  };
  const filtered = catalog.filter((row) => `${row.label} ${row.id}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <div data-testid="error-notification-exclusions" className="space-y-2 rounded border border-border-muted p-2 text-xs">
    <label className="flex items-center gap-2" data-settings-id={scope + 'error-notification-exclusions.1'}><input type="checkbox" checked={value.enabled} disabled={busy}
      onChange={(event) => { void update({ ...value, enabled: event.target.checked }); }} />{settingsText(scope + 'error-notification-exclusions.1')}</label>
    <p className="text-[11px] text-text-secondary" data-settings-id={scope + 'error-notification-exclusions.2'}>{settingsText(scope + 'error-notification-exclusions.2')}</p>
    <input aria-label={settingsText(scope + 'error-notification-exclusions.3')} type="search" value={query} onChange={(event) => setQuery(event.target.value)}
      className="w-full rounded border border-border-default bg-bg-primary px-2 py-1" data-settings-id={scope + 'error-notification-exclusions.3'} />
    <p data-settings-id={scope + 'error-notification-exclusions.4'}>{value.types.length} {settingsText(scope + 'error-notification-exclusions.4')}{filtered.length} {settingsText(scope + 'error-notification-exclusions.5')}{catalog.length} {settingsText(scope + 'error-notification-exclusions.6')}</p>
    <div aria-label={settingsText(scope + 'error-notification-exclusions.7')} className="h-40 overflow-y-auto space-y-1" style={{ height: 160, overflowY: 'auto' }} data-settings-id={scope + 'error-notification-exclusions.7'}>
      {filtered.map((row) => <label key={row.id} className="flex items-start gap-2 py-1 break-all">
        <input type="checkbox" disabled={busy} checked={value.types.includes(row.id)} onChange={(event) => {
          void update({ ...value, types: event.target.checked ? [...value.types, row.id] : value.types.filter((id) => id !== row.id) });
        }} />{row.label}
      </label>)}
      {!filtered.length && <p data-settings-id={scope + 'error-notification-exclusions.8'}>{settingsText(scope + 'error-notification-exclusions.8')}</p>}
    </div>
    {error && <p role="alert" className="text-danger">{error}</p>}
  </div>;
}
