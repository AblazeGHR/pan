import { describe, expect, it } from 'vitest';
import {
  computeWorkspaceRowVisibility,
  matchesSessionSearch,
  matchesWorkspaceSearch,
  workspaceMatchesSpecialFilters,
} from './sessionFilters';
import type { Session, Workspace } from '@/types';

/** Minimal Session for filter tests (only the fields the filters read). */
function makeSession(id: string, overrides: Partial<Session> = {}): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: 'medium',
    history: [],
    ...overrides,
  } as unknown as Session;
}

function makeWorkspace(id: string, name: string): Workspace {
  return { id, name, order: null };
}

describe('matchesWorkspaceSearch', () => {
  it('matches every workspace for an empty or whitespace query', () => {
    expect(matchesWorkspaceSearch(makeWorkspace('ws_1', 'Alpha WS'), '')).toBe(true);
    expect(matchesWorkspaceSearch(makeWorkspace('ws_1', 'Alpha WS'), '   ')).toBe(true);
  });

  it('matches the workspace name case-insensitively as a substring', () => {
    expect(matchesWorkspaceSearch(makeWorkspace('ws_1', 'Alpha WS'), 'alpha')).toBe(true);
    expect(matchesWorkspaceSearch(makeWorkspace('ws_1', 'Alpha WS'), 'LPHA W')).toBe(true);
    expect(matchesWorkspaceSearch(makeWorkspace('ws_1', 'Alpha WS'), ' alpha ')).toBe(true);
  });

  it('does not match unrelated names', () => {
    expect(matchesWorkspaceSearch(makeWorkspace('ws_1', 'Alpha WS'), 'beta')).toBe(false);
    expect(matchesWorkspaceSearch(makeWorkspace('ws_1', 'Alpha WS'), 'alphax')).toBe(false);
  });

  it('does not match workspace IDs', () => {
    expect(matchesWorkspaceSearch(makeWorkspace('secret-id', 'Alpha'), 'secret-id')).toBe(false);
  });

  it('never matches a missing name for a non-empty query', () => {
    expect(matchesWorkspaceSearch({ id: 'ws_1' } as unknown as Workspace, 'alpha')).toBe(false);
  });
});

