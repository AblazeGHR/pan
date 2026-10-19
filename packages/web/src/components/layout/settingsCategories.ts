/**
 * Canonical App Settings category order (decided 2026-10-19).
 *
 * `shortcuts` is a reserved slot owned by the parallel Shortcuts task
 * (ses_d47fac0bbd49a32f). This baseline must not render an empty Shortcuts
 * page. Integration point: add `shortcuts` to `SettingsTab` and render its
 * panel in AppSettingsModal; the order below already places it between
 * Notifications and Adapters.
 */
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

/** Categories rendered by this baseline. */
export type SettingsTab = Exclude<SettingsCategory, 'shortcuts'>;

export const SETTINGS_TABS: readonly SettingsTab[] = SETTINGS_CATEGORY_ORDER.filter(
  (category): category is SettingsTab => category !== 'shortcuts',
);
