// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SessionMenu } from './SessionMenu';
import type { Session } from '@/types';

const session: Session = {
  id: 'ses-menu-test',
  name: 'Menu test',
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
};

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('SessionMenu details entry', () => {
  it('offers Details and closes the menu before opening the modal', () => {
    const onClose = vi.fn();
    const onDetails = vi.fn();
    render(<SessionMenu session={session} position={{ x: 10, y: 10 }} onClose={onClose} onDetails={onDetails} />);

    fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onDetails).toHaveBeenCalledWith(session.id);
  });
});
