// @vitest-environment node
//
// Source-level layering guard for the two chat views.
//
// jsdom has no cascade, so the component tests can only prove which classes are
// mounted. These assertions read index.css directly and pin the *layering*:
// the TUI view owns the base `width: 100%` role-bar rows (default), and every
// bubble / row-alignment rule stays behind `.bubble-mode`. Without this, a rule
// moved back onto a base selector silently re-styles the default TUI view.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (relativePath: string) =>
  readFileSync(resolve(process.cwd(), relativePath), 'utf8');

const escapeSelector = (selector: string) =>
  selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Comments are stripped so a selector mentioned in prose cannot match, and
// whitespace is collapsed so multi-line selector lists compare consistently.
const css = read('src/index.css')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/\s+/g, ' ');

/**
 * Index of a rule body, anchored on a real rule boundary. A plain
 * `indexOf('.msg.user {')` would also hit the tail of
 * `.bubble-mode .msg.user {`, which is exactly the bug this file guards.
 */
function ruleIndex(selectorList: string): number {
  const target = escapeSelector(selectorList.replace(/\s+/g, ' ').trim());
  const match = css.match(new RegExp(`(?:^|[{};])\\s*${target}\\s*\\{`));
  if (!match || match.index === undefined) {
    throw new Error(`CSS rule not found: ${selectorList}`);
  }
  return match.index;
}

function declarations(selectorList: string): string {
  const target = escapeSelector(selectorList.replace(/\s+/g, ' ').trim());
  const match = css.match(new RegExp(`(?:^|[{};])\\s*${target}\\s*\\{([^}]*)\\}`));
  if (!match) throw new Error(`CSS rule not found: ${selectorList}`);
  return match[1]!.trim();
}

describe('TUI view keeps the original full-width role-bar rows', () => {
  it('restores the assistant bar, the user box, separators and the ">" prompt', () => {
    expect(declarations('.msg.assistant')).toBe(
      'border-left: 3px solid var(--color-text-secondary); padding-left: 10px;',
    );

    const user = declarations('.msg.user');
    expect(user).toContain('border-left: 3px solid var(--color-success);');
    expect(user).toContain('border-top: 1px solid var(--color-success);');
    expect(user).toContain('border-bottom: 1px solid var(--color-success);');
    expect(user).toContain('padding: 8px 16px 8px 28px;');
    expect(user).toContain('width: 100%;');
    expect(user).toContain('margin: 6px 0;');
    expect(user).toContain('position: relative;');
    // The default view must not shrink to a bubble.
    expect(user).not.toContain('fit-content');
    expect(user).not.toContain('border-radius');

    expect(declarations('.msg.user::before')).toContain("content: '>';");
  });

  it('keeps the row wrapper shared but leaves row alignment to the Bubble view', () => {
    expect(declarations('.message-row')).toBe(
      'display: flex; flex-direction: column; min-width: 0;',
    );
    // Unscoped alignment rules would left/right-split the TUI view too.
    expect(() => declarations('.message-row-user')).toThrow();
    expect(() => declarations('.message-row-assistant')).toThrow();
  });

  it('does not leak the bubble content containment onto base markdown', () => {
    expect(() => declarations('.msg.user .prose-kimi')).toThrow();
    expect(() => declarations('.msg.assistant .prose-kimi')).toThrow();
  });
});

