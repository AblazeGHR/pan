import { useEffect, useState } from 'react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { lockerRequest } from '@/views/lockerModel';

interface ErrorType { id: string; label: string }

/** Both entrances edit the same committed store value; failed writes change nothing. */
export function ErrorNotificationExclusions() {
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
    <label className="flex items-center gap-2"><input type="checkbox" checked={value.enabled} disabled={busy}
      onChange={(event) => { void update({ ...value, enabled: event.target.checked }); }} />Enable error notification exclusions</label>
    <p className="text-[11px] text-text-secondary">Shared by Behavior and Notifications. Applies only to future error notifications; error history and manager reports remain. Turning exclusions off retains selections. A selected Pan kind or provider type excludes the event. Errors without a selected match are forwarded.</p>
    <input aria-label="Search error types" type="search" value={query} onChange={(event) => setQuery(event.target.value)}
      className="w-full rounded border border-border-default bg-bg-primary px-2 py-1" />
    <p>{value.types.length} selected · {filtered.length} of {catalog.length} types</p>
    <div aria-label="Error types" className="h-40 overflow-y-auto space-y-1" style={{ height: 160, overflowY: 'auto' }}>
      {filtered.map((row) => <label key={row.id} className="flex items-start gap-2 py-1 break-all">
        <input type="checkbox" disabled={busy} checked={value.types.includes(row.id)} onChange={(event) => {
          void update({ ...value, types: event.target.checked ? [...value.types, row.id] : value.types.filter((id) => id !== row.id) });
        }} />{row.label}
      </label>)}
      {!filtered.length && <p>No error types match.</p>}
    </div>
    {error && <p role="alert" className="text-danger">{error}</p>}
  </div>;
}
