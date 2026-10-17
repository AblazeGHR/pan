import { describe, expect, it } from 'vitest';
import {
  collectAdapterOptions,
  computeWorkspaceRowVisibility,
  getSessionListCandidates,
  matchesAdapterFilter,
  workspaceMatchesAdapterFilter,
} from './sessionFilters';
import type { Session, Workspace } from '@/types';

function session(overrides: Partial<Session> = {}): Session {
  return {
    id: 'pan-session-123',
    name: 'Release planning',
    cliSessionId: 'CLI-Session-ABC',
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    ...overrides,
  };
}

function workspace(id: string, name: string): Workspace {
  return { id, name, order: null };
}

describe('collectAdapterOptions', () => {
  it('collects distinct adapter names from the live session list, sorted', () => {
    const options = collectAdapterOptions([
      session({ id: 'a', adapter: 'kimi' }),
      session({ id: 'b', adapter: 'cbc' }),
      session({ id: 'c', adapter: 'codex' }),
      session({ id: 'd', adapter: 'cbc' }),
    ]);
    expect(options).toEqual(['cbc', 'codex', 'kimi']);
  });

  it('skips missing and blank adapter names', () => {
    const options = collectAdapterOptions([
      session({ id: 'a' }),
      session({ id: 'b', adapter: '' }),
      session({ id: 'c', adapter: '   ' }),
      session({ id: 'd', adapter: 'claude' }),
    ]);
    expect(options).toEqual(['claude']);
  });

  it('trims surrounding whitespace so a padded name is one option', () => {
    const options = collectAdapterOptions([
      session({ id: 'a', adapter: ' codex ' }),
      session({ id: 'b', adapter: 'codex' }),
    ]);
    expect(options).toEqual(['codex']);
  });

  it('is stable regardless of input order', () => {
    const forward = collectAdapterOptions([
      session({ id: 'a', adapter: 'opencode' }),
      session({ id: 'b', adapter: 'cbc' }),
    ]);
    const reversed = collectAdapterOptions([
      session({ id: 'b', adapter: 'cbc' }),
      session({ id: 'a', adapter: 'opencode' }),
    ]);
    expect(forward).toEqual(reversed);
  });

  it('derives options from real data — an unseen adapter appears on its own', () => {
    // No hardcoded registry: whatever the sessions carry becomes an option.
    expect(collectAdapterOptions([session({ id: 'a', adapter: 'future-adapter' })])).toEqual([
      'future-adapter',
    ]);
  });
});

describe('matchesAdapterFilter', () => {
  it('filters nothing when no adapter is selected (undefined or empty)', () => {
    const withoutAdapter = session({ id: 'a' });
    expect(matchesAdapterFilter(withoutAdapter, undefined)).toBe(true);
    expect(matchesAdapterFilter(withoutAdapter, new Set())).toBe(true);
    expect(matchesAdapterFilter(session({ id: 'b', adapter: 'cbc' }), new Set())).toBe(true);
  });

  it('matches only the selected adapter', () => {
    const selected = new Set(['codex']);
    expect(matchesAdapterFilter(session({ id: 'a', adapter: 'codex' }), selected)).toBe(true);
    expect(matchesAdapterFilter(session({ id: 'b', adapter: 'cbc' }), selected)).toBe(false);
  });

  it('ORs multiple selections inside the group', () => {
    const selected = new Set(['codex', 'kimi']);
    expect(matchesAdapterFilter(session({ id: 'a', adapter: 'codex' }), selected)).toBe(true);
    expect(matchesAdapterFilter(session({ id: 'b', adapter: 'kimi' }), selected)).toBe(true);
    expect(matchesAdapterFilter(session({ id: 'c', adapter: 'cbc' }), selected)).toBe(false);
  });

  it('never matches a session without an adapter while a selection is active', () => {
    expect(matchesAdapterFilter(session({ id: 'a' }), new Set(['cbc']))).toBe(false);
    expect(matchesAdapterFilter(session({ id: 'b', adapter: undefined }), new Set(['cbc']))).toBe(false);
  });

  it('compares trimmed adapter names', () => {
    expect(matchesAdapterFilter(session({ id: 'a', adapter: ' codex ' }), new Set(['codex']))).toBe(true);
  });

  it('matches nothing for a stale selection id (adapter no longer present)', () => {
    const selected = new Set(['gone-adapter']);
    expect(matchesAdapterFilter(session({ id: 'a', adapter: 'cbc' }), selected)).toBe(false);
  });
});

