import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchCompletedJobRetentionSettings, updateCompletedJobRetentionSettings, canonicalRequest,
} from '@/services/api';
import type {
  CompletedJobRetentionRun, CanonicalRetentionSettings, RetentionUpdate,
  JobRetentionRule,
  JobRetentionRules,
} from '@/services/api';
import type { CanonicalReceipt } from '@/types/canonicalJobs';

const pendingStorage = 'pan:job-retention-pending';
const RETENTION_RULE_META: Array<{
  key: JobRetentionRule;
  label: string;
  description: string;
}> = [
  {
    key: 'completed',
    label: 'Completed Runs',
    description: 'Cleans eligible completed Run history after the configured retention period.',
  },
  {
    key: 'failed',
    label: 'Failed Runs',
    description: 'Cleans eligible failed Run history after the configured retention period.',
  },
  {
    key: 'timed_out',
    label: 'Timed out Runs',
    description: 'Cleans eligible timed-out Run history after the configured retention period.',
  },
  {
    key: 'cancelled',
    label: 'Cancelled Runs',
    description: 'Cleans eligible cancelled Run history after the configured retention period.',
  },
  {
    key: 'logs',
    label: 'Run log files',
    description: 'Cleans eligible Run logs independently from Run history.',
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
  const [revision, setRevision] = useState<number | null>(null);
  const [pending, setPendingState] = useState<RetentionUpdate | null>(() => {
    try { return JSON.parse(sessionStorage.getItem(pendingStorage) ?? 'null') as RetentionUpdate | null; } catch { return null; }
  });
  const lock = useRef(false);
  const pendingRef = useRef(pending);
  const setPending = (value: RetentionUpdate | null) => {
    if (value) sessionStorage.setItem(pendingStorage, JSON.stringify(value));
    else sessionStorage.removeItem(pendingStorage);
    pendingRef.current = value;
    setPendingState(value);
  };
  const applySettings = (result: CanonicalRetentionSettings) => {
    setRevision(result.revision); setRules(result.rules); setDraft(cloneRules(result.rules)); setLastRuns(result.lastRuns);
    const invalidRules = RETENTION_RULE_META.filter(({ key }) => !result.configValidity[key]).map(({ label }) => label);
    if (invalidRules.length) setError(`Invalid saved values for ${invalidRules.join(', ')}. Those rules remain disabled until valid values are saved.`);
  };
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchCompletedJobRetentionSettings();
      applySettings(result);
      if (pendingRef.current) setDraft(cloneRules({ ...result.rules, ...pendingRef.current.rules }));
    } catch (reason) {
      setError(errMsg(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const settle = (receipt: CanonicalReceipt) => {
    if (receipt.status === 'completed' && receipt.result?.settings) {
      applySettings(receipt.result.settings as CanonicalRetentionSettings);
      setPending(null); setFeedback('Settings saved.');
    } else if (receipt.status === 'rejected' || receipt.status === 'cancelled') {
      setPending(null); setError(receipt.error?.message ?? `Operation ${receipt.status}.`);
    } else setError(`Operation ${receipt.status}; query the original request before continuing.`);
  };
  const saveSettings = async () => {
    if (lock.current || pending || revision === null) return;
    for (const { key, label } of RETENTION_RULE_META) {
      const days = draft[key].days;
      if (days !== null && (!Number.isInteger(days) || days < 1 || days > 36500)) {
        setError(`${label} keep days must be blank or a whole number from 1 to 36500.`); setFeedback(null); return;
      }
    }
    const body: RetentionUpdate = { expectedRevision: revision, requestKey: crypto.randomUUID(), rules: cloneRules(draft) };
    try { setPending(body); } catch { setError('Cannot preserve the request identity; settings were not submitted.'); return; }
    lock.current = true; setSaving(true); setError(null); setFeedback(null);
    try { settle(await updateCompletedJobRetentionSettings(body)); }
    catch (reason) {
      const status = (reason as { status?: number })?.status;
      if (status === 400 || status === 401 || status === 403 || status === 409) setPending(null);
      setError(errMsg(reason));
    } finally { lock.current = false; setSaving(false); }
  };
  const queryOriginal = async () => {
    if (!pending || lock.current) return;
    lock.current = true; setSaving(true); setError(null);
    try { settle(await canonicalRequest<CanonicalReceipt>(`operations?operation=update&requestKey=${encodeURIComponent(pending.requestKey)}`)); }
    catch (reason) { setError(`${errMsg(reason)}. The original request remains unresolved.`); }
    finally { lock.current = false; setSaving(false); }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-semibold text-text-primary">Automatic cleanup settings</h2>
        <p className="mt-1 text-xs leading-5 text-text-secondary">
          Each rule is independent. A blank day count prevents cleanup even when a switch is on.
          The four status rules apply to terminal Run history; logs are managed independently.
          Job definitions and schedules remain available. Active, unknown, pinned and referenced records are protected.
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
                      Log cleanup does not delete Job definitions or Run history. A Run may remain
                      after its expired log is removed, but that log can no longer be viewed.
                      Logs with unknown ownership and active execution logs are protected.
                    </p>
                  ) : (
                    <p className="mt-1 text-xs leading-5 text-text-secondary">
                      Other Run statuses are unaffected; this rule does not infer or group statuses.
                    </p>
                  )}
                </div>

                <label className="flex items-start gap-2 text-xs text-text-primary">
                  <input
                    type="checkbox"
                    aria-label={`Enable ${label} cleanup`}
                    checked={rule.enabled}
                    disabled={saving || rules === null || !!pending}
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

                <label className="flex max-w-[24rem] flex-col gap-1 text-xs text-text-secondary">
                  <span>Keep {label} for at least</span>
                  <span className="flex items-center gap-2">
                    <input
                      aria-label={`Keep ${label} for days`}
                      type="number"
                      min={1}
                      max={36500}
                      step={1}
                      value={rule.days ?? ''}
                      disabled={saving || rules === null || !!pending}
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
                        {new Date(lastRun.at * 1000).toLocaleString()} · expired {lastRun.expired}
                        {' · '}protected {lastRun.protected}{' · '}errors {lastRun.errors.length}
                      </p>
                      {lastRun.errors.length > 0 && (
                        <details className="mt-1">
                          <summary className="cursor-pointer">Error details</summary>
                          <ul className="mt-1 list-disc pl-4">
                            {lastRun.errors.map((message, index) => (
                              <li key={`${key}-error-${index}`}>{message.code} · Run {message.runId} · Attempt {message.attemptId}</li>
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
              disabled={saving || rules === null || !!pending}
              onClick={() => void saveSettings()}
              className="rounded border border-accent/50 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent transition-colors hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? 'Saving…' : 'Save settings'}
            </button>
            {pending && <div className="text-xs text-warning" role="status">Unresolved request {pending.requestKey}. Query using the same account and profile.
              <details><summary>Original request</summary><pre className="whitespace-pre-wrap break-all">{JSON.stringify(pending, null, 2)}</pre></details>
              <button type="button" disabled={saving} onClick={() => void queryOriginal()} className="underline">Query original operation</button>
            </div>}
            {feedback && <span className="text-xs text-warning" role="status">{feedback}</span>}
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