describe('workspaceMatchesSpecialFilters', () => {
  const sessions = [
    makeSession('root', { workspaceIds: ['ws_1'], workerStatus: 'idle' }),
    makeSession('child', { managedBy: 'root', workerStatus: 'running' }),
    makeSession('loose', { workerStatus: 'running', unreadDoneCount: 2, queuePendingCount: 1 }),
    makeSession('illegal-one', {
      workspaceIds: ['ws_2'],
      lastLegalWorkerState: 'running',
      workerStatus: 'idle',
      illegalOffline: true,
    }),
  ];

  it('passes with no active filters even without members', () => {
    expect(workspaceMatchesSpecialFilters([], sessions, new Set())).toBe(true);
  });

  it('aggregates the running state over any member', () => {
    expect(workspaceMatchesSpecialFilters([sessions[0]!, sessions[1]!], sessions, new Set(['running']))).toBe(true);
    expect(workspaceMatchesSpecialFilters([sessions[0]!], sessions, new Set(['running']))).toBe(false);
  });

  it('aggregates unread over any member with a positive authoritative count', () => {
    expect(workspaceMatchesSpecialFilters([sessions[0]!], sessions, new Set(['unread']))).toBe(false);
    expect(workspaceMatchesSpecialFilters([sessions[2]!], sessions, new Set(['unread']))).toBe(true);
    // Zero / non-finite counts are not unread.
    const zero = makeSession('zero', { unreadDoneCount: 0 });
    expect(workspaceMatchesSpecialFilters([zero], sessions, new Set(['unread']))).toBe(false);
  });

  it('aggregates queued messages over any member', () => {
    expect(workspaceMatchesSpecialFilters([sessions[0]!], sessions, new Set(['queue']))).toBe(false);
    expect(workspaceMatchesSpecialFilters([sessions[2]!], sessions, new Set(['queue']))).toBe(true);
  });

  it('aggregates the illegal state over any member', () => {
    expect(workspaceMatchesSpecialFilters([sessions[3]!], sessions, new Set(['illegal']))).toBe(true);
    // A session that is actually running is legal, never illegal.
    expect(workspaceMatchesSpecialFilters([sessions[2]!], sessions, new Set(['illegal']))).toBe(false);
  });

  it('requires a satisfying member for EVERY active filter (per-filter any, then AND)', () => {
    const runningOnly = [sessions[1]!];
    const unreadOnly = [makeSession('unread-only', { unreadDoneCount: 3 })];
    expect(workspaceMatchesSpecialFilters(runningOnly, sessions, new Set(['running', 'unread']))).toBe(false);
    expect(workspaceMatchesSpecialFilters(unreadOnly, sessions, new Set(['running', 'unread']))).toBe(false);
    const both = [makeSession('both', { workerStatus: 'running', unreadDoneCount: 1 })];
    expect(workspaceMatchesSpecialFilters(both, sessions, new Set(['running', 'unread']))).toBe(true);
    expect(workspaceMatchesSpecialFilters(unreadOnly, sessions, new Set(['running', 'unread']))).toBe(false);
  });

  it('lets a DIFFERENT member satisfy each filter (workspace-level AND)', () => {
    const running = makeSession('m-running', { workerStatus: 'running' });
    const unread = makeSession('m-unread', { unreadDoneCount: 2 });
    const queued = makeSession('m-queued', { queuePendingCount: 1 });
    const illegal = makeSession('m-illegal', { lastLegalWorkerState: 'running', workerStatus: 'idle', illegalOffline: true });
    const members = [running, unread, queued, illegal];

    // Every selected filter is met by some member, though no single member
    // satisfies more than one of them.
    expect(workspaceMatchesSpecialFilters(members, members, new Set(['running', 'unread']))).toBe(true);
    expect(workspaceMatchesSpecialFilters(members, members, new Set(['unread', 'queue']))).toBe(true);
    expect(workspaceMatchesSpecialFilters(members, members, new Set(['queue', 'illegal']))).toBe(true);
    expect(workspaceMatchesSpecialFilters(members, members, new Set(['illegal', 'running']))).toBe(true);
    expect(
      workspaceMatchesSpecialFilters(members, members, new Set(['running', 'unread', 'queue', 'illegal'])),
    ).toBe(true);

    // Removing the member that carries a selected filter fails that filter.
    expect(workspaceMatchesSpecialFilters([running, unread], members, new Set(['running', 'queue']))).toBe(false);
    expect(workspaceMatchesSpecialFilters([queued], members, new Set(['running', 'queue']))).toBe(false);
    expect(workspaceMatchesSpecialFilters([illegal], members, new Set(['illegal', 'unread']))).toBe(false);
    expect(workspaceMatchesSpecialFilters([unread, queued], members, new Set(['illegal', 'unread']))).toBe(false);
  });

  it('preserves all 15 nonempty status combinations across inherited effective members', () => {
    const members = [
      makeSession('root', { workspaceIds: ['ws'], workerStatus: 'running' }),
      makeSession('child-unread', { managedBy: 'root', unreadDoneCount: 1 }),
      makeSession('child-queue', { managedBy: 'root', queuePendingCount: 1 }),
      makeSession('child-illegal', { managedBy: 'root', workerStatus: 'idle', lastLegalWorkerState: 'running', illegalOffline: true }),
    ];
    const filters = ['running', 'unread', 'queue', 'illegal'] as const;
    for (let mask = 1; mask < 16; mask += 1) {
      const selected = new Set(filters.filter((_, index) => mask & (1 << index)));
      const options = {
        expanded: true, searchQuery: 'target', specialFilters: selected,
        membersByWorkspace: new Map([['ws', members]]), ungroupedMembers: [],
      };
      expect(computeWorkspaceRowVisibility([makeWorkspace('ws', 'Target')], members, options).workspaces).toHaveLength(1);
      for (let index = 0; index < filters.length; index += 1) {
        if (!(mask & (1 << index))) continue;
        const remaining = members.filter((_, memberIndex) => memberIndex !== index);
        expect(computeWorkspaceRowVisibility([makeWorkspace('ws', 'Target')], members, {
          ...options, membersByWorkspace: new Map([['ws', remaining]]),
        }).workspaces).toHaveLength(0);
      }
    }
  });

  it('aggregates ACTUAL member states — a session the keyword would hide still counts', () => {
    // 'zzz-not-matching' fails the keyword filter, but its real running state
    // must still aggregate to the workspace (never derive from the
    // keyword-filtered Session list).
    const hiddenByKeyword = [makeSession('zzz-not-matching', { workerStatus: 'running' })];
    expect(matchesSessionSearch(hiddenByKeyword[0]!, 'alpha')).toBe(false);
    expect(workspaceMatchesSpecialFilters(hiddenByKeyword, sessions, new Set(['running']))).toBe(true);
  });
});

