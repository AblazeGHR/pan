/**
 * Canonical keyboard-binding helpers.
 *
 * A binding is stored as a platform-neutral string such as ``Mod+Shift+K``.
 * ``Mod`` is Ctrl on Windows/Linux and Cmd (Meta) on macOS, so a preference
 * saved on one platform keeps working on the other.  Explicit ``Ctrl`` and
 * ``Meta`` tokens are also accepted for users who need a non-primary modifier.
 */

export type ShortcutPlatform = 'mac' | 'other';

const MODIFIER_ORDER = ['Mod', 'Ctrl', 'Meta', 'Alt', 'Shift'] as const;
type ModifierToken = (typeof MODIFIER_ORDER)[number];

const MODIFIER_TOKENS = new Set<string>(MODIFIER_ORDER);
const MODIFIER_ALIASES: Record<string, ModifierToken> = {
  mod: 'Mod',
  ctrl: 'Ctrl',
  control: 'Ctrl',
  meta: 'Meta',
  cmd: 'Meta',
  command: 'Meta',
  alt: 'Alt',
  option: 'Alt',
  shift: 'Shift',
};
const MODIFIER_KEYS = new Set([
  'Alt',
  'AltGraph',
  'CapsLock',
  'Control',
  'Fn',
  'FnLock',
  'Hyper',
  'Meta',
  'NumLock',
  'OS',
  'ScrollLock',
  'Shift',
  'Super',
  'Symbol',
  'SymbolLock',
]);

const CODE_KEYS: Record<string, string> = {
  ArrowDown: 'ArrowDown',
  ArrowLeft: 'ArrowLeft',
  ArrowRight: 'ArrowRight',
  ArrowUp: 'ArrowUp',
  Backspace: 'Backspace',
  Backslash: '\\',
  BracketLeft: '[',
  BracketRight: ']',
  Comma: ',',
  Delete: 'Delete',
  End: 'End',
  Enter: 'Enter',
  Equal: '=',
  Escape: 'Escape',
  Home: 'Home',
  Insert: 'Insert',
  Minus: '-',
  PageDown: 'PageDown',
  PageUp: 'PageUp',
  Period: '.',
  Quote: "'",
  Semicolon: ';',
  Slash: '/',
  Space: 'Space',
  Tab: 'Tab',
};

const PUNCTUATION_KEYS = new Set<string>([
  '\\', '[', ']', ',', '.', '=', '-', "'", '`', ';', '/',
]);

const NAMED_KEYS = new Set<string>([
  ...Object.values(CODE_KEYS),
  'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12',
  'F13', 'F14', 'F15', 'F16', 'F17', 'F18', 'F19', 'F20', 'F21', 'F22', 'F23', 'F24',
]);

/** Browser, OS and editor/terminal combinations a global action may not take. */
export const RESERVED_BINDINGS: ReadonlySet<string> = new Set([
  // Browser / window lifecycle.  These must keep working even when Pan has a
  // custom action that could otherwise match.
  'F5',
  'Mod+R',
  'Mod+Shift+R',
  'Mod+W',
  'Mod+Shift+W',
  'Mod+T',
  'Mod+Shift+T',
  'Mod+N',
  'Mod+Shift+N',
  'Mod+L',
  'Mod+O',
  'Mod+P',
  'Mod+Shift+P',
  'Mod+Shift+S',
  'Mod+Shift+Delete',
  'Mod+Shift+I',
  'Mod+Shift+J',
  'Mod+Shift+C',
  'Mod+Shift+K',
  'Mod+U',
  'Mod+J',
  'Mod+H',
  'Mod+Q',
  'Mod+Tab',
  'Mod+Shift+Tab',
  'Alt+F4',
  // Editing and terminal-native combinations.  The global dispatcher ignores
  // editable targets, but these still belong to the focused editor/terminal.
  'Mod+A',
  'Mod+C',
  'Mod+V',
  'Mod+X',
  'Mod+Z',
  'Mod+Shift+Z',
  'Mod+Y',
  'Mod+S',
  // Navigation keys are not useful as standalone global shortcuts.
  'Escape',
  'Tab',
  'Enter',
  'Backspace',
  'Delete',
]);

export function detectShortcutPlatform(
  navigatorLike: Pick<Navigator, 'platform' | 'userAgent'> | undefined = typeof navigator === 'undefined'
    ? undefined
    : navigator,
): ShortcutPlatform {
  const source = `${navigatorLike?.platform ?? ''} ${navigatorLike?.userAgent ?? ''}`;
  return /Mac|iPhone|iPad|iPod/i.test(source) ? 'mac' : 'other';
}

function modifierToken(token: string): ModifierToken | null {
  if (MODIFIER_TOKENS.has(token)) return token as ModifierToken;
  return MODIFIER_ALIASES[token.toLowerCase()] ?? null;
}

