// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { SETTINGS_CATEGORY_ORDER } from './settingsCategories';
import {
  SETTINGS_SEARCH_ENTRIES,
  createSettingsIndex,
  findSettingsAnchor,
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

  it('keeps different fields with the same caption independently addressable', () => {
    const fields = SETTINGS_SEARCH_ENTRIES.filter((entry) => entry.group === 'System');
    expect(fields.map((entry) => entry.anchor)).toContain('auto-report.system');
    expect(fields.map((entry) => entry.anchor)).toContain('prompt-summary.system');
    expect(new Set(fields.map((entry) => entry.anchor)).size).toBe(fields.length);
  });

  it('covers every settings tab and only known tabs', () => {
    for (const tab of SETTINGS_CATEGORY_ORDER) {
      expect(SETTINGS_SEARCH_ENTRIES.some((entry) => entry.tab === tab)).toBe(true);
    }
    for (const entry of SETTINGS_SEARCH_ENTRIES) {
      expect(SETTINGS_CATEGORY_ORDER).toContain(entry.tab);
      expect(entry.group.trim().length).toBeGreaterThan(0);
      expect(entry.anchor.length).toBeGreaterThan(0);
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
    expect(hitIds('tunnel', NO_ACCESS)).not.toContain('remote-tunnel');
    expect(hitIds('tunnel', FULL_ACCESS)).toContain('remote-tunnel');
    expect(hitIds('change password', NO_ACCESS)).not.toContain('change-password');
    expect(hitIds('new password', NO_ACCESS)).not.toContain('authorization-settings.14');
    expect(hitIds('change password', FULL_ACCESS)).toContain('change-password');
    expect(hitIds('permissions', NO_ACCESS)).toEqual([]);
    expect(hitIds('permissions', FULL_ACCESS)).toContain('agent-permissions');
    // Always-rendered groups stay searchable without any permission.
    expect(hitIds('sidebar', NO_ACCESS)).toContain('sidebar');
  });
});

describe('shared field descriptions', () => {
  it('does not leak hidden operational prose through a shared anchor', () => {
    const entries = createSettingsIndex([
      { id: 'visible.setting', tab: 'behavior', label: 'Visible setting', anchor: 'stable.field' },
      { id: 'operation.status', tab: 'behavior', label: 'Secret pending result', anchor: 'stable.field', searchable: false },
      { id: 'authorized.field', tab: 'behavior', label: 'Private configuration', anchor: 'stable.field', gate: 'authorized' },
    ]);
    expect(searchSettings(entries, 'secret pending', NO_ACCESS)).toEqual([]);
    expect(searchSettings(entries, 'private configuration', NO_ACCESS)).toEqual([]);
    expect(searchSettings(entries, 'visible setting', NO_ACCESS).map(entry => entry.id)).toEqual(['visible.setting']);
  });

  it('indexes a new field label, hint and aliases without a second search registration', () => {
    const description = { id: 'future.setting', tab: 'behavior' as const,
      label: 'Future setting', hint: 'Preserve pending messages', aliases: ['future policy'] };
    const entries = createSettingsIndex([description]);
    expect(searchSettings(entries, 'future messages', FULL_ACCESS).map((entry) => entry.id)).toEqual(['future.setting']);
    expect(searchSettings(entries, 'future policy', FULL_ACCESS)).toHaveLength(1);
    const root = document.createElement('div');
    root.innerHTML = '<label data-settings-id="future.setting"><input type="checkbox" />Renamed label</label>';
    expect(findSettingsAnchor(root, entries[0]!)).toBe(root.firstElementChild);
  });

  it('uses capability visibility before searching the actual field text', () => {
    const entries = createSettingsIndex([{ id: 'native.fixture', tab: 'adapters', label: 'Native fixture',
      capability: 'terminal.native' }]);
    expect(searchSettings(entries, 'native fixture', { ...FULL_ACCESS, capabilities: { 'terminal.native': false } })).toEqual([]);
    expect(searchSettings(entries, 'native fixture', { ...FULL_ACCESS, capabilities: { 'terminal.native': true } })).toHaveLength(1);
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
