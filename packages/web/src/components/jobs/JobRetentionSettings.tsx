import { useCallback, useEffect, useState } from 'react';
import {
  fetchCompletedJobRetentionSettings,
  updateCompletedJobRetentionSettings,
} from '@/services/api';
import type {
  CompletedJobRetentionRun,
  JobRetentionRule,
  JobRetentionRules,
} from '@/services/api';

const RETENTION_RULE_META: Array<{
  key: JobRetentionRule;
  label: string;
  description: string;
}> = [
  {
    key: 'completed',
    label: 'Completed Jobs',
    description: 'Deletes only Jobs whose top-level status is exactly completed and whose updatedAt is old enough.',
  },
  {
    key: 'failed',
    label: 'Failed Jobs',
    description: 'Deletes only Jobs whose top-level status is exactly failed and whose updatedAt is old enough.',
  },
  {
    key: 'timed_out',
    label: 'Timed out Jobs',
    description: 'Deletes only Jobs whose top-level status is exactly timed_out and whose updatedAt is old enough.',
  },
  {
    key: 'cancelled',
    label: 'Cancelled Jobs',
    description: 'Deletes only Jobs whose top-level status is exactly cancelled and whose updatedAt is old enough.',
  },
  {
    key: 'logs',
    label: 'Job log files',
    description: 'Uses each uniquely owned regular log file’s last-modified time inside the Pan Jobs logs directory.',
  },
];

const DEFAULT_RETENTION_RULES: JobRetentionRules = {
  completed: { enabled: false, days: null },
  failed: { enabled: false, days: null },
  timed_out: { enabled: false, days: null },
  cancelled: { enabled: false, days: null },
  logs: { enabled: false, days: null },
};

const selectClass =
  'w-full bg-bg-tertiary border border-border-default rounded text-xs py-1.5 px-2 text-text-primary outline-none focus:border-accent/50';

const errMsg = (error: unknown) => (error instanceof Error ? error.message : 'Request failed');

function cloneRules(rules: JobRetentionRules): JobRetentionRules {
  return {
    completed: { ...rules.completed },
    failed: { ...rules.failed },
    timed_out: { ...rules.timed_out },
    cancelled: { ...rules.cancelled },
    logs: { ...rules.logs },
  };
}

