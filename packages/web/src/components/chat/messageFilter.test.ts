import { describe, it, expect } from 'vitest';
import {
  filterVisibleMessages,
  getQuickJumpIndexItems,
  getQuickJumpKind,
  getQuickJumpMessages,
  getQuickJumpPreview,
} from './messageFilter';
import type { MessageVisibilitySettings } from './messageFilter';
import type { Message } from '@/types';

const mk = (content: string, role = 'assistant'): Message => ({ role, content });

const ALL_SHOWN: MessageVisibilitySettings = {
  showMetaAgent: true,
  showTaskAgent: true,
  showQQ: true,
};

describe('filterVisibleMessages', () => {
  it('keeps every message when all toggles are on', () => {
    const msgs = [
      mk('////by agent : ses_1 | T'),
      mk('@@@@by agent : ses_2 | R'),
      mk('@@@@by qq : user:1 | Nick'),
      mk('plain hello'),
    ];
    expect(filterVisibleMessages(msgs, ALL_SHOWN)).toHaveLength(4);
  });

  it('drops meta-agent messages when showMetaAgent is off', () => {
    const msgs = [mk('////by agent : ses_1 | T'), mk('plain hello')];
    const out = filterVisibleMessages(msgs, { ...ALL_SHOWN, showMetaAgent: false });
    expect(out.map((m) => m.content)).toEqual(['plain hello']);
  });

  it('drops task-agent reports when showTaskAgent is off', () => {
    const msgs = [mk('@@@@by agent : ses_2 | R'), mk('plain hello')];
    const out = filterVisibleMessages(msgs, { ...ALL_SHOWN, showTaskAgent: false });
    expect(out.map((m) => m.content)).toEqual(['plain hello']);
  });

  it('drops QQ messages when showQQ is off', () => {
    const msgs = [mk('@@@@by qq : user:1 | Nick'), mk('plain hello')];
    const out = filterVisibleMessages(msgs, { ...ALL_SHOWN, showQQ: false });
    expect(out.map((m) => m.content)).toEqual(['plain hello']);
  });

  it('matches prefixes after trimming leading whitespace, across any role', () => {
    const msgs = [
      mk('  ////by agent : ses_1 | T'),
      mk('\n\t@@@@by agent : ses_2 | R', 'tool'),
      mk('   @@@@by qq : user:1 | Nick'),
    ];
    const out = filterVisibleMessages(msgs, {
      showMetaAgent: false,
      showTaskAgent: false,
      showQQ: false,
    });
    expect(out).toHaveLength(0);
  });

  it('does not filter messages that merely contain a prefix mid-content', () => {
    const msgs = [mk('note: ////by agent is a marker'), mk('foo @@@@by qq bar')];
    const out = filterVisibleMessages(msgs, {
      showMetaAgent: false,
      showTaskAgent: false,
      showQQ: false,
    });
    expect(out).toHaveLength(2);
  });

  it('never mutates the input array', () => {
    const msgs = [mk('////by agent : ses_1 | T'), mk('plain hello')];
    const snapshot = [...msgs];
    filterVisibleMessages(msgs, { ...ALL_SHOWN, showMetaAgent: false });
    expect(msgs).toEqual(snapshot);
  });
});


describe('quick-location classification', () => {
  it('classifies user messages and task-agent reports separately', () => {
    expect(getQuickJumpKind(mk('plain user', 'user'))).toBe('user');
    expect(getQuickJumpKind(mk('@@@@by agent : ses_2 | Worker\nresult', 'user'))).toBe('worker');
    expect(getQuickJumpKind(mk('@@@@by agent : ses_2 | Worker\nresult', 'assistant'))).toBe('worker');
    expect(getQuickJumpKind(mk('assistant reply', 'assistant'))).toBeNull();
  });

  it('removes report headers and truncates hover previews', () => {
    const preview = getQuickJumpPreview(
      '@@@@by agent : ses_2 | Worker\n' + 'a '.repeat(100),
    );
    expect(preview.startsWith('a a a')).toBe(true);
    expect(preview).toHaveLength(120);
    expect(preview).not.toContain('@@@@by agent');
  });

  it('builds compact full-history targets keyed by stable fromEnd offsets', () => {
    const targets = getQuickJumpIndexItems(
      [
        mk('old user', 'user'),
        mk('assistant reply', 'assistant'),
        mk('@@@@by agent : ses_2 | Worker\nold report', 'assistant'),
      ],
      203,
      10,
      ALL_SHOWN,
    );
    expect(targets).toEqual([
      { fromEnd: 192, kind: 'user', preview: 'old user' },
      { fromEnd: 190, kind: 'worker', preview: 'old report' },
    ]);
  });

  it('applies source visibility before compacting a history page', () => {
    const targets = getQuickJumpIndexItems(
      [
        mk('////by agent : ses_1 | Meta\ninstruction', 'user'),
        mk('@@@@by agent : ses_2 | Worker\nreport', 'assistant'),
        mk('@@@@by qq : user:1 | Nick\nhello', 'user'),
        mk('plain user', 'user'),
      ],
      4,
      0,
      { showMetaAgent: false, showTaskAgent: false, showQQ: false },
    );
    expect(targets).toEqual([{ fromEnd: 0, kind: 'user', preview: 'plain user' }]);
  });

  it('honors visibility switches when building navigation targets', () => {
    const targets = getQuickJumpMessages(
      [
        mk('user message', 'user'),
        mk('@@@@by agent : ses_2 | Worker\nreport', 'assistant'),
        mk('////by agent : ses_3 | Meta\ninstruction', 'user'),
      ],
      { ...ALL_SHOWN, showTaskAgent: false },
    );
    expect(targets.map((target) => target.kind)).toEqual(['user', 'user']);
    expect(targets.map((target) => target.preview)).toEqual(['user message', 'instruction']);
  });
});

