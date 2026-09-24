// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { WorkspaceManagerChangeConfirmationModal } from './WorkspaceManagerChangeConfirmationModal';

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('WorkspaceManagerChangeConfirmationModal', () => {
  const base = {
    sessionName: 'Research subtree',
    subtreeCount: 3,
    managerName: 'Destination manager',
    targetWorkspaceName: 'Review',
    resolve: vi.fn(),
  };

  it('explains that detach creates a new management root', () => {
    render(<WorkspaceManagerChangeConfirmationModal
      request={{ ...base, changeType: 'detach' }} onClose={vi.fn()} onConfirm={vi.fn()}
    />);

    expect(screen.getByRole('dialog').textContent).toContain('detach it from Destination manager');
    expect(screen.getByRole('dialog').textContent).toContain('new management root');
    expect(screen.getByRole('button', { name: 'Move and detach' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Reparent subtree' })).toBeNull();
  });

  it('explains that reparent inherits the destination manager tree workspace', () => {
    render(<WorkspaceManagerChangeConfirmationModal
      request={{ ...base, changeType: 'attach' }} onClose={vi.fn()} onConfirm={vi.fn()}
    />);

    expect(screen.getByRole('dialog').textContent).toContain('under Destination manager');
    expect(screen.getByRole('dialog').textContent).toContain('inherit the manager tree\'s workspace: Review');
    expect(screen.getByRole('button', { name: 'Reparent subtree' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Move and detach' })).toBeNull();
  });

  it('passes confirm and cancel decisions to their handlers', () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(<WorkspaceManagerChangeConfirmationModal
      request={{ ...base, changeType: 'attach' }} onClose={onClose} onConfirm={onConfirm}
    />);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reparent subtree' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});