/** Shared Jobs retention editor used by Jobs Settings and App Settings Data. */
export function JobRetentionSettings() {
  const [rules, setRules] = useState<JobRetentionRules | null>(null);
  const [draft, setDraft] = useState<JobRetentionRules>(DEFAULT_RETENTION_RULES);
  const [lastRuns, setLastRuns] = useState<Record<
    JobRetentionRule,
    CompletedJobRetentionRun | null
  > | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchCompletedJobRetentionSettings();
      setRules(result.rules);
      setDraft(cloneRules(result.rules));
      setLastRuns(result.lastRuns);
      if (!result.configValid) {
        const invalidRules = RETENTION_RULE_META
          .filter(({ key }) => !result.configValidity[key])
          .map(({ label }) => label);
        setError(`Invalid saved values for ${invalidRules.join(', ')}. Those rules remain disabled until valid values are saved.`);
      }
    } catch (reason) {
      setError(errMsg(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const saveSettings = useCallback(async () => {
    for (const { key, label } of RETENTION_RULE_META) {
      const days = draft[key].days;
      if (days !== null && (!Number.isInteger(days) || days < 1 || days > 36500)) {
        setError(`${label} keep days must be blank or a whole number from 1 to 36500.`);
        setFeedback(null);
        return;
      }
    }

    setSaving(true);
    setError(null);
    setFeedback(null);
    try {
      const result = await updateCompletedJobRetentionSettings(draft);
      setRules(result.rules);
      setDraft(cloneRules(result.rules));
      setLastRuns(result.lastRuns);
      if (!result.configValid) {
        setError('Some saved retention values are invalid. Automatic cleanup remains disabled for those rules.');
      } else {
        setFeedback('Settings saved. The server applies changes without a restart.');
      }
    } catch (reason) {
      setError(errMsg(reason));
    } finally {
      setSaving(false);
    }
  }, [draft]);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-semibold text-text-primary">Automatic cleanup settings</h2>
        <p className="mt-1 text-xs leading-5 text-text-secondary">
          Each rule is independent and disabled by default. Rules with a day count run on the server
          at most once every 24 hours without requiring a browser page to stay open. A blank day count
          prevents cleanup even when a switch is on. Job record rules use the exact top-level status
          and updatedAt; missing or invalid timestamps are kept. Logs and Job records are retained independently.
        </p>
      </div>

      {loading ? (
        <p className="text-xs text-text-tertiary" role="status">Loading settings…</p>
      ) : (
        <>
          {RETENTION_RULE_META.map(({ key, label, description }) => {
            const rule = draft[key];
            const lastRun = lastRuns?.[key] ?? null;
            return (
              <section
                key={key}
                aria-labelledby={`job-retention-${key}-title`}
                className="flex flex-col gap-3 rounded border border-border-muted bg-bg-secondary/40 p-4"
              >
                <div>
                  <h3
                    id={`job-retention-${key}-title`}
                    className="text-sm font-semibold text-text-primary"
                  >
                    {label}
                  </h3>
                  <p className="mt-1 text-xs leading-5 text-text-secondary">{description}</p>
                  {key === 'logs' ? (
                    <p className="mt-1 text-xs leading-5 text-text-secondary">
                      This does not delete Job records or runs history. A Job record may remain
                      after its expired log is removed, but that log can no longer be viewed.
                      Files with unknown ownership, links, directories, and logs for active
                      Jobs or Runners are kept.
                    </p>
                  ) : (
                    <p className="mt-1 text-xs leading-5 text-text-secondary">
                      Other Job statuses are unaffected; this rule does not infer or group statuses.
                    </p>
                  )}
                </div>

                <label className="flex items-start gap-2 text-xs text-text-primary">
                  <input
                    type="checkbox"
                    aria-label={`Enable ${label} cleanup`}
                    checked={rule.enabled}
                    disabled={saving || rules === null}
                    onChange={(event) => {
                      setDraft((current) => ({
                        ...current,
                        [key]: { ...current[key], enabled: event.target.checked },
                      }));
                      setFeedback(null);
                    }}
                    className="mt-0.5 accent-accent"
                  />
                  <span>Enable automatic cleanup</span>
                </label>

                <label className="flex max-w-sm flex-col gap-1 text-xs text-text-secondary">
                  <span>Keep {label} for at least</span>
                  <span className="flex items-center gap-2">
                    <input
                      aria-label={`Keep ${label} for days`}
                      type="number"
                      min={1}
                      max={36500}
                      step={1}
                      value={rule.days ?? ''}
                      disabled={saving || rules === null}
                      onChange={(event) => {
                        const value = event.target.value;
                        setDraft((current) => ({
                          ...current,
                          [key]: {
                            ...current[key],
                            days: value === '' ? null : Number(value),
                          },
                        }));
                        setFeedback(null);
                      }}
                      className={`${selectClass} max-w-32`}
                    />
                    <span>days</span>
                  </span>
                  <span>Leave blank to prevent this rule from running.</span>
                </label>

                <div className="border-t border-border-muted pt-2 text-xs text-text-tertiary">
                  <p className="font-medium text-text-secondary">Last check for this rule</p>
                  {lastRun ? (
                    <>
                      <p className="mt-1">
                        {new Date(lastRun.scannedAt).toLocaleString()} · scanned {lastRun.scanned}
                        {' · '}deleted {lastRun.deleted}{' · '}skipped {lastRun.skipped}
                        {' · '}errors {lastRun.errorCount}
                      </p>
                      {lastRun.errors.length > 0 && (
                        <details className="mt-1">
                          <summary className="cursor-pointer">Error details</summary>
                          <ul className="mt-1 list-disc pl-4">
                            {lastRun.errors.map((message, index) => (
                              <li key={`${key}-error-${index}`}>{message}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </>
                  ) : (
                    <p className="mt-1">No automatic check has run for this rule yet.</p>
                  )}
                </div>
              </section>
            );
          })}

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={saving || rules === null}
              onClick={() => void saveSettings()}
              className="rounded border border-accent/50 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent transition-colors hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? 'Saving…' : 'Save settings'}
            </button>
            {feedback && <span className="text-xs text-success" role="status">{feedback}</span>}
            {error && (
              <span className="inline-flex items-center gap-2 text-xs text-danger" role="alert">
                {error}
                {rules === null && (
                  <button
                    type="button"
                    onClick={() => void loadSettings()}
                    className="underline underline-offset-2"
                  >
                    Retry
                  </button>
                )}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
