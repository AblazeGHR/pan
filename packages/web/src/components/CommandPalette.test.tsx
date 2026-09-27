// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { CommandPalette } from './CommandPalette';
import { useSessionStore } from '@/stores/sessionStore';

afterEach(() => cleanup());

describe('CommandPalette layout', () => {
  it('uses a readable component-local width and viewport gutters', () => {
    useSessionStore.setState({ sessions: [] });
    render(
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>,
    );

    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });

    const input = screen.getByPlaceholderText('Search commands, sessions...');
    const card = input.parentElement?.parentElement;
    expect(card?.className).toContain('max-w-[32rem]');
    expect(card?.parentElement?.className).toContain('px-4');
  });
});
