import { describe, expect, it } from 'vitest';
import {
  ALL_WORKSPACES,
  UNGROUPED_WORKSPACES,
  getSessionListCandidates,
  scopeSessionsByWorkspace,
} from './sessionFilters';
import type { Session } from '@/types';

/** Minimal Session for scope tests (only the fields the filters read). */
function makeSession(id: string, workspaceIds?: string[]): Session {
  return {
    id,
    name: id,
    alwaysThinkingEnabled: false,
    effort: 'medium',
    history: [],
    workspaceIds,
  } as unknown as Session;
}

const sessions = [
  makeSession('alpha', ['ws_1']),
  makeSession('beta', ['ws_2']),
  makeSession('gamma'),          // ungrouped (field omitted, like old sessions)
  makeSession('delta', []),      // ungrouped (explicit empty list)
];

describe('scopeSessionsByWorkspace', () => {
  it("keeps every session for 'all' / missing scope", () => {
    expect(scopeSessionsByWorkspace(sessions, ALL_WORKSPACES).map((s) => s.id)).toEqual([
      'alpha', 'beta', 'gamma', 'delta',
    ]);
    expect(scopeSessionsByWorkspace(sessions).map((s) => s.id)).toEqual([
      'alpha', 'beta', 'gamma', 'delta',
    ]);
  });

  it('keeps only the members of the active workspace', () => {
    expect(scopeSessionsByWorkspace(sessions, 'ws_1').map((s) => s.id)).toEqual(['alpha']);
    expect(scopeSessionsByWorkspace(sessions, 'ws_2').map((s) => s.id)).toEqual(['beta']);
  });

  it('keeps sessions without any membership for the ungrouped scope', () => {
    expect(scopeSessionsByWorkspace(sessions, UNGROUPED_WORKSPACES).map((s) => s.id)).toEqual([
      'gamma', 'delta',
    ]);
  });
});

describe('getSessionListCandidates workspace scoping', () => {
  const base = {
    hiddenSessionIds: new Set<string>(),
    specialFilters: new Set<never>(),
  };

  it('scopes the select-all / selection candidates to the active workspace', () => {
    const candidates = getSessionListCandidates(sessions, {
      ...base,
      multiSelectMode: true,
      searchQuery: '',
      activeWorkspaceId: 'ws_1',
    });
    // Select-all must not reach sessions of other workspaces.
    expect(candidates.map((s) => s.id)).toEqual(['alpha']);
  });

  it('applies the search INSIDE the workspace scope only', () => {
    const searchInWs1 = getSessionListCandidates(sessions, {
      ...base,
      multiSelectMode: false,
      searchQuery: 'beta',           // matches a session of ws_2 only
      activeWorkspaceId: 'ws_1',
    });
    expect(searchInWs1).toEqual([]);

    const searchInAll = getSessionListCandidates(sessions, {
      ...base,
      multiSelectMode: false,
      searchQuery: 'beta',
      activeWorkspaceId: ALL_WORKSPACES,
    });
    expect(searchInAll.map((s) => s.id)).toEqual(['beta']);
  });

  it('keeps the historical behaviour when no scope is provided', () => {
    const candidates = getSessionListCandidates(sessions, {
      ...base,
      multiSelectMode: true,
      searchQuery: '',
    });
    expect(candidates.map((s) => s.id)).toEqual(['alpha', 'beta', 'gamma', 'delta']);
  });
});
