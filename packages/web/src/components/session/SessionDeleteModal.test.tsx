// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SessionDeleteModal } from './SessionDeleteModal';
import type { Session } from '@/types';

const sessions: Session[] = [{
  id: 'parent', name: 'Parent', history: [], alwaysThinkingEnabled: false, effort: '', managed: ['child'],
}];

afterEach(cleanup);

describe('SessionDeleteModal', () => {
  it('keeps the delete confirmation on the safe md modal width', () => {
    const onConfirm = vi.fn();
    render(<SessionDeleteModal sessions={sessions} specialIds={['parent']} normalIds={['plain']} descendantCount={2} onClose={vi.fn()} onConfirm={onConfirm} onCancelSpecial={vi.fn()} />);
    const overlay = document.body.querySelector<HTMLElement>('.modal-overlay');
    const card = document.body.querySelector<HTMLElement>('.modal-card');
    expect(overlay?.className).toContain('p-4');
    expect(card?.className).toContain('w-full');
    expect(card?.className).toContain('max-w-[32rem]');
    expect(card?.className).not.toContain('max-w-lg');
    expect(card?.className).not.toContain('var(--spacing-lg)');
    expect(screen.getByText(/up to 3 managed sessions recursively/)).toBeTruthy();
    expect(screen.getByText(/1 session without children will also be deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected' }));
    expect(onConfirm).toHaveBeenCalledWith(true);
  });

  it('can cancel special deletion separately', () => {
    const onCancel = vi.fn();
    render(<SessionDeleteModal sessions={sessions} specialIds={['parent']} normalIds={['plain']} descendantCount={0} onClose={vi.fn()} onConfirm={vi.fn()} onCancelSpecial={onCancel} />);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel special deletion' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
