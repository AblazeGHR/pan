import { SETTINGS_CATEGORY_LABELS, SETTINGS_CATEGORY_ORDER, type SettingsCategory } from './settingsCategories';
import { SETTING_DESCRIPTIONS, settingAvailable, type SettingDescription } from './settingsCatalog';
export type { SettingsSearchContext } from './settingsCatalog';
import type { SettingsSearchContext } from './settingsCatalog';

export interface SettingsSearchEntry {
  id: string;
  tab: SettingsCategory;
  group: string;
  detail: string;
  keywords: string[];
  anchor: string;
  jump?: 'group' | 'tab';
  available?: (context: SettingsSearchContext) => boolean;
}

/** Every UI description is indexed automatically, including newly added fields. */
export function createSettingsIndex(descriptions: readonly SettingDescription[]): SettingsSearchEntry[] {
  return descriptions.filter((description) => description.searchable !== false).map((description) => ({
    id: description.id, tab: description.tab, group: description.label,
    detail: description.hint ?? description.label,
    keywords: [...(description.aliases ?? []), ...descriptions
      .filter((other) => other !== description && other.searchable !== false
        && other.anchor === (description.anchor ?? description.id)
        && other.gate === description.gate && other.capability === description.capability)
      .flatMap((other) => [other.label, other.hint ?? ''])],
    anchor: description.anchor ?? description.id,
    available: (context) => settingAvailable(description, context),
  }));
}
export const SETTINGS_SEARCH_ENTRIES = createSettingsIndex(SETTING_DESCRIPTIONS);

/** Stable identity is independent of the label, hint and translated wording. */
export function findSettingsAnchor(root: ParentNode, entry: Pick<SettingsSearchEntry, 'anchor'>): HTMLElement | null {
  return Array.from(root.querySelectorAll<HTMLElement>('[data-settings-id]'))
    .find(element => element.dataset.settingsId === entry.anchor) ?? null;
}

/** Case- and whitespace-insensitive form used by both search and jump. */
export function normalizeSettingsSearchText(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, ' ');
}

function entryHaystack(entry: SettingsSearchEntry): string {
  return normalizeSettingsSearchText(
    [SETTINGS_CATEGORY_LABELS[entry.tab], entry.group, entry.detail, ...entry.keywords].join(' '),
  );
}

function scoreEntry(entry: SettingsSearchEntry, tokens: string[]): number | null {
  const haystack = entryHaystack(entry);
  const normalizedGroup = normalizeSettingsSearchText(entry.group);
  const normalizedDetail = normalizeSettingsSearchText(entry.detail);
  let score = 0;
  for (const token of tokens) {
    if (!haystack.includes(token)) return null;
    if (normalizedGroup.startsWith(token)) score += 4;
    else if (normalizedGroup.includes(token)) score += 3;
    else if (normalizedDetail.includes(token)) score += 2;
    else score += 1;
  }
  return score;
}

/**
 * Rank matching groups. Every whitespace-separated token must appear in the tab
 * label, group, detail or keywords, so extra words narrow instead of widen.
 */
export function searchSettings(
  entries: readonly SettingsSearchEntry[],
  query: string,
  context: SettingsSearchContext,
): SettingsSearchEntry[] {
  const tokens = normalizeSettingsSearchText(query).split(' ').filter(Boolean);
  if (tokens.length === 0) return [];

  return entries
    .map((entry, index) => ({ entry, index }))
    .filter(({ entry }) => (entry.available ? entry.available(context) : true))
    .map(({ entry, index }) => ({ entry, index, score: scoreEntry(entry, tokens) }))
    .filter(
      (hit): hit is { entry: SettingsSearchEntry; index: number; score: number } =>
        hit.score !== null,
    )
    .sort((left, right) => {
      if (right.score !== left.score) return right.score - left.score;
      const leftOrder = SETTINGS_CATEGORY_ORDER.indexOf(left.entry.tab) * 1000 + left.index;
      const rightOrder = SETTINGS_CATEGORY_ORDER.indexOf(right.entry.tab) * 1000 + right.index;
      return leftOrder - rightOrder;
    })
    .map((hit) => hit.entry);
}

/**
 * Find the rendered group heading a search hit should scroll to. Headings that
 * wrap extra copy (collapsible Remote sections) keep the group name as a prefix.
 */
export function findSettingsGroupHeading(root: HTMLElement, group: string): HTMLElement | null {
  const target = normalizeSettingsSearchText(group);
  if (!target) return null;
  const headings = Array.from(
    root.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, legend'),
  ).map((heading) => ({ heading, text: normalizeSettingsSearchText(heading.textContent ?? '') }));

  const exact = headings.find((candidate) => candidate.text === target);
  if (exact) return exact.heading;
  const prefixed = headings.find((candidate) => candidate.text.startsWith(target));
  return prefixed ? prefixed.heading : null;
}
