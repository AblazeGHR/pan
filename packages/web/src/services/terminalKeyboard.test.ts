// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { terminalKeyHandler } from './terminalKeyboard';

describe('terminal browser clipboard key', () => {
  it.each(['keydown', 'keypress', 'keyup'])('leaves Ctrl-V %s to the browser without cancelling it', (type) => {
    const event = new KeyboardEvent(type, { key: 'v', ctrlKey: true, cancelable: true });
    expect(terminalKeyHandler(event)).toBe(false);
    expect(event.defaultPrevented).toBe(false);
  });
  it('also permits Ctrl-Shift-V browser paste', () => {
    expect(terminalKeyHandler(new KeyboardEvent('keydown', { key: 'V', ctrlKey: true, shiftKey: true }))).toBe(false);
  });
  it.each([
    { key: 'c', ctrlKey: true }, { key: 'd', ctrlKey: true }, { key: 'ArrowLeft' },
    { key: 'Tab' }, { key: 'Enter' }, { key: 'v' }, { key: 'v', altKey: true },
    { key: 'v', ctrlKey: true, altKey: true }, { key: 'v', metaKey: true },
  ])('preserves ordinary xterm handling for %o', (init) => {
    expect(terminalKeyHandler(new KeyboardEvent('keydown', init))).toBe(true);
  });
});
