// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { SETTINGS_CATEGORY_ORDER } from './settingsCategories';
import {
  SETTINGS_SEARCH_ENTRIES,
  findSettingsGroupHeading,
  normalizeSettingsSearchText,
  searchSettings,
} from './settingsSearch';

const FULL_ACCESS = { remoteTunnel: true, authorizationLoggedIn: true };
const NO_ACCESS = { remoteTunnel: false, authorizationLoggedIn: false };

function hitIds(query: string, context = FULL_ACCESS): string[] {
  return searchSettings(SETTINGS_SEARCH_ENTRIES, query, context).map((entry) => entry.id);
}

describe('settings search index', () => {
  it('keeps ids unique', () => {
    const ids = SETTINGS_SEARCH_ENTRIES.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('never repeats the same tab and group', () => {
    const keys = SETTINGS_SEARCH_ENTRIES.map((entry) => `${entry.tab}:${entry.group}`);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('covers every settings tab and only known tabs', () => {
    for (const tab of SETTINGS_CATEGORY_ORDER) {
      expect(SETTINGS_SEARCH_ENTRIES.some((entry) => entry.tab === tab)).toBe(true);
    }
    for (const entry of SETTINGS_SEARCH_ENTRIES) {
      expect(SETTINGS_CATEGORY_ORDER).toContain(entry.tab);
      expect(entry.group.trim().length).toBeGreaterThan(0);
      expect(entry.keywords.length).toBeGreaterThan(0);
    }
  });
});

describe('searchSettings', () => {
  it('returns nothing for an empty or blank query so the original UI stays', () => {
    expect(searchSettings(SETTINGS_SEARCH_ENTRIES, '', FULL_ACCESS)).toEqual([]);
    expect(searchSettings(SETTINGS_SEARCH_ENTRIES, '   ', FULL_ACCESS)).toEqual([]);
  });

  it('matches a group name and ranks it first', () => {
    expect(hitIds('sidebar')[0]).toBe('sidebar');
    expect(hitIds('history search')[0]).toBe('history-search');
  });

  it('matches row keywords and tab labels', () => {
    expect(hitIds('illegal queue')[0]).toBe('illegal-queue-safety');
    expect(hitIds('cloudflared')).toContain('remote-tunnel');
    expect(hitIds('timeout_sec')).toContain('worker-config');
    expect(hitIds('adapters')).toContain('codex-adapter');
    expect(hitIds('授权')).toContain('local-authorization');
  });

  it('requires every token, so extra words narrow the result', () => {
    const broad = hitIds('locker');
    const narrow = hitIds('locker desktop');
    expect(broad.length).toBeGreaterThan(narrow.length);
    expect(narrow).toEqual(['locker-desktop']);
  });

  it('returns no hits for unknown text', () => {
    expect(hitIds('definitely-not-a-setting')).toEqual([]);
  });

  it('hides groups the current config or permission keeps closed', () => {
    expect(hitIds('tunnel', NO_ACCESS)).toEqual([]);
    expect(hitIds('tunnel', FULL_ACCESS)).toContain('remote-tunnel');
    expect(hitIds('change password', NO_ACCESS)).toEqual([]);
    expect(hitIds('change password', FULL_ACCESS)).toContain('change-password');
    expect(hitIds('permissions', NO_ACCESS)).toEqual([]);
    expect(hitIds('permissions', FULL_ACCESS)).toContain('agent-permissions');
    // Always-rendered groups stay searchable without any permission.
    expect(hitIds('sidebar', NO_ACCESS)).toEqual(['sidebar']);
  });
});

describe('normalizeSettingsSearchText', () => {
  it('trims, lowercases and collapses whitespace', () => {
    expect(normalizeSettingsSearchText('  Session   Legal-State  ')).toBe('session legal-state');
  });
});

describe('findSettingsGroupHeading', () => {
  it('matches a heading regardless of case and spacing', () => {
    const root = document.createElement('div');
    root.innerHTML = '<h3>  Session   list </h3>';
    expect(findSettingsGroupHeading(root, 'Session list')).toBe(root.querySelector('h3'));
  });

  it('prefers an exact heading over a prefixed one', () => {
    const root = document.createElement('div');
    root.innerHTML = '<h2>Data locations extra</h2><h3>Data locations</h3>';
    expect(findSettingsGroupHeading(root, 'Data locations')).toBe(root.querySelector('h3'));
  });

  it('accepts collapsible headings that wrap a description', () => {
    const root = document.createElement('div');
    root.innerHTML = '<h3><button><span>Remote / Tunnel</span><span>cloudflared 隧道</span></button></h3>';
    expect(findSettingsGroupHeading(root, 'Remote / Tunnel')).toBe(root.querySelector('h3'));
  });

  it('returns null when no heading matches', () => {
    const root = document.createElement('div');
    root.innerHTML = '<h3>Sidebar</h3>';
    expect(findSettingsGroupHeading(root, 'Missing group')).toBeNull();
    expect(findSettingsGroupHeading(root, '')).toBeNull();
  });
});