describe('getSessionListCandidates with adapter filters', () => {
  const sessions = [
    session({ id: 'cbc-1', name: 'Cbc one', adapter: 'cbc' }),
    session({ id: 'cbc-2', name: 'Cbc two', adapter: 'cbc', workerStatus: 'running' }),
    session({ id: 'codex-1', name: 'Codex one', adapter: 'codex' }),
    session({ id: 'kimi-1', name: 'Kimi one', adapter: 'kimi' }),
    session({ id: 'none-1', name: 'No adapter' }),
  ];
  const base: Parameters<typeof getSessionListCandidates>[1] = {
    multiSelectMode: false,
    hiddenSessionIds: new Set<string>(),
    searchQuery: '',
    specialFilters: new Set(),
  };

  it('keeps only the selected adapter', () => {
    const ids = getSessionListCandidates(sessions, { ...base, adapterFilters: new Set(['codex']) }).map((s) => s.id);
    expect(ids).toEqual(['codex-1']);
  });

  it('ORs adapters inside the group', () => {
    const ids = getSessionListCandidates(sessions, {
      ...base,
      adapterFilters: new Set(['codex', 'kimi']),
    }).map((s) => s.id);
    expect(ids).toEqual(['codex-1', 'kimi-1']);
  });

  it('ANDs across groups: adapter OR-set + state AND-set', () => {
    // codex + running → only a RUNNING codex session; cbc-2 runs but is not codex.
    const ids = getSessionListCandidates(sessions, {
      ...base,
      adapterFilters: new Set(['cbc', 'codex']),
      specialFilters: new Set(['running']),
    }).map((s) => s.id);
    expect(ids).toEqual(['cbc-2']);
  });

  it('composes with the text search (AND)', () => {
    const ids = getSessionListCandidates(sessions, {
      ...base,
      adapterFilters: new Set(['cbc']),
      searchQuery: 'two',
    }).map((s) => s.id);
    expect(ids).toEqual(['cbc-2']);
  });

  it('still respects the workspace scope as the outermost filter', () => {
    const scoped = sessions.map((s, index) =>
      index < 2 ? s : { ...s, workspaceIds: ['ws-1'] },
    );
    const ids = getSessionListCandidates(scoped, {
      ...base,
      adapterFilters: new Set(['cbc', 'codex']),
      activeWorkspaceId: 'ws-1',
    }).map((s) => s.id);
    expect(ids).toEqual(['codex-1']);
  });

  it('select mode includes hidden sessions but still applies the adapter filter', () => {
    const ids = getSessionListCandidates(sessions, {
      ...base,
      multiSelectMode: true,
      hiddenSessionIds: new Set(['cbc-1']),
      adapterFilters: new Set(['cbc']),
    }).map((s) => s.id);
    expect(ids).toEqual(['cbc-1', 'cbc-2']);
  });

  it('returns an empty list when the combination matches nothing', () => {
    const ids = getSessionListCandidates(sessions, {
      ...base,
      adapterFilters: new Set(['kimi']),
      specialFilters: new Set(['running']),
    }).map((s) => s.id);
    expect(ids).toEqual([]);
  });

  it('clearing every filter restores the full list', () => {
    const ids = getSessionListCandidates(sessions, {
      ...base,
      adapterFilters: new Set(),
      specialFilters: new Set(),
    }).map((s) => s.id);
    expect(ids).toEqual(sessions.map((s) => s.id));
  });

  it('legacy callers that omit adapterFilters keep the exact previous behavior', () => {
    // The pre-feature option shape: no adapterFilters key at all.
    const legacy = (options: Parameters<typeof getSessionListCandidates>[1]) =>
      getSessionListCandidates(sessions, options);
    const withoutKey = legacy({
      multiSelectMode: false,
      hiddenSessionIds: new Set<string>(),
      searchQuery: '',
      specialFilters: new Set(),
    }).map((s) => s.id);
    expect(withoutKey).toEqual(sessions.map((s) => s.id));

    const withRunning = legacy({
      multiSelectMode: false,
      hiddenSessionIds: new Set<string>(),
      searchQuery: '',
      specialFilters: new Set(['running']),
    }).map((s) => s.id);
    expect(withRunning).toEqual(['cbc-2']);
  });
});

