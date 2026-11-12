import { useSettingsAvailability } from './settingsAvailability';
import { settingsText } from '@/components/layout/settingsCatalog';
import { useEffect, useState } from 'react';
import { fetchPluginMessageSources } from '@/services/api';
import type { PluginMessageSource } from '@/services/api';
import {
  DEFAULT_AUTO_REPORT, DEFAULT_AUTO_REPORT_SUMMARY, useAppSettingsStore,
} from '@/stores/appSettingsStore';
import type { AutoReportSettings, AutoReportSourceFlags } from '@/stores/appSettingsStore';

const CORE_SOURCES = ['system', 'ma', 'user'] as const;

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
  const summary: AutoReportSourceFlags = policy.summary;
  const applySummary = (next: AutoReportSourceFlags) => apply({ ...policy, summary: next });
  useEffect(() => { if (sources) useSettingsAvailability.setState({pluginSources: sources}); }, [sources]);
  return <>
    <section aria-label={settingsText("auto-report")} data-settings-id="auto-report">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="auto-report-settings-group.1">{settingsText("auto-report-settings-group.1")}</h3>
      <p className="text-xs text-text-secondary mb-2" data-settings-id="auto-report-settings-group.2">{settingsText("auto-report-settings-group.2")}</p>
      <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
        {CORE_SOURCES.map((key) =>
          <label data-settings-id={'auto-report.' + key} key={key} className="flex items-center justify-between px-3 py-2 text-sm">
            {settingsText('auto-report.' + key)}<input type="checkbox" aria-label={`Auto report ${settingsText('auto-report.' + key)}`} checked={policy[key]} disabled={busy}
              onChange={(e) => { void apply({ ...policy, [key]: e.target.checked }); }} />
          </label>)}
        {sources?.map((source) => <label data-settings-id={'auto-report.plugin.' + source.id} key={source.id} className="flex items-center justify-between px-3 py-2 text-sm">
          {source.label}<input type="checkbox" aria-label={`Auto report plugin ${source.label}`} disabled={busy}
            checked={policy.plugins[source.id] ?? source.defaultEnabled}
            onChange={(e) => { void apply({ ...policy, plugins: { ...policy.plugins, [source.id]: e.target.checked } }); }} />
        </label>)}
      </div>
      {sources === null && !error && <p className="text-xs text-text-tertiary" data-settings-id="auto-report-settings-group.3">{settingsText("auto-report-settings-group.3")}</p>}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      <button type="button" disabled={busy} className="mt-2 text-xs text-accent" onClick={() => {
        void apply({
          ...policy,
          system: DEFAULT_AUTO_REPORT.system,
          ma: DEFAULT_AUTO_REPORT.ma,
          user: DEFAULT_AUTO_REPORT.user,
          plugins: {},
        });
      }} data-settings-id="auto-report-settings-group.4">{settingsText("auto-report-settings-group.4")}</button>
    </section>
    <section aria-label={settingsText("prompt-summary")} className="mt-4" data-settings-id="prompt-summary">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2" data-settings-id="auto-report-settings-group.5">{settingsText("auto-report-settings-group.5")}</h3>
      <p className="text-xs text-text-secondary mb-2" data-settings-id="auto-report-settings-group.6">{settingsText("auto-report-settings-group.6")}</p>
      <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
        {CORE_SOURCES.map((key) =>
          <label data-settings-id={'prompt-summary.' + key} key={key} className="flex items-center justify-between px-3 py-2 text-sm">
            {settingsText('prompt-summary.' + key)}<input type="checkbox" aria-label={`Prompt summary ${settingsText('prompt-summary.' + key)}`} checked={summary[key]} disabled={busy}
              onChange={(e) => { void applySummary({ ...summary, [key]: e.target.checked }); }} />
          </label>)}
        {sources?.map((source) => <label data-settings-id={'prompt-summary.plugin.' + source.id} key={source.id} className="flex items-center justify-between px-3 py-2 text-sm">
          {source.label}<input type="checkbox" aria-label={`Prompt summary plugin ${source.label}`} disabled={busy}
            checked={summary.plugins[source.id] ?? source.effectiveSummaryEnabled}
            onChange={(e) => { void applySummary({ ...summary, plugins: { ...summary.plugins, [source.id]: e.target.checked } }); }} />
        </label>)}
      </div>
      <button type="button" disabled={busy} className="mt-2 text-xs text-accent" onClick={() => {
        void applySummary({ ...DEFAULT_AUTO_REPORT_SUMMARY, plugins: {} });
      }} data-settings-id="auto-report-settings-group.7">{settingsText("auto-report-settings-group.7")}</button>
    </section>
  </>;
}
