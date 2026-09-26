import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, Database, LoaderCircle } from 'lucide-react';
import {
  fetchCompletedJobRetentionSettings,
  type JobRetentionRule,
  type JobRetentionRules,
} from '@/services/api';

const RULES: Array<{ key: JobRetentionRule; label: string }> = [
  { key: 'completed', label: 'Completed Jobs' },
  { key: 'failed', label: 'Failed Jobs' },
  { key: 'timed_out', label: 'Timed out Jobs' },
  { key: 'cancelled', label: 'Cancelled Jobs' },
  { key: 'logs', label: 'Job log files' },
];

export default function DataView() {
  const [rules, setRules] = useState<JobRetentionRules | null>(null);
  const [configValidity, setConfigValidity] = useState<Record<JobRetentionRule, boolean> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchCompletedJobRetentionSettings()
      .then((response) => {
        if (!active) return;
        setRules(response.rules);
        setConfigValidity(response.configValidity);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : 'Failed to load retention settings.');
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col bg-bg-primary">
      <header className="shrink-0 border-b border-border-default bg-bg-secondary/50 px-4 py-3">
        <div className="flex items-center gap-2">
          <Database size={16} className="text-text-secondary" />
          <h1 className="text-sm font-semibold text-text-primary">Data</h1>
        </div>
        <p className="mt-1 text-xs leading-5 text-text-secondary">
          Read-only view of the Jobs retention settings from the shared Jobs configuration.
          Blank day counts prevent a rule from running, even when enabled.
        </p>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto p-4">
        <section className="mx-auto w-full max-w-3xl rounded-lg border border-border-default bg-bg-secondary p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-text-primary">Jobs retention</h2>
            <Link
              to="/jobs"
              className="text-xs text-accent hover:underline"
            >
              Open Jobs settings
            </Link>
          </div>

          {error && (
            <div role="alert" className="flex items-start gap-2 rounded border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-500">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>Could not load shared Jobs settings: {error}</span>
            </div>
          )}

          {!error && !rules && (
            <div role="status" className="flex items-center gap-2 py-4 text-xs text-text-secondary">
              <LoaderCircle size={14} className="animate-spin" />
              Loading Jobs retention settings…
            </div>
          )}

          {rules && (
            <div className="divide-y divide-border-muted">
              {RULES.map(({ key, label }) => {
                const setting = rules[key];
                const valid = configValidity?.[key] !== false;
                let summary = 'Disabled';
                if (!valid) summary = 'Invalid config; automatic cleanup is disabled';
                else if (setting.enabled && setting.days === null) summary = 'Enabled, but no day count; this rule will not run';
                else if (setting.enabled && setting.days !== null) summary = `Enabled · keep ${setting.days} days`;

                return (
                  <div key={key} className="flex flex-wrap items-center justify-between gap-2 py-3">
                    <span className="text-sm text-text-primary">{label}</span>
                    <span className="text-xs text-text-secondary">{summary}</span>
                  </div>
                );
              })}
            </div>
          )}

          <p className="mt-3 border-t border-border-muted pt-3 text-[11px] leading-5 text-text-tertiary">
            This page only displays the values returned by the Jobs settings API. Change them in Jobs → Settings.
            Job records, log files, and run history use separate retention rules.
          </p>
        </section>
      </main>
    </div>
  );
}
