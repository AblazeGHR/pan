import { useEffect, useRef, useState } from 'react';
import { isMockMode } from '@/demo/mockBackend';
import {
  claimStartupRecovery,
  decideStartupRecovery,
  fetchStartupRecovery,
} from '@/services/api';
import type {
  ApiStartupRecoveryChoice,
  ApiStartupRecoveryRecord,
} from '@/types';

const CHOICES: Array<{
  value: ApiStartupRecoveryChoice;
  title: string;
  detail: string;
}> = [
  {
    value: 'restart',
    title: 'Restart these Sessions',
    detail: 'Send “继续” to each candidate through the normal Session broadcast path.',
  },
  {
    value: 'preserve-running',
    title: 'Keep their legal state as running',
    detail: 'Do not restart them and leave their persisted legal state unchanged.',
  },
  {
    value: 'sync-actual',
    title: 'Update legal state to current Worker state',
    detail: 'Do not restart them; Pan will read each current runtime state and persist it.',
  },
];

function makeTabId(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function StartupRecoveryPrompt() {
  const tabId = useRef(makeTabId());
  const [record, setRecord] = useState<ApiStartupRecoveryRecord | null>(null);
  const [claimed, setClaimed] = useState(false);
  const [selected, setSelected] = useState<ApiStartupRecoveryChoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (isMockMode()) return;
    let active = true;
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const status = await fetchStartupRecovery();
        if (!active) return;
        setRecord(status);
        if (status.state === 'no_candidates' || status.state === 'completed') {
          setClaimed(false);
          setLoadError(null);
          return;
        }
        if (status.state === 'initializing') return;
        const claim = await claimStartupRecovery(status.generation, tabId.current);
        if (!active) return;
        setClaimed(claim.claimed);
        setRecord((current) => ({
          ...(current ?? status),
          state: claim.state,
          decision: claim.decision ?? status.decision,
          attempts: claim.attempts ?? status.attempts,
          results: claim.results ?? status.results,
          error: claim.error ?? status.error,
          candidateSnapshot: claim.candidates ?? status.candidateSnapshot,
        }));
        if (claim.decision) setSelected(claim.decision);
        setLoadError(null);
      } catch (error) {
        if (active) setLoadError(error instanceof Error ? error.message : String(error));
      } finally {
        loading = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const submit = async () => {
    if (!record || !selected || busy) return;
    setBusy(true);
    setLoadError(null);
    try {
      const next = await decideStartupRecovery(record.generation, tabId.current, selected);
      setRecord(next);
      if (next.state === 'failed') setLoadError(next.error || 'Startup recovery failed.');
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
      try {
        const latest = await fetchStartupRecovery();
        if (latest.generation === record.generation) {
          setRecord(latest);
          if (latest.decision) setSelected(latest.decision);
        }
      } catch {
        // Keep the original error visible; the lease heartbeat retries status.
      }
    } finally {
      setBusy(false);
    }
  };

  if (!claimed || !record || !record.candidateSnapshot.length
      || record.state === 'completed' || record.state === 'no_candidates') {
    if (!loadError || record || claimed) return null;
    return (
      <div className="fixed inset-0 z-[100] grid place-items-center bg-black/55 px-4">
        <section className="w-full max-w-lg rounded-lg border border-border-default bg-bg-secondary p-5 shadow-2xl">
          <h2 className="text-base font-semibold text-text-primary">Session recovery decision</h2>
          <p className="mt-2 text-sm text-text-secondary">{loadError}</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 rounded bg-accent px-3 py-2 text-xs text-white"
          >
            Retry dashboard load
          </button>
        </section>
      </div>
    );
  }

  const lockedChoice = record.decision;
  const processing = busy || record.state === 'processing';
  const failedResults = record.results.filter((result) =>
    result.status !== 'queued' && result.status !== 'preserved' && result.status !== 'updated',
  );
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-black/55 px-4">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="startup-recovery-title"
        className="w-full max-w-xl rounded-lg border border-border-default bg-bg-secondary p-5 shadow-2xl"
      >
        <h2 id="startup-recovery-title" className="text-base font-semibold text-text-primary">
          Continue previous Sessions?
        </h2>
        <p className="mt-2 text-sm text-text-secondary">
          Pan found {record.candidateSnapshot.length} Session(s) whose persisted legal state is running
          and which currently have no live Worker.
        </p>
        <ul className="mt-3 max-h-32 space-y-1 overflow-y-auto rounded border border-border-muted bg-bg-primary px-3 py-2 text-xs text-text-secondary">
          {record.candidateSnapshot.map((candidate) => (
            <li key={candidate.id} className="truncate" title={candidate.id}>
              {candidate.name || candidate.id}
            </li>
          ))}
        </ul>
        <fieldset className="mt-4 space-y-2">
          <legend className="sr-only">Choose startup recovery action</legend>
          {CHOICES.map((choice, index) => {
            const disabledByRecordedChoice = Boolean(lockedChoice && lockedChoice !== choice.value);
            return (
              <label
                key={choice.value}
                className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 ${
                  selected === choice.value
                    ? 'border-accent bg-accent/10'
                    : 'border-border-muted bg-bg-primary'
                } ${disabledByRecordedChoice || processing ? 'opacity-60' : ''}`}
              >
                <input
                  type="radio"
                  name="startup-recovery-choice"
                  checked={selected === choice.value}
                  disabled={disabledByRecordedChoice || processing}
                  onChange={() => setSelected(choice.value)}
                  className="mt-0.5"
                />
                <span>
                  <span className="block text-xs font-medium text-text-primary">
                    {index + 1}. {choice.title}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-text-tertiary">
                    {choice.detail}
                  </span>
                </span>
              </label>
            );
          })}
        </fieldset>
        {loadError && (
          <p role="alert" className="mt-3 rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
            {loadError}
          </p>
        )}
        {failedResults.length > 0 && (
          <ul className="mt-2 space-y-1 text-[10px] text-danger">
            {failedResults.map((result, index) => (
              <li key={`${String(result.sessionId ?? 'session')}-${index}`}>
                {String(result.sessionId ?? 'Session')}: {String(result.error ?? result.result ?? result.status ?? 'failed')}
              </li>
            ))}
          </ul>
        )}
        {failedResults.length > 0 && (
          <ul className="mt-2 space-y-1 text-[10px] text-danger">
            {failedResults.map((result, index) => (
              <li key={`${String(result.sessionId ?? 'session')}-${index}`}>
                {String(result.sessionId ?? 'Session')}: {String(result.error ?? result.result ?? result.status ?? 'failed')}
              </li>
            ))}
          </ul>
        )}
        <div className="mt-4 flex items-center justify-between gap-3">
          <span className="text-[10px] text-text-tertiary">
            {processing ? 'Applying saved choice…' : lockedChoice ? 'Retry is limited to the saved choice.' : 'Choose one action to continue.'}
          </span>
          <button
            type="button"
            disabled={!selected || processing || Boolean(lockedChoice && selected !== lockedChoice)}
            onClick={() => void submit()}
            className="shrink-0 rounded bg-accent px-4 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {processing ? 'Working…' : record.state === 'failed' ? 'Retry choice' : 'Continue'}
          </button>
        </div>
      </section>
    </div>
  );
}