describe('workspaceMatchesAdapterFilter', () => {
  it('imposes no constraint without a selection', () => {
    expect(workspaceMatchesAdapterFilter([session({ id: 'a', adapter: 'cbc' })], new Set())).toBe(true);
    expect(workspaceMatchesAdapterFilter([], undefined)).toBe(true);
  });

  it('matches when any member carries a selected adapter', () => {
    const members = [
      session({ id: 'a', adapter: 'cbc' }),
      session({ id: 'b', adapter: 'kimi' }),
    ];
    expect(workspaceMatchesAdapterFilter(members, new Set(['kimi']))).toBe(true);
    expect(workspaceMatchesAdapterFilter(members, new Set(['codex']))).toBe(false);
  });
});

describe('computeWorkspaceRowVisibility with adapter filters', () => {
  const sessions = [
    session({ id: 'a', name: 'Alpha', adapter: 'cbc' }),
    session({ id: 'b', name: 'Bravo', adapter: 'codex' }),
    session({ id: 'c', name: 'Charlie', adapter: 'kimi' }),
  ];
  const workspaces = [workspace('ws-1', 'Alpha WS'), workspace('ws-2', 'Bravo WS')];
  const membersByWorkspace = new Map<string, Session[]>([
    ['ws-1', [sessions[0]!]],
    ['ws-2', [sessions[1]!]],
  ]);
  const ungroupedMembers = [sessions[2]!];
  const baseOptions: Parameters<typeof computeWorkspaceRowVisibility>[2] = {
    expanded: true,
    searchQuery: '',
    specialFilters: new Set(),
    membersByWorkspace,
    ungroupedMembers,
  };

  it('hides workspaces whose members carry no selected adapter', () => {
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      adapterFilters: new Set(['codex']),
    });
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws-2']);
  });

  it('keeps a workspace when any member matches (OR inside the group)', () => {
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      adapterFilters: new Set(['cbc', 'kimi']),
    });
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws-1']);
  });

  it('drives the virtual all/ungrouped rows from real member matches', () => {
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      adapterFilters: new Set(['kimi']),
    });
    expect(visibility.showAll).toBe(true);
    expect(visibility.showUngrouped).toBe(true);

    const withoutUngrouped = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      adapterFilters: new Set(['cbc']),
    });
    expect(withoutUngrouped.showAll).toBe(true);
    expect(withoutUngrouped.showUngrouped).toBe(false);
  });

  it('ANDs the adapter group with the status filters across groups', () => {
    const running = [
      session({ id: 'a', name: 'Alpha', adapter: 'cbc' }),
      session({ id: 'b', name: 'Bravo', adapter: 'codex', workerStatus: 'running' }),
    ];
    const visibility = computeWorkspaceRowVisibility(workspaces, running, {
      ...baseOptions,
      specialFilters: new Set(['running']),
      adapterFilters: new Set(['codex', 'cbc']),
      membersByWorkspace: new Map([
        ['ws-1', [running[0]!]],
        ['ws-2', [running[1]!]],
      ]),
      ungroupedMembers: [],
    });
    // ws-1 has a cbc member (adapter match) but none running (status AND fails).
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws-2']);
  });

  it('keeps every row without a selection (legacy behavior)', () => {
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, baseOptions);
    expect(visibility.workspaces).toHaveLength(2);
    expect(visibility.showAll).toBe(true);
    expect(visibility.showUngrouped).toBe(true);
  });

  it('legacy callers that omit adapterFilters keep the exact previous behavior', () => {
    // The pre-feature option shape: no adapterFilters key at all.
    const legacyOptions: Parameters<typeof computeWorkspaceRowVisibility>[2] = {
      expanded: true,
      searchQuery: 'alpha',
      specialFilters: new Set(),
      membersByWorkspace,
      ungroupedMembers,
    };
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, legacyOptions);
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws-1']);
    expect(visibility.showUngrouped).toBe(true);
  });
});
