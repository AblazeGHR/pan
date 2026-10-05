import { useEffect, useState } from 'react';

/** UI numbers are 1-based, latest first; API ordinals remain oldest first. */
export function HistorySearchOrdinal({ value, total, disabled, testId, onConfirm }: {
  value: number; total: number; disabled: boolean; testId: string; onConfirm: (number: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value, total]);
  const reset = () => setDraft(String(value));
  const confirm = () => {
    const number = /^\d+$/.test(draft.trim()) ? Number(draft.trim()) : NaN;
    if (!Number.isSafeInteger(number) || number < 1 || number > total) { reset(); return; }
    onConfirm(number);
  };
  return <span data-testid={testId} className="session-history-search__count" aria-live="polite" title="1 = latest occurrence; current Session only. Enter to jump.">
    <input className="history-search-ordinal" aria-label="Result number (1-based, latest first, current Session)"
      inputMode="numeric" value={draft} disabled={disabled} onChange={(event) => setDraft(event.target.value)}
      onFocus={(event) => event.currentTarget.select()} onBlur={reset}
      onKeyDown={(event) => {
        if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); confirm(); }
        else if (event.key === 'Escape') reset();
      }} /> / {total}
  </span>;
}