describe('Bubble view owns the shrink-to-fit bubble styles', () => {
  it('scopes the bubble geometry, alignment, narrow widths and prompt removal', () => {
    const shared = declarations('.bubble-mode .msg.user, .bubble-mode .msg.assistant');
    expect(shared).toContain('width: fit-content;');
    expect(shared).toContain('max-width: 100%;');
    expect(shared).toContain('margin: 0;');
    expect(shared).toContain('padding: 0.65rem 0.9rem;');
    expect(shared).toContain('border: 1px solid;');
    expect(shared).toContain('border-radius: 1rem;');
    expect(shared).toContain('overflow-wrap: anywhere;');

    expect(declarations('.bubble-mode .msg.user')).toContain('max-width: 75%;');
    expect(declarations('.bubble-mode .msg.assistant')).toContain('max-width: 85%;');

    // Bubbles must not paint the TUI ">" prompt.
    expect(declarations('.bubble-mode .msg.user::before')).toBe('content: none;');

    expect(declarations('.bubble-mode .message-row-user')).toBe('align-items: flex-end;');
    expect(declarations('.bubble-mode .message-row-assistant')).toBe('align-items: flex-start;');

    // Narrow-screen widths stay scoped to the Bubble view.
    expect(declarations('.bubble-mode .message-row-user .msg.user')).toBe('max-width: 94%;');
    expect(declarations('.bubble-mode .message-row-assistant .msg.assistant')).toBe(
      'max-width: 96%;',
    );
  });

  it('stops the shared bubble base from forcing full width on user/assistant', () => {
    const base = declarations('.bubble-mode .msg');
    expect(base).not.toContain('width: 100%');
    expect(base).not.toContain('max-width: 100%');
    expect(base).toContain('border-radius: 0;');

    // Legacy rows keep their own explicit full width.
    expect(declarations('.bubble-mode .msg.system')).toContain('width: 100%;');
    expect(declarations('.bubble-mode .msg.thinking')).toContain('width: 100% !important;');
    expect(declarations('.bubble-mode .msg.tool')).toContain('width: 100% !important;');
  });
});

describe('worker-report treatment is shared by both views', () => {
  it('keeps the label unscoped and the green border after every other width/edge rule', () => {
    const label = declarations('.worker-report-label');
    expect(label).toContain('align-self: flex-start;');
    expect(label).toContain('background: color-mix(in srgb, var(--color-success) 13%');
    expect(label).toContain('color: var(--color-success);');
    expect(() => declarations('.bubble-mode .worker-report-label')).toThrow();

    const borderSelector =
      '.message-row-worker-report .msg.user, .message-row-worker-report .msg.assistant';
    expect(declarations(borderSelector)).toBe(
      'border-left-width: 3px; border-left-color: var(--color-success);',
    );

    // Equal specificity is resolved by source order, so the green edge must
    // come after both the TUI base rule and the Bubble-scoped bubble rule.
    const borderIndex = ruleIndex(borderSelector);
    expect(borderIndex).toBeGreaterThan(ruleIndex('.msg.user'));
    expect(borderIndex).toBeGreaterThan(ruleIndex('.msg.assistant'));
    expect(borderIndex).toBeGreaterThan(ruleIndex('.bubble-mode .msg.user'));
    expect(borderIndex).toBeGreaterThan(ruleIndex('.bubble-mode .msg.assistant'));
  });
});

describe('the view toggle and its naming', () => {
  it('exposes the TUI/Bubble toggle instead of hiding it', () => {
    const topBar = read('src/components/layout/TopBar.tsx');
    expect(topBar).toMatch(/<button\s+onClick=\{toggleTuiView\}/);
    expect(topBar).toContain("title={tuiViewEnabled ? 'Switch to Bubble view' : 'Switch to TUI view'}");
    expect(topBar).not.toContain('Deprecated Bubble view');
  });

  it('describes the two presentations consistently and defaults to TUI', () => {
    const uiStore = read('src/stores/uiStore.ts');
    expect(uiStore).toContain('tuiViewEnabled: true,');
    expect(uiStore).toContain('Two independent chat presentations');
    expect(uiStore).not.toContain('old names were reversed');

    const chatMessages = read('src/components/chat/ChatMessages.tsx');
    expect(chatMessages).toContain("!tuiViewEnabled ? 'bubble-mode' : ''");
    expect(chatMessages).not.toContain('deprecated Bubble branch');
  });
});
