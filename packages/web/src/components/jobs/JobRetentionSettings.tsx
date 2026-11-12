import { settingsText } from '@/components/layout/settingsCatalog';
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
type PendingRetention = Omit<RetentionUpdate, 'expectedRequestScope'> & { expectedRequestScope?: string; requestScope?: string };
const RETENTION_RULE_META: Array<{
  key: JobRetentionRule;
  label: string; labelId: string;
  description: string; descriptionId: string;
}> = [
  {
    key: 'completed',
    labelId: "job-retention-settings.1", label: settingsText("job-retention-settings.1"),
    descriptionId: "job-retention-settings.2", description: settingsText("job-retention-settings.2"),
  },
  {
    key: 'failed',
    labelId: "job-retention-settings.3", label: settingsText("job-retention-settings.3"),
    descriptionId: "job-retention-settings.4", description: settingsText("job-retention-settings.4"),
  },
  {
    key: 'timed_out',
    labelId: "job-retention-settings.5", label: settingsText("job-retention-settings.5"),
    descriptionId: "job-retention-settings.6", description: settingsText("job-retention-settings.6"),
  },
  {
    key: 'cancelled',
    labelId: "job-retention-settings.7", label: settingsText("job-retention-settings.7"),
    descriptionId: "job-retention-settings.8", description: settingsText("job-retention-settings.8"),
  },
  {
    key: 'logs',
    labelId: "job-retention-settings.9", label: settingsText("job-retention-settings.9"),
    descriptionId: "job-retention-settings.10", description: settingsText("job-retention-settings.10"),
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
  const [requestScope, setRequestScope] = useState<string | null>(null);
  const [pending, setPendingState] = useState<PendingRetention | null>(() => {
    try { return JSON.parse(sessionStorage.getItem(pendingStorage) ?? 'null') as PendingRetention | null; } catch { return null; }
  });
  const lock = useRef(false);
  const pendingRef = useRef(pending);
  const setPending = (value: PendingRetention | null) => {
    if (value) sessionStorage.setItem(pendingStorage, JSON.stringify(value));
    else sessionStorage.removeItem(pendingStorage);
    pendingRef.current = value;
    setPendingState(value);
  };
  const applySettings = (result: CanonicalRetentionSettings) => {
    setRequestScope(typeof result.requestScope === 'string' && result.requestScope ? result.requestScope : null);
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

  const settle = (receipt: CanonicalReceipt, expectedKey: string, expectedScope: string) => {
    if (receipt.requestKey !== expectedKey) {
      setError('The response request key does not match the original request. Its outcome remains unresolved.');
      return;
    }
    if (receipt.status === 'completed' && receipt.result?.settings) {
      if ((receipt.result.settings as CanonicalRetentionSettings).requestScope !== expectedScope) {
        setError('The response account or profile does not match the original request. Its outcome remains unresolved.');
        return;
      }
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
    if (!requestScope) { setError('Cannot verify the current account and profile; settings were not submitted.'); return; }
    const body: RetentionUpdate = { expectedRevision: revision, expectedRequestScope: requestScope, requestKey: crypto.randomUUID(), rules: cloneRules(draft) };
    lock.current = true; setSaving(true); setError(null); setFeedback(null);
    try {
      const current = await fetchCompletedJobRetentionSettings();
      if (current.requestScope !== requestScope) throw new Error('Account or profile changed; settings were not submitted.');
      try { setPending({ ...body, requestScope }); }
      catch { throw new Error('Cannot preserve the request identity; settings were not submitted.'); }
      try { settle(await updateCompletedJobRetentionSettings(body), body.requestKey, requestScope); }
      catch (reason) {
        const status = (reason as { status?: number })?.status;
        if (status === 400 || status === 401 || status === 403 || status === 409) setPending(null);
        throw reason;
      }
    } catch (reason) { setError(errMsg(reason)); }
    finally { lock.current = false; setSaving(false); }
  };


  const queryOriginal = async () => {
    if (!pending?.requestScope || pending.requestScope !== requestScope || lock.current) return;
    lock.current = true; setSaving(true); setError(null);
    try {
      const before = await fetchCompletedJobRetentionSettings();
      if (before.requestScope !== pending.requestScope) throw new Error('Account or profile changed; the original request remains unresolved');
      const receipt = await canonicalRequest<CanonicalReceipt>(`operations?operation=update&requestKey=${encodeURIComponent(pending.requestKey)}`);
      const after = await fetchCompletedJobRetentionSettings();
      if (after.requestScope !== pending.requestScope) throw new Error('Account or profile changed; the original request remains unresolved');
      settle(receipt, pending.requestKey, pending.requestScope);
    } catch (reason) { setError(`${errMsg(reason)}. The original request remains unresolved.`); }
    finally { lock.current = false; setSaving(false); }
  };


  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-semibold text-text-primary" data-settings-id="jobs-retention">{settingsText("jobs-retention")}</h2>
        <p className="mt-1 text-xs leading-5 text-text-secondary" data-settings-id="job-retention-settings.11">
          {settingsText("job-retention-settings.11")}</p>
      </div>

      {loading ? (
        <p className="text-xs text-text-tertiary" role="status" data-settings-id="job-retention-settings.12">{settingsText("job-retention-settings.12")}</p>
      ) : (
        <>
          {RETENTION_RULE_META.map(({ key, label, labelId, description, descriptionId }) => {
            const rule = draft[key];
            const lastRun = lastRuns?.[key] ?? null;
            return (
              <section
                key={key}
                data-settings-id={labelId}
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
                  <p className="mt-1 text-xs leading-5 text-text-secondary" data-settings-id={descriptionId}>{description}</p>
                  {key === 'logs' ? (
                    <p className="mt-1 text-xs leading-5 text-text-secondary" data-settings-id="job-retention-settings.13">
                      {settingsText("job-retention-settings.13")}</p>
                  ) : (
                    <p className="mt-1 text-xs leading-5 text-text-secondary" data-settings-id="job-retention-settings.14">
                      {settingsText("job-retention-settings.14")}</p>
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
                  <span data-settings-id="job-retention-settings.15">{settingsText("job-retention-settings.15")}</span>
                </label>

                <label className="flex max-w-[24rem] flex-col gap-1 text-xs text-text-secondary">
                  <span data-settings-id="job-retention-settings.16">{settingsText("job-retention-settings.16")}{label} {settingsText("job-retention-settings.17")}</span>
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
                    <span data-settings-id="job-retention-settings.18">{settingsText("job-retention-settings.18")}</span>
                  </span>
                  <span data-settings-id="job-retention-settings.19">{settingsText("job-retention-settings.19")}</span>
                </label>

                <div className="border-t border-border-muted pt-2 text-xs text-text-tertiary">
                  <p className="font-medium text-text-secondary" data-settings-id="job-retention-settings.20">{settingsText("job-retention-settings.20")}</p>
                  {lastRun ? (
                    <>
                      <p className="mt-1" data-settings-id="job-retention-settings.21">
                        {new Date(lastRun.at * 1000).toLocaleString()} {settingsText("job-retention-settings.21")}{lastRun.expired}
                        {' · '}{settingsText("job-retention-settings.22")}{lastRun.protected}{' · '}{settingsText("job-retention-settings.23")}{lastRun.errors.length}
                      </p>
                      {lastRun.errors.length > 0 && (
                        <details className="mt-1">
                          <summary className="cursor-pointer" data-settings-id="job-retention-settings.24">{settingsText("job-retention-settings.24")}</summary>
                          <ul className="mt-1 list-disc pl-4">
                            {lastRun.errors.map((message, index) => (
                              <li key={`${key}-error-${index}`} data-settings-id="job-retention-settings.25">{message.code} {settingsText("job-retention-settings.25")}{message.runId} {settingsText("job-retention-settings.26")}{message.attemptId}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </>
                  ) : (
                    <p className="mt-1" data-settings-id="job-retention-settings.27">{settingsText("job-retention-settings.27")}</p>
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
            {pending && <div className="text-xs text-warning" role="status" data-settings-id="job-retention-settings.28">{settingsText("job-retention-settings.28")}{pending.requestKey}. {pending.requestScope && pending.requestScope === requestScope ? 'The original account and profile match.' : 'The original account and profile cannot be verified; lookup is unavailable.'} {settingsText("job-retention-settings.29")}<details><summary data-settings-id="job-retention-settings.30">{settingsText("job-retention-settings.30")}</summary><pre className="whitespace-pre-wrap break-all">{JSON.stringify(pending, null, 2)}</pre></details>
              <button type="button" disabled={saving || !pending.requestScope || pending.requestScope !== requestScope} onClick={() => void queryOriginal()} className="underline disabled:opacity-50" data-settings-id="job-retention-settings.31">{settingsText("job-retention-settings.31")}</button>
            </div>}
            {feedback && <span className="text-xs text-warning" role="status">{feedback}</span>}
            {error && (
              <span className="inline-flex items-center gap-2 text-xs text-danger" role="alert">
                {error}
                {rules === null && (
                  <button
                    type="button"
                    onClick={() => void loadSettings()}
                    className="underline underline-offset-2" data-settings-id="job-retention-settings.32"
                  >
                    {settingsText("job-retention-settings.32")}</button>
                )}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
