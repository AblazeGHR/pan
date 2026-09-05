import { describe, expect, it } from 'vitest';
import { matchesSessionSearch } from './sessionFilters';
import type { Session } from '@/types';

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

describe('matchesSessionSearch', () => {
  it.each([
    ['session name', 'release'],
    ['Pan Session ID', 'session-123'],
    ['CLI Session ID', 'session-abc'],
  ])('matches a partial query in the %s', (_field, query) => {
    expect(matchesSessionSearch(session(), query)).toBe(true);
  });

  it('matches case-insensitively and trims the query', () => {
    expect(matchesSessionSearch(session(), '  RELEASE PLANNING ')).toBe(true);
    expect(matchesSessionSearch(session(), 'CLI-SESSION-ABC')).toBe(true);
  });

  it('returns false when the query matches none of the searchable fields', () => {
    expect(matchesSessionSearch(session(), 'unrelated')).toBe(false);
  });

  it('does not fail or match the literal text for a missing CLI Session ID', () => {
    const missingCliId = session({ cliSessionId: null });
    expect(matchesSessionSearch(missingCliId, 'cli-session')).toBe(false);
    expect(matchesSessionSearch(missingCliId, 'null')).toBe(false);
    expect(matchesSessionSearch(missingCliId, 'pan-session')).toBe(true);

    expect(matchesSessionSearch(session({ cliSessionId: '' }), 'cli-session')).toBe(false);
  });

  it('keeps a session when a query matches any one of multiple fields', () => {
    const multiFieldSession = session({
      id: 'pan-unique-id',
      name: 'Unique name',
      cliSessionId: 'cli-unique-id',
    });

    expect(matchesSessionSearch(multiFieldSession, 'unique name')).toBe(true);
    expect(matchesSessionSearch(multiFieldSession, 'pan-unique')).toBe(true);
    expect(matchesSessionSearch(multiFieldSession, 'cli-unique')).toBe(true);
  });

  it('treats an empty query as matching every session', () => {
    expect(matchesSessionSearch(session({ cliSessionId: undefined }), '')).toBe(true);
    expect(matchesSessionSearch(session({ cliSessionId: undefined }), '   ')).toBe(true);
  });
});
