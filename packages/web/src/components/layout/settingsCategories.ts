/** Canonical App Settings category order. */
export const SETTINGS_CATEGORY_ORDER = [
  'lifecycle',
  'behavior',
  'remote',
  'appearance',
  'notifications',
  'shortcuts',
  'adapters',
  'plugins',
  'data',
  'authorization',
] as const;

export type SettingsCategory = (typeof SETTINGS_CATEGORY_ORDER)[number];

export type SettingsTab = SettingsCategory | 'search';

export const SETTINGS_TABS: readonly SettingsTab[] = [...SETTINGS_CATEGORY_ORDER, 'search'];

/** Canonical tab labels. The settings search reuses them, so they live with the order. */
export const SETTINGS_CATEGORY_LABELS: Record<SettingsCategory, string> = {
  lifecycle: 'Lifecycle',
  behavior: 'Behavior',
  remote: 'Remote',
  appearance: 'Appearance',
  shortcuts: 'Shortcuts',
  notifications: 'Notifications',
  adapters: 'Adapters',
  plugins: 'Plugins',
  data: 'Data',
  authorization: 'Authorization',
};