describe('computeWorkspaceRowVisibility', () => {
  const alpha = makeWorkspace('ws_alpha', 'Alpha WS');
  const beta = makeWorkspace('ws_beta', 'Beta WS');
  const workspaces = [alpha, beta];

  const sessions = [
    makeSession('w-one', { workspaceIds: ['ws_alpha'], workerStatus: 'idle' }),
    makeSession('w-two', { workspaceIds: ['ws_beta'], workerStatus: 'running' }),
    makeSession('w-loose', { workerStatus: 'idle' }),
  ];
  const membersByWorkspace = new Map<string, Session[]>([
    ['ws_alpha', [sessions[0]!]],
    ['ws_beta', [sessions[1]!]],
  ]);
  const ungroupedMembers = [sessions[2]!];

  const baseOptions = {
    expanded: true,
    searchQuery: '',
    specialFilters: new Set<'running' | 'unread' | 'queue' | 'illegal' | 'subagent' | 'metaagent'>(),
    membersByWorkspace,
    ungroupedMembers,
  };

  it('keeps every row when the rail is collapsed, even with active filters', () => {
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      expanded: false,
      searchQuery: 'alpha',
      specialFilters: new Set(['running']),
    });
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws_alpha', 'ws_beta']);
    expect(visibility.showAll).toBe(true);
    expect(visibility.showUngrouped).toBe(true);
  });

  it('keeps every row with no keyword and no status filter', () => {
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, baseOptions);
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws_alpha', 'ws_beta']);
    expect(visibility.showAll).toBe(true);
    expect(visibility.showUngrouped).toBe(true);
  });

  it('filters workspace rows by name using its independent keyword', () => {
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      searchQuery: 'alpha',
    });
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws_alpha']);
    // Virtual scopes are unaffected by the workspace name query.
    expect(visibility.showAll).toBe(true);
    expect(visibility.showUngrouped).toBe(true);
  });

  it('filters workspace rows by the aggregated member status', () => {
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      specialFilters: new Set(['running']),
    });
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws_beta']);
    // Virtual scopes follow their own session content.
    expect(visibility.showAll).toBe(true); // w-two is running
    expect(visibility.showUngrouped).toBe(false); // w-loose is idle
  });

  it('combines keyword and status with the original AND semantics', () => {
    // Name matches AND a member actually runs → visible.
    expect(
      computeWorkspaceRowVisibility(workspaces, sessions, {
        ...baseOptions,
        searchQuery: 'beta',
        specialFilters: new Set(['running']),
      }).workspaces.map((w) => w.id),
    ).toEqual(['ws_beta']);
    // Name matches but no member runs → hidden.
    expect(
      computeWorkspaceRowVisibility(workspaces, sessions, {
        ...baseOptions,
        searchQuery: 'alpha',
        specialFilters: new Set(['running']),
      }).workspaces.map((w) => w.id),
    ).toEqual([]);
    // A member runs but the name does not match → hidden.
    expect(
      computeWorkspaceRowVisibility(workspaces, sessions, {
        ...baseOptions,
        searchQuery: 'zzz',
        specialFilters: new Set(['running']),
      }).workspaces.map((w) => w.id),
    ).toEqual([]);
  });

  it('aggregates status over actual members, not the keyword-filtered list', () => {
    // w-two is running but its NAME does not match the keyword; the
    // workspace name still matches, and the running member still counts.
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      searchQuery: 'beta ws',
      specialFilters: new Set(['running']),
    });
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws_beta']);
  });

  it('resolves members through the caller-provided inheritance map', () => {
    // The root is idle; the managed child actually runs and inherits ws_alpha.
    const tree = [
      makeSession('mgr', { workspaceIds: ['ws_alpha'], workerStatus: 'idle' }),
      makeSession('kid', { managedBy: 'mgr', workerStatus: 'running' }),
    ];
    const visibility = computeWorkspaceRowVisibility([alpha, beta], tree, {
      ...baseOptions,
      specialFilters: new Set(['running']),
      membersByWorkspace: new Map([['ws_alpha', tree]]),
      ungroupedMembers: [],
    });
    expect(visibility.workspaces.map((w) => w.id)).toEqual(['ws_alpha']);
  });

  it('does not match Session names with the workspace keyword', () => {
    // A Session-only keyword matches no workspace; virtual scopes stay available.
    const visibility = computeWorkspaceRowVisibility(workspaces, sessions, {
      ...baseOptions,
      searchQuery: 'loose',
    });
    expect(visibility.workspaces.map((w) => w.id)).toEqual([]);
    expect(visibility.showUngrouped).toBe(true);
    expect(visibility.showAll).toBe(true);
  });

  it('retains virtual scopes per-Session status AND rather than workspace aggregation', () => {
    const members = [
      makeSession('running', { workerStatus: 'running' }),
      makeSession('unread', { unreadDoneCount: 1 }),
    ];
    const visibility = computeWorkspaceRowVisibility([alpha], members, {
      ...baseOptions,
      searchQuery: 'alpha', specialFilters: new Set(['running', 'unread']),
      membersByWorkspace: new Map([['ws_alpha', members]]), ungroupedMembers: members,
    });
    expect(visibility.workspaces).toEqual([alpha]);
    expect(visibility.showAll).toBe(false);
    expect(visibility.showUngrouped).toBe(false);
  });

  it('handles an empty workspace list', () => {
    const visibility = computeWorkspaceRowVisibility([], sessions, {
      ...baseOptions,
      searchQuery: 'alpha',
      specialFilters: new Set(['running']),
    });
    expect(visibility.workspaces).toEqual([]);
    expect(visibility.showAll).toBe(true);
    expect(visibility.showUngrouped).toBe(false);
  });
});
