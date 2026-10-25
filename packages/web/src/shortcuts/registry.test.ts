import { beforeEach, describe, expect, it } from 'vitest';
import { entries, mergeViewEntries } from '@/components/layout/viewEntries';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import {
  buildPanelShortcutActions,
  buildShortcutActions,
  getShortcutActions,
} from './registry';

const panel = (pluginId: string, id: string, title: string) => ({
  pluginId,
  id,
  title,
  renderer: 'account-switcher/v1' as const,
  path: `/plugins/${pluginId}/${id}`,
  actions: {},
});

beforeEach(() => {
  usePluginPanelStore.setState({ panels: [], loading: false, loaded: true, error: null });
});

describe('dynamic panel shortcut registry', () => {
  it('uses pluginId/panelId identities and never array positions', () => {
    const merged = mergeViewEntries(entries, [
      panel('alpha', 'one', 'Alpha One'),
      panel('beta', 'two', 'Beta Two'),
    ]);
    expect(merged.map((entry) => entry.id)).toEqual([
      'builtin:chat',
      'builtin:editor',
      'builtin:jobs',
      'builtin:terminals',
      'builtin:locker',
      'builtin:actions',
      'builtin:automation',
      'plugin:alpha/one',
      'plugin:beta/two',
    ]);

    const actions = buildPanelShortcutActions(merged);
    expect(actions.find((action) => action.id === 'panel:builtin:chat')?.defaultBinding)
      .toBe('Mod+1');
    expect(actions.find((action) => action.id === 'panel:builtin:editor')?.defaultBinding)
      .toBe('Mod+2');
    expect(actions.find((action) => action.id === 'panel:builtin:jobs')?.defaultBinding)
      .toBeNull();
    expect(actions.find((action) => action.id === 'panel:plugin:alpha/one')?.defaultBinding)
      .toBeNull();

    // Reordering the live catalog must not change any action identity.
    const reversed = mergeViewEntries(entries, [
      panel('beta', 'two', 'Beta Two'),
      panel('alpha', 'one', 'Alpha One'),
    ]);
    expect(buildPanelShortcutActions(reversed).map((action) => action.id).sort())
      .toEqual(actions.map((action) => action.id).sort());
  });

  it('skips malformed/duplicate panels without throwing', () => {
    const merged = mergeViewEntries(entries, [
      panel('alpha', 'one', 'Alpha One'),
      panel('alpha', 'one', 'Duplicate'),
      { pluginId: 'bad', id: '', path: '/plugins/bad/' } as never,
      { pluginId: 'bad', id: 'no-path' } as never,
      null as never,
    ]);
    expect(merged).toHaveLength(8);
    expect(merged.filter((entry) => entry.id === 'plugin:alpha/one')).toHaveLength(1);
  });

  it('adds and removes actions from the live store without disturbing others', () => {
    usePluginPanelStore.setState({
      panels: [panel('alpha', 'one', 'Alpha One'), panel('beta', 'two', 'Beta Two')],
    });
    const before = getShortcutActions().filter((action) => action.group === 'Panels');
    expect(before.map((action) => action.id)).toContain('panel:plugin:alpha/one');
    expect(before.map((action) => action.id)).toContain('panel:plugin:beta/two');

    usePluginPanelStore.setState({ panels: [panel('beta', 'two', 'Beta Two')] });
    const after = getShortcutActions().filter((action) => action.group === 'Panels');
    expect(after.map((action) => action.id)).not.toContain('panel:plugin:alpha/one');
    expect(after.map((action) => action.id)).toContain('panel:plugin:beta/two');
    expect(after.find((action) => action.id === 'panel:builtin:chat')?.defaultBinding)
      .toBe('Mod+1');
  });

  it('keeps the full action set in stable groups with safe defaults', () => {
    const actions = buildShortcutActions(entries);
    expect(actions.map((action) => action.id)).toEqual(
      expect.arrayContaining([
        'app.toggleSidebar',
        'app.commandPalette',
        'app.historySearch',
        'app.globalHistorySearch',
        'queue.selectPrevious',
        'queue.selectNext',
        'queue.editSelected',
        'queue.toggleSelectedLock',
        'queue.togglePanel',
        'queue.enqueueLocked',
        'queue.lockAll',
        'queue.pauseAll',
        'session.toggleStopReport',
        'session.toggleNotification',
      ]),
    );
    // Queue-row navigation ships bound (it is scoped to the open panel and
    // releases the chord when disabled); the destructive queue/session actions
    // still start unbound.
    const queueDefaults: Record<string, string> = {
      'queue.selectPrevious': 'Alt+ArrowUp',
      'queue.selectNext': 'Alt+ArrowDown',
      'queue.editSelected': 'Alt+Enter',
      'queue.toggleSelectedLock': 'Alt+L',
    };
    for (const action of actions) {
      if (action.id.startsWith('session.')) {
        expect(action.defaultBinding, action.id).toBeNull();
      }
      if (action.id.startsWith('queue.')) {
        expect(action.defaultBinding, action.id).toBe(queueDefaults[action.id] ?? null);
      }
    }
    expect(actions.find((action) => action.id === 'app.historySearch')?.defaultBinding).toBe('Mod+F');
    expect(actions.find((action) => action.id === 'app.globalHistorySearch')?.defaultBinding).toBe('Mod+G');
  });
});
