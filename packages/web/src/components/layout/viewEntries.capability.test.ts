import { describe, expect, it } from 'vitest';
import { entries, filterAvailableViewEntries } from './viewEntries';

describe('view entry capabilities', () => {
  it('keeps every entry when the platform probe did not answer', () => {
    expect(filterAvailableViewEntries(entries, null)).toEqual(entries);
    expect(filterAvailableViewEntries(entries, undefined)).toEqual(entries);
  });

  it('keeps the Terminal entry when the server does not mention terminal.native', () => {
    const filtered = filterAvailableViewEntries(entries, { rewind: false });
    expect(filtered.some((entry) => entry.id === 'builtin:terminals')).toBe(true);
  });

  it('drops the Terminal entry when terminal.native is false', () => {
    const filtered = filterAvailableViewEntries(entries, { 'terminal.native': false });
    expect(filtered.some((entry) => entry.to === '/terminals')).toBe(false);
    // Everything else survives: a denied platform must not lose the shell.
    expect(filtered.map((entry) => entry.id)).toEqual(
      entries.filter((entry) => entry.id !== 'builtin:terminals').map((entry) => entry.id),
    );
  });

  it('keeps the Terminal entry when the server enables it', () => {
    const filtered = filterAvailableViewEntries(entries, { 'terminal.native': true });
    expect(filtered.some((entry) => entry.to === '/terminals')).toBe(true);
  });

  it('never filters entries without a capability binding', () => {
    const filtered = filterAvailableViewEntries(entries, {
      'terminal.native': false,
      rewind: false,
      'service.lifecycle': false,
      'terminal.detach': false,
    });
    expect(filtered.map((entry) => entry.to)).toEqual([
      '/', '/editor', '/locker', '/actions', '/automation',
    ]);
  });
});
