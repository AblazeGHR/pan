// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  bindingFromEvent,
  findBindingConflicts,
  formatBinding,
  isReservedBinding,
  normalizeBinding,
  resolveBinding,
} from './bindings';

function keydown(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent('keydown', init);
}

describe('shortcut binding canonicalization', () => {
  it('normalizes modifier order and single-letter case', () => {
    expect(normalizeBinding('shift+mod+k')).toBe('Mod+Shift+K');
    expect(normalizeBinding('Ctrl+Alt+1')).toBe('Ctrl+Alt+1');
    expect(normalizeBinding('Mod+K+J')).toBeNull();
    expect(normalizeBinding('')).toBeNull();
    expect(normalizeBinding(42)).toBeNull();
  });

  it('uses Ctrl as Mod on Windows/Linux and Meta on macOS', () => {
    expect(bindingFromEvent(keydown({ key: 'k', code: 'KeyK', ctrlKey: true }), 'other'))
      .toBe('Mod+K');
    expect(bindingFromEvent(keydown({ key: 'k', code: 'KeyK', metaKey: true }), 'mac'))
      .toBe('Mod+K');
    expect(bindingFromEvent(keydown({ key: 'k', code: 'KeyK', ctrlKey: true }), 'mac'))
      .toBe('Ctrl+K');
    expect(bindingFromEvent(keydown({ key: 'k', code: 'KeyK', metaKey: true }), 'other'))
      .toBe('Meta+K');
  });

  it('records shifted digits by physical key and ignores bare keys/modifiers', () => {
    expect(bindingFromEvent(keydown({
      key: '!', code: 'Digit1', ctrlKey: true, shiftKey: true,
    }), 'other')).toBe('Mod+Shift+1');
    expect(bindingFromEvent(keydown({ key: 'k', code: 'KeyK' }), 'other')).toBeNull();
    expect(bindingFromEvent(keydown({ key: 'Control', code: 'ControlLeft', ctrlKey: true }), 'other'))
      .toBeNull();
    expect(bindingFromEvent(keydown({
      key: 'k', code: 'KeyK', ctrlKey: true, altKey: true,
    }), 'other')).toBe('Mod+Alt+K');
  });

  it('formats per platform and flags reserved combinations', () => {
    expect(formatBinding('Mod+Shift+K', 'other')).toBe('Ctrl+Shift+K');
    expect(formatBinding('Mod+Shift+K', 'mac')).toBe('Cmd+Shift+K');
    expect(formatBinding(null, 'mac')).toBe('Not set');
    expect(isReservedBinding('Mod+R')).toBe(true);
    expect(isReservedBinding('Mod+S')).toBe(true);
    expect(isReservedBinding('Mod+B')).toBe(false);
  });
});

describe('binding resolution and conflicts', () => {
  const actions = [
    { id: 'a', defaultBinding: 'Mod+B' },
    { id: 'b', defaultBinding: null },
    { id: 'panel:plugin:alpha/one', defaultBinding: null },
  ];

  it('distinguishes a missing override from an explicit clear', () => {
    expect(resolveBinding('a', 'Mod+B', {})).toBe('Mod+B');
    expect(resolveBinding('a', 'Mod+B', { a: null })).toBeNull();
    expect(resolveBinding('b', null, { b: 'Mod+Shift+P' })).toBe('Mod+Shift+P');
  });

  it('detects conflicts across dynamic panel actions', () => {
    const conflicts = findBindingConflicts(actions, {
      b: 'Mod+B',
      'panel:plugin:alpha/one': 'Mod+B',
    });
    expect(conflicts.get('Mod+B')).toEqual(['a', 'b', 'panel:plugin:alpha/one']);
  });

  it('does not report a binding shared with an explicitly cleared action', () => {
    expect(findBindingConflicts(actions, { b: null, 'panel:plugin:alpha/one': null }).size)
      .toBe(0);
  });

  it('detects explicit Ctrl versus portable Mod collisions and captures physical layout/case', () => {
    expect(findBindingConflicts(actions, { a: 'Ctrl+B', b: 'Mod+B' }).get('Mod+B')).toEqual(['a', 'b']);
    expect(bindingFromEvent(keydown({ key: 'ф', code: 'KeyA', ctrlKey: true }))).toBe('Mod+A');
    expect(bindingFromEvent(keydown({ key: 'F8', code: 'F8' }))).toBe('F8');
    expect(bindingFromEvent(keydown({ key: 'Enter', code: 'Enter' }))).toBe('Enter');
    expect(isReservedBinding('Enter', 'composer.send')).toBe(false);
    expect(isReservedBinding('Enter', 'queue.lockAll')).toBe(true);
  });
});
