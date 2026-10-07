import { useRef } from 'react';
import { Search } from 'lucide-react';

/** Presentation only: callers own fetching, target identity and selection. */
export function QqContactSearch({ value, onChange, disabled = false }: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return <div className="relative min-w-0">
    <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-tertiary" />
    <input ref={input} type="search" aria-label="Search QQ contacts" value={value} disabled={disabled}
      onChange={(event) => onChange(event.target.value)} placeholder="Search by name or QQ / group number"
      className="w-full min-w-0 rounded border border-border-default bg-bg-tertiary py-1.5 pl-7 pr-14 text-xs text-text-primary placeholder:text-text-tertiary focus:border-accent focus:outline-none disabled:opacity-50" />
    {value && <button type="button" disabled={disabled} aria-label="Clear QQ contact search"
      onClick={() => { onChange(''); input.current?.focus(); }} className="absolute right-1 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-xs text-text-secondary focus-visible:outline focus-visible:outline-accent">Clear</button>}
  </div>;
}

export function QqContactName({ name, number }: { name: string; number: string }) {
  return <span className="block min-w-0 break-words">{name || number}</span>;
}
