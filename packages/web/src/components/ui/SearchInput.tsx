import { Search } from 'lucide-react';

/** Shared compact sidebar search presentation; callers own their query. */
export function SearchInput({ value, onChange, placeholder, label }: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: 'session' | 'workspace';
}) {
  return (
    <div className="relative min-w-0 flex-1" onKeyDown={(event) => event.stopPropagation()}>
      <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none" />
      <input
        type="text"
        placeholder={placeholder}
        aria-label={`Search ${label === 'workspace' ? 'workspaces' : 'sessions'}`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full bg-bg-tertiary border border-border-default rounded text-xs py-1 pl-6 pr-6 text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent/50"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          className="absolute right-1 top-1/2 -translate-y-1/2 px-1 text-text-tertiary hover:text-text-primary"
          aria-label={`Clear ${label} search`}
          title={`Clear ${label} search`}
        >
          ×
        </button>
      )}
    </div>
  );
}
