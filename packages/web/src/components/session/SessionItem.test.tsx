// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { SessionItem } from './SessionItem';
import type { Session } from '@/types';

function session(lastMessage: string, workerStatus: string = 'idle'): Session {
  return {
    id: 'session-1',
    name: 'Codex',
    adapter: 'codex',
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    historyTotal: 1,
    lastMessage,
    workerStatus,
  };
}

describe('SessionItem streaming preview', () => {
  afterEach(() => cleanup());

  it('shows the last message for a selected completed session', () => {
    render(
      <SessionItem session={session('## Answer\n\n**body**')} isActive />,
    );

    expect(screen.getByText('Answer body')).toBeTruthy();
  });

  it('keeps the selected running session preview visible', () => {
    render(
      <SessionItem
        session={session('## Answer\n\n**body**', 'running')}
        isActive
      />,
    );

    expect(screen.getByText('Answer body')).toBeTruthy();
  });

  it('keeps the preview visible for a background Codex session while it runs', () => {
    render(
      <SessionItem
        session={session('## Answer\n\n**body**', 'running')}
        isActive={false}
      />,
    );

    expect(screen.getByText('Answer body')).toBeTruthy();
  });
});
