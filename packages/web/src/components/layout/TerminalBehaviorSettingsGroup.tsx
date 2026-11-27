import { settingsText } from './settingsCatalog';
import { useEffect, useId, useRef, useState } from 'react';
import { fetchRuntimeIssueSchema } from '@/services/api';
import { useAppSettingsStore } from '@/stores/appSettingsStore';

/**
 * Extra search terms for a classification token. Runtime issue names split
 * words with underscores, so "limit" should still find quota exhaustion and
 * capacity classes, not only the literal `*_rate_limited` entries.
 */
const SEARCH_ALIASES: Record<string, string[]> = {
  limit: ['quota', 'exhausted', 'rate', 'capacity'],
  limited: ['quota', 'exhausted', 'rate', 'capacity'],
  quota: ['limit', 'exhausted', 'capacity'],
};

function matchScore(candidate: string, query: string): number | null {
  const target = candidate.toLowerCase();
  const normalized = query.trim().toLowerCase();
  if (!normalized) return null;
  if (target === normalized) return 0;
  if (target.startsWith(normalized)) return 1;
  if (target.includes(normalized)) return 2;
  const tokens = normalized.split(/[\s_-]+/).filter(Boolean);
  if (tokens.length === 0) return null;
  const matchesEveryToken = tokens.every((token) => {
    const terms = [token, ...(SEARCH_ALIASES[token] ?? [])];
    return terms.some((term) => target.includes(term));
  });
  return matchesEveryToken ? 3 : null;
}

/** Rank schema classifications for the current input, best match first. */
function suggestClassifications(candidates: string[], query: string): string[] {
  if (!query.trim()) return [];
  return candidates
    .map((candidate, index) => ({ candidate, index, score: matchScore(candidate, query) }))
    .filter((row): row is { candidate: string; index: number; score: number } => row.score !== null)
    .sort((a, b) => a.score - b.score || a.index - b.index)
    .map((row) => row.candidate);
}

export function TerminalBehaviorSettingsGroup() {
  const value = useAppSettingsStore((s) => s.terminalBehavior);
  const save = useAppSettingsStore((s) => s.saveTerminalBehavior);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [classification, setClassification] = useState('');
  const [candidates, setCandidates] = useState<string[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const entries = Object.entries(value.autoReportByErrorType);
  const suggestions = suggestClassifications(candidates, classification);
  const showSuggestions = suggestionsOpen && !busy && suggestions.length > 0;

  useEffect(() => {
    let cancelled = false;
    fetchRuntimeIssueSchema()
      .then((schema) => {
        if (cancelled || !Array.isArray(schema.classification)) return;
        const unique = [...new Set(schema.classification
          .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
          .map((item) => item.trim()))];
        setCandidates(unique);
      })
      .catch(() => {
        // Manual classification entry stays available when the schema is unavailable.
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!suggestionsOpen) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setSuggestionsOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    return () => document.removeEventListener('mousedown', closeOnOutsideClick);
  }, [suggestionsOpen]);

  const choose = (candidate: string) => {
    setClassification(candidate);
    setActiveIndex(0);
    setSuggestionsOpen(false);
  };

  const update = async (next: Record<string, boolean>) => {
    setBusy(true); setError('');
    try { await save({ autoReportByErrorType: next }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save terminal Behavior'); }
    finally { setBusy(false); }
  };
  return <section aria-label={settingsText('terminal-behavior-region')} data-testid="terminal-behavior-settings" data-settings-id="terminal-behavior" className="mt-4">
    <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">{settingsText('terminal-behavior')}</h3>
    <p className="text-xs text-text-secondary mb-2">{settingsText('terminal-hint')}</p>
    <div className="flex gap-2">
      <div ref={rootRef} className="relative min-w-0 flex-1">
        <input
          aria-label={settingsText('terminal-classification')}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={showSuggestions}
          aria-controls={showSuggestions ? listId : undefined}
          aria-activedescendant={showSuggestions ? `${listId}-${activeIndex}` : undefined}
          value={classification}
          disabled={busy}
          onChange={(e) => {
            setClassification(e.target.value);
            setActiveIndex(0);
            setSuggestionsOpen(e.target.value.trim().length > 0);
          }}
          onKeyDown={(event) => {
            if (!showSuggestions) return;
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              setActiveIndex((index) => Math.min(index + 1, suggestions.length - 1));
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              setActiveIndex((index) => Math.max(index - 1, 0));
            } else if (event.key === 'Enter') {
              const candidate = suggestions[activeIndex];
              if (candidate) { event.preventDefault(); choose(candidate); }
            } else if (event.key === 'Escape') {
              event.preventDefault();
              setSuggestionsOpen(false);
            }
          }}
          placeholder={settingsText('terminal-classification-example')}
          className="w-full min-w-0 rounded border border-border-default bg-bg-primary px-2 py-1 text-xs"
        />
        {showSuggestions && <ul
          id={listId}
          role="listbox"
          aria-label={settingsText('terminal-classifications')}
          className="absolute left-0 right-0 top-full z-20 mt-1 max-h-40 overflow-y-auto rounded border border-border-default bg-bg-secondary py-1 shadow-xl"
        >
          {suggestions.map((candidate, index) => <li
            key={candidate}
            id={`${listId}-${index}`}
            role="option"
            aria-selected={index === activeIndex}
            onMouseDown={(event) => event.preventDefault()}
            onMouseEnter={() => setActiveIndex(index)}
            onClick={() => choose(candidate)}
            className={`cursor-pointer px-2 py-1 text-xs ${index === activeIndex ? 'bg-bg-tertiary text-accent' : 'text-text-primary hover:bg-bg-tertiary'}`}
          >{candidate}</li>)}
        </ul>}
      </div>
      <button type="button" disabled={busy || !classification.trim()} className="rounded border border-border-default px-2 py-1 text-xs" onClick={() => { const key = classification.trim(); void update({ ...value.autoReportByErrorType, [key]: false }).then(() => setClassification('')); }}>Disable report</button>
    </div>
    <div className="mt-2 space-y-1">{entries.map(([key, enabled]) => <div key={key} className="flex items-center justify-between rounded border border-border-muted px-2 py-1 text-xs"><span className="break-all">{key}</span><span className="flex items-center gap-2"><button type="button" disabled={busy} onClick={() => void update({ ...value.autoReportByErrorType, [key]: !enabled })}>{enabled ? 'Report' : 'Skip report'}</button><button type="button" disabled={busy} aria-label={`Remove ${key}`} onClick={() => { const next = { ...value.autoReportByErrorType }; delete next[key]; void update(next); }}>×</button></span></div>)}</div>
    {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
  </section>;
}
