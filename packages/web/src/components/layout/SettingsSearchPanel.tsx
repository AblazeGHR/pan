import { Search, X } from 'lucide-react';
import { SETTINGS_CATEGORY_LABELS } from './settingsCategories';
import type { SettingsSearchEntry } from './settingsSearch';

interface SettingsSearchFieldProps {
  value: string;
  onChange: (value: string) => void;
}

/**
 * Right-hand search entry in the App Settings tab bar. Empty by default, so the
 * modal keeps its original tabbed layout until the user types.
 */
export function SettingsSearchField({ value, onChange }: SettingsSearchFieldProps) {
  return (
    <div className="relative w-full min-w-0" data-testid="settings-search-field">
      <Search
        size={13}
        aria-hidden
        className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-tertiary"
      />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && value) {
            // Clear first; only an already-empty field lets Escape close the modal.
            event.stopPropagation();
            onChange('');
          }
        }}
        aria-label="Search settings"
        placeholder="Search settings"
        data-testid="settings-search-input"
        className="w-full rounded border border-border-default bg-bg-tertiary py-1 pl-7 pr-7 text-xs text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent [&::-webkit-search-cancel-button]:appearance-none"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Clear settings search"
          data-testid="settings-search-clear"
          className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-tertiary transition-colors hover:bg-bg-hover hover:text-text-primary"
        >
          <X size={12} />
        </button>
      )}
    </div>
  );
}

interface SettingsSearchResultsProps {
  query: string;
  results: SettingsSearchEntry[];
  onSelect: (entry: SettingsSearchEntry) => void;
}

/**
 * Hits keep the original group name and tab, so opening one lands on the real
 * control with its own edit behavior and permission gating unchanged.
 */
export function SettingsSearchResults({ query, results, onSelect }: SettingsSearchResultsProps) {
  const trimmed = query.trim();
  return (
    <section aria-label="Settings search results" data-testid="settings-search-results">
      {results.length === 0 ? (
        <p
          data-testid="settings-search-empty"
          className="rounded-md border border-border-muted bg-bg-primary px-3 py-6 text-center text-[11px] text-text-tertiary"
        >
          No settings match “{trimmed}”.
        </p>
      ) : (
        <>
          <p className="mb-2 text-[11px] text-text-tertiary">
            {results.length} matching setting{results.length === 1 ? '' : 's'} for “{trimmed}”.
          </p>
          <ul className="divide-y divide-border-muted rounded-md border border-border-muted bg-bg-primary">
            {results.map((entry) => (
              <li key={entry.id}>
                <button
                  type="button"
                  onClick={() => onSelect(entry)}
                  data-testid={`settings-search-hit-${entry.id}`}
                  data-settings-search-tab={entry.tab}
                  className="flex w-full items-start justify-between gap-3 px-3 py-2 text-left transition-colors hover:bg-bg-hover"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-xs text-text-primary">{entry.group}</span>
                    <span className="mt-0.5 block text-[10px] text-text-tertiary">
                      {entry.detail}
                    </span>
                  </span>
                  <span className="shrink-0 rounded border border-border-muted px-1.5 py-0.5 text-[10px] text-text-tertiary">
                    {SETTINGS_CATEGORY_LABELS[entry.tab]}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
