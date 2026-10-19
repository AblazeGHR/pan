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
] as const;

export type SettingsCategory = (typeof SETTINGS_CATEGORY_ORDER)[number];

export type SettingsTab = SettingsCategory;

export const SETTINGS_TABS: readonly SettingsTab[] = SETTINGS_CATEGORY_ORDER;
