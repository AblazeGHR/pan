import { useEffect } from 'react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import type { GroupMode } from '@/stores/uiStore';

interface AppSettingsPopoverProps {
  open: boolean;
  onClose: () => void;
  /** Where the popover anchors relative to its wrapper element. */
  placement?: 'bottom-right' | 'right';
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
      className="w-full flex items-center justify-between gap-2 py-1 text-left"
    >
      <span className="min-w-0">
        <span className="block text-xs text-text-primary">{label}</span>
        {hint && (
          <span className="block text-[10px] text-text-tertiary font-mono">
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
 * Global app-settings popover: default session-list grouping + toggles for
 * meta-agent / task-agent / QQ-injected messages. Reads/writes
 * appSettingsStore directly, so changes take effect immediately.
 */
export function AppSettingsPopover({
  open,
  onClose,
  placement = 'bottom-right',
}: AppSettingsPopoverProps) {
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

  // Close on outside click. The anchor wrapper (gear button + popover) carries
  // `data-app-settings`, so clicking the gear toggles instead of instantly
  // closing and reopening (same pattern as the chat SettingsPopover).
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('[data-app-settings]')) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open, onClose]);

  if (!open) return null;

  const placementClass =
    placement === 'right'
      ? 'left-full ml-1 bottom-0'
      : 'right-0 top-full mt-1';

  return (
    <div
      data-app-settings-popover
      className={`absolute z-30 w-64 rounded-md border border-border-default bg-bg-primary shadow-lg p-3 space-y-3 ${placementClass}`}
    >
      <h4 className="text-xs font-semibold text-text-secondary">
        App Settings
      </h4>

      <div>
        <label className="block text-xs text-text-secondary mb-1">
          Default group by
        </label>
        <select
          value={defaultGroupBy}
          onChange={(e) => setDefaultGroupBy(e.target.value as GroupMode)}
          className="w-full rounded border border-border-default bg-bg-tertiary px-2 py-1 text-xs text-text-primary"
        >
          {GROUP_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="border-t border-border-muted pt-2 space-y-1">
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

      <div className="border-t border-border-muted pt-2">
        <button
          type="button"
          onClick={resetSettings}
          className="text-[11px] text-text-tertiary hover:text-text-primary"
        >
          Reset to defaults
        </button>
        <p className="mt-2 text-[10px] text-text-tertiary leading-relaxed">
          Hiding affects frontend display only — original messages stay in
          session history and reappear when toggled back on.
        </p>
      </div>
    </div>
  );
}
