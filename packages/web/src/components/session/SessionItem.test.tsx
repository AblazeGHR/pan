// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SessionItem } from './SessionItem';
import type { Session } from '@/types';

function session(lastMessage: string): Session {
  return {
    id: 'session-1',
    name: 'Codex',
    adapter: 'codex',
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    historyTotal: 1,
    lastMessage,
  };
}

describe('SessionItem streaming preview', () => {
  it('hides the plain-text preview for the selected session but keeps it for background sessions', () => {
    const { rerender } = render(
      <SessionItem session={session('## Answer\n\n**body**')} isActive />,
    );

    expect(screen.queryByText('Answer body')).toBeNull();

    rerender(
      <SessionItem session={session('## Answer\n\n**body**')} isActive={false} />,
    );
    expect(screen.getByText('Answer body')).toBeTruthy();
  });
});
