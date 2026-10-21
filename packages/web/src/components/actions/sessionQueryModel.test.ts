import { expect, it } from 'vitest';
import { validateSessionQuery } from './sessionQueryModel';
it.each([
  { field: 'id', op: 'in', value: ['a', 'b'] }, { field: 'name', op: 'contains', value: 'Review' },
  { field: 'adapter', op: 'ne', value: 'cbc' }, { field: 'managedBy', op: 'in', value: [null, 'ses_a'] },
  { field: 'readonlySession', op: 'eq', value: false }, { field: 'pinned', op: 'in', value: [true, false] },
  { field: 'workspaceIds', op: 'contains', value: 'ws_a' }, { field: 'workspaceIds', op: 'eq', value: ['ws_b', 'ws_a'] },
  { and: [{ field: 'name', op: 'eq', value: 'One' }, { or: [{ not: { field: 'pinned', op: 'eq', value: true } }] }] },
  { field: 'name', op: 'eq', value: '😀'.repeat(512) },
])('accepts contract predicate %j', (value) => expect(validateSessionQuery(value)).toBe(''));
it.each([
  {}, { and: [] }, { or: [] }, { field: 'name', op: 'contains', value: '' },
  { field: 'pinned', op: 'eq', value: 1 }, { field: 'readonlySession', op: 'eq', value: 'false' },
  { field: 'name', op: 'eq', value: null }, { field: 'unknown', op: 'eq', value: '' },
  { field: 'adapter', op: 'contains', value: 'c' }, { field: 'workspaceIds', op: 'in', value: ['a'] },
  { field: 'workspaceIds', op: 'eq', value: 'a' }, { field: 'id', op: 'in', value: [] },
  { field: 'managedBy', op: 'in', value: [true] }, { field: 'name', op: 'regex', value: '.*' },
  { field: 'id', op: 'in', value: Array(129).fill('a') }, { field: 'name', op: 'eq', value: '😀'.repeat(513) },
  { and: Array(64).fill({ field: 'pinned', op: 'eq', value: false }) },
])('rejects invalid predicate %j', (value) => expect(validateSessionQuery(value)).not.toBe(''));
it('bounds nested NOT with root counted at depth one', () => {
  let tree: unknown = { field: 'name', op: 'eq', value: 'a' };
  for (let i = 0; i < 7; i++) tree = { not: tree };
  expect(validateSessionQuery(tree)).toBe('');
  expect(validateSessionQuery({ not: tree })).toContain('8/64');
});
