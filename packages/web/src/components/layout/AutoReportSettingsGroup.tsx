import { useEffect, useState } from 'react';
import { fetchPluginMessageSources } from '@/services/api';
import type { PluginMessageSource } from '@/services/api';
import { DEFAULT_AUTO_REPORT, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { AutoReportSettings } from '@/stores/appSettingsStore';

export function AutoReportSettingsGroup() {
  const policy = useAppSettingsStore((s) => s.autoReport);
  const save = useAppSettingsStore((s) => s.saveAutoReport);
  const [sources, setSources] = useState<PluginMessageSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    void fetchPluginMessageSources().then((data) => {
      if (active) setSources(data.sources);
    }).catch((err: unknown) => { if (active) setError(String(err)); });
    return () => { active = false; };
  }, []);
  async function apply(next: AutoReportSettings) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try { await save(next); }
    catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  }
  return <section aria-label="Auto report">
    <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">Auto report</h3>
    <p className="text-xs text-text-secondary mb-2">Automatically report answers to a subscribed manager based on what started the execution. Manual reports and messages remain available.</p>
    <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
      {([['system', 'System'], ['ma', 'MA'], ['user', 'Real user']] as const).map(([key, label]) =>
        <label key={key} className="flex items-center justify-between px-3 py-2 text-sm">
          {label}<input type="checkbox" aria-label={`Auto report ${label}`} checked={policy[key]} disabled={busy}
            onChange={(e) => { void apply({ ...policy, [key]: e.target.checked }); }} />
        </label>)}
      {sources?.map((source) => <label key={source.id} className="flex items-center justify-between px-3 py-2 text-sm">
        {source.label}<input type="checkbox" aria-label={`Auto report plugin ${source.label}`} disabled={busy}
          checked={policy.plugins[source.id] ?? source.defaultEnabled}
          onChange={(e) => { void apply({ ...policy, plugins: { ...policy.plugins, [source.id]: e.target.checked } }); }} />
      </label>)}
    </div>
    {sources === null && !error && <p className="text-xs text-text-tertiary">Loading plugin message sources…</p>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    <button type="button" disabled={busy} className="mt-2 text-xs text-accent" onClick={() => { void apply({ ...DEFAULT_AUTO_REPORT, plugins: {} }); }}>Reset auto report defaults</button>
  </section>;
}
