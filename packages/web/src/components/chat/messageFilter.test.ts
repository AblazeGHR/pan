import { describe, it, expect } from 'vitest';
import { filterVisibleMessages } from './messageFilter';
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
