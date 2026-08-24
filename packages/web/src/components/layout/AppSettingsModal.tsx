import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import type { GroupMode } from '@/stores/uiStore';

interface AppSettingsModalProps {
  open: boolean;
  onClose: () => void;
}

const GROUP_OPTIONS: { value: GroupMode; label: string }[] = [
  { value: 'none', label: 'Off' },
  { value: 'workdir', label: 'Working directory' },
  { value: 'manager', label: 'Manager' },
];

function SwitchRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-hover transition-colors"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">{label}</span>
        {hint && (
          <span className="block text-[10px] text-text-tertiary font-mono mt-0.5">
            {hint}
          </span>
        )}
      </span>
      <span
        className={`relative inline-flex w-8 h-[18px] shrink-0 rounded-full transition-colors ${
          checked ? 'bg-accent' : 'bg-bg-hover'
        }`}
      >
        <span
          className={`absolute top-[2px] left-[2px] h-[14px] w-[14px] rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-[14px]' : 'translate-x-0'
          }`}
        />
      </span>
    </button>
  );
}

/**
 * Global app-settings modal. Desktop: centered card covering ~75% of the
 * viewport. Mobile: full-screen, edge-to-edge and scrollable. Reads/writes
 * appSettingsStore directly so changes take effect immediately.
 *
 * Rendered through a portal to <body> — the sidebar's mobile container uses
 * `transform`, which would otherwise become the containing block for
 * `position: fixed` descendants and clamp the overlay to the sidebar width.
 */
export function AppSettingsModal({ open, onClose }: AppSettingsModalProps) {
  const {
    defaultGroupBy,
    showMetaAgent,
    showTaskAgent,
    showQQ,
    setDefaultGroupBy,
    setShowMetaAgent,
    setShowTaskAgent,
    setShowQQ,
    resetSettings,
  } = useAppSettingsStore();

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="App Settings"
      className="app-settings-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="app-settings-card flex flex-col w-full h-full bg-bg-primary overflow-hidden md:w-[75vw] md:h-[75vh] md:rounded-lg md:border md:border-border-default md:shadow-xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-border-default px-4 py-3 md:px-6 md:py-4 shrink-0">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">App Settings</h2>
            <p className="mt-0.5 text-[11px] text-text-tertiary">
              Global preferences for the Pan app.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-text-tertiary hover:text-text-primary hover:bg-bg-tertiary p-1.5 rounded transition-colors shrink-0"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4 md:px-6 md:py-5 space-y-6">
          {/* Session list grouping */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">
              Session list
            </h3>
            <label className="block text-xs text-text-secondary mb-1">
              Default group by
            </label>
            <select
              value={defaultGroupBy}
              onChange={(e) => setDefaultGroupBy(e.target.value as GroupMode)}
              className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary outline-none focus:border-accent"
            >
              {GROUP_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <p className="mt-1.5 text-[11px] text-text-tertiary leading-relaxed">
              Applies to the session list as the default grouping. You can still
              cycle grouping per view with the group button.
            </p>
          </section>

          {/* Message visibility */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary mb-2">
              Message visibility
            </h3>
            <div className="rounded-md border border-border-muted divide-y divide-border-muted bg-bg-primary">
              <SwitchRow
                label="Show meta-agent info"
                hint="////by agent"
                checked={showMetaAgent}
                onChange={setShowMetaAgent}
              />
              <SwitchRow
                label="Show task-agent info"
                hint="@@@@by agent"
                checked={showTaskAgent}
                onChange={setShowTaskAgent}
              />
              <SwitchRow
                label="Show QQ messages"
                hint="@@@@by qq"
                checked={showQQ}
                onChange={setShowQQ}
              />
            </div>
          </section>

          {/* Reset */}
          <div className="border-t border-border-muted pt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <p className="text-[11px] text-text-tertiary leading-relaxed sm:max-w-md">
              Hiding affects frontend display only — original messages stay in
              session history and reappear when toggled back on.
            </p>
            <button
              type="button"
              onClick={resetSettings}
              className="shrink-0 text-[11px] text-text-tertiary hover:text-text-primary underline underline-offset-2"
            >
              Reset to defaults
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