function keyToken(raw: string): string | null {
  if (raw.length === 1) {
    if (/[a-z]/.test(raw)) return raw.toUpperCase();
    if (/[A-Z0-9]/.test(raw)) return raw;
    return PUNCTUATION_KEYS.has(raw) ? raw : null;
  }
  if (MODIFIER_KEYS.has(raw) || raw === 'Dead' || raw === 'Process' || raw === 'Unidentified') {
    return null;
  }
  return NAMED_KEYS.has(raw) ? raw : null;
}

function canonicalize(tokens: string[]): string | null {
  const modifiers = new Set<ModifierToken>();
  let key: string | null = null;
  for (const token of tokens) {
    if (!token) continue;
    const modifier = modifierToken(token);
    if (modifier) {
      modifiers.add(modifier);
      continue;
    }
    if (key !== null) return null;
    key = keyToken(token);
    if (!key) return null;
  }
  if (!key) return null;
  const ordered = MODIFIER_ORDER.filter((modifier) => modifiers.has(modifier));
  return [...ordered, key].join('+');
}

/** Parse and canonicalize a stored binding; returns null for invalid input. */
export function normalizeBinding(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  return canonicalize(trimmed.split('+'));
}

/** Physical key token for a keydown, independent of the active layout where possible. */
export function keyTokenFromEvent(event: KeyboardEvent): string | null {
  const code = event.code || '';
  const letter = /^Key([A-Z])$/.exec(code);
  if (letter) return letter[1]!;
  const digit = /^Digit([0-9])$/.exec(code);
  if (digit) return digit[1]!;
  if (/^F(?:[1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
  const mapped = CODE_KEYS[code];
  if (mapped) return mapped;
  if (MODIFIER_KEYS.has(event.key)) return null;
  if (!event.key || event.key === 'Dead' || event.key === 'Process' || event.key === 'Unidentified') {
    return null;
  }
  if (event.key.length === 1) {
    // Punctuation that has no stable `code` mapping above (for example keypad
    // variants) still gets a deterministic single-character token.
    return event.key.toUpperCase();
  }
  return event.key;
}

/**
 * Build the canonical binding for a real keydown.  Returns null when the event
 * is not a shortcut-shaped combination (modifier-only, bare typing, AltGraph).
 */
export function bindingFromEvent(
  event: KeyboardEvent,
  platform: ShortcutPlatform = detectShortcutPlatform(),
): string | null {
  if (event.getModifierState?.('AltGraph')) return null;
  const key = keyTokenFromEvent(event);
  if (!key) return null;
  const tokens: string[] = [];
  if (platform === 'mac') {
    if (event.metaKey) tokens.push('Mod');
    if (event.ctrlKey) tokens.push('Ctrl');
  } else {
    if (event.ctrlKey) tokens.push('Mod');
    if (event.metaKey) tokens.push('Meta');
  }
  if (event.altKey) tokens.push('Alt');
  if (event.shiftKey) tokens.push('Shift');
  if (tokens.length === 0) return null;
  return canonicalize([...tokens, key]);
}

function keyLabel(key: string): string {
  if (key === 'Escape') return 'Esc';
  if (key === 'ArrowUp') return '↑';
  if (key === 'ArrowDown') return '↓';
  if (key === 'ArrowLeft') return '←';
  if (key === 'ArrowRight') return '→';
  return key;
}

/** Human-readable binding for the current platform (Ctrl on Win/Linux, Cmd on macOS). */
export function formatBinding(
  binding: string | null,
  platform: ShortcutPlatform = detectShortcutPlatform(),
): string {
  if (!binding) return 'Not set';
  const tokens = binding.split('+');
  const key = tokens.pop() ?? '';
  const labels = tokens.map((token) => {
    if (token === 'Mod') return platform === 'mac' ? 'Cmd' : 'Ctrl';
    if (token === 'Ctrl') return 'Ctrl';
    if (token === 'Meta') return platform === 'mac' ? 'Cmd' : 'Meta';
    return token;
  });
  return [...labels, keyLabel(key)].filter(Boolean).join('+');
}

export function isReservedBinding(binding: string | null): boolean {
  return !!binding && RESERVED_BINDINGS.has(binding);
}

/** Resolve the effective binding for one action. `null` means explicitly unbound. */
export function resolveBinding(
  actionId: string,
  defaultBinding: string | null,
  bindings: Readonly<Record<string, string | null>>,
): string | null {
  return Object.prototype.hasOwnProperty.call(bindings, actionId)
    ? bindings[actionId] ?? null
    : defaultBinding;
}

/** All combinations shared by more than one action. */
export function findBindingConflicts(
  actions: readonly { id: string; defaultBinding: string | null }[],
  bindings: Readonly<Record<string, string | null>>,
): Map<string, string[]> {
  const owners = new Map<string, string[]>();
  for (const action of actions) {
    const binding = resolveBinding(action.id, action.defaultBinding, bindings);
    if (!binding) continue;
    const existing = owners.get(binding);
    if (existing) existing.push(action.id);
    else owners.set(binding, [action.id]);
  }
  const conflicts = new Map<string, string[]>();
  for (const [binding, actionIds] of owners) {
    if (actionIds.length > 1) conflicts.set(binding, actionIds);
  }
  return conflicts;
}
