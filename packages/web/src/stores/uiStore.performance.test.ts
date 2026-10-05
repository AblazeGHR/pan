import { afterEach, describe, expect, it, vi } from 'vitest';
import { useUIStore } from './uiStore';

describe('sidebar maintenance notifications', () => {
  afterEach(() => {
    useUIStore.setState({ collapsedGroups: new Set(), hiddenSessionIds: new Set() });
  });

  it('does not notify subscribers when repeated summary maintenance changes nothing', () => {
    useUIStore.setState({
      collapsedGroups: new Set(['ses_parent', '/project']),
      hiddenSessionIds: new Set(['ses_hidden']),
    });
    const initial = useUIStore.getState();
    const listener = vi.fn();
    const unsubscribe = useUIStore.subscribe(listener);
    try {
      for (let update = 0; update < 100; update += 1) {
        initial.pruneCollapsedGroups(new Set(['ses_parent', '/project']));
        initial.pruneHiddenSessions(new Set(['ses_hidden']));
      }
      initial.collapseAllGroups(['ses_parent']);
      initial.expandAllGroups(['not-collapsed']);
      expect(listener).not.toHaveBeenCalled();
      expect(useUIStore.getState()).toBe(initial);
    } finally {
      unsubscribe();
    }
  });

  it('still notifies and removes stale keys when maintenance changes state', () => {
    useUIStore.setState({
      collapsedGroups: new Set(['ses_live', 'ses_deleted', '/project']),
      hiddenSessionIds: new Set(['ses_live', 'ses_deleted']),
    });
    const listener = vi.fn();
    const unsubscribe = useUIStore.subscribe(listener);
    try {
      useUIStore.getState().pruneCollapsedGroups(new Set(['ses_live', '/project']));
      useUIStore.getState().pruneHiddenSessions(new Set(['ses_live']));
      expect(listener).toHaveBeenCalledTimes(2);
      expect([...useUIStore.getState().collapsedGroups]).toEqual(['ses_live', '/project']);
      expect([...useUIStore.getState().hiddenSessionIds]).toEqual(['ses_live']);
    } finally {
      unsubscribe();
    }
  });
});
