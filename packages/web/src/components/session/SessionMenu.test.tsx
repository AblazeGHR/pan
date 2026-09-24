// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
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
  vi.useRealTimers();
});

function renderMenuWithCard(onClose: () => void) {
  vi.useFakeTimers();
  const view = render(
    <div data-testid="session-card">
      <span data-testid="card-content">Session card content</span>
      <SessionMenu session={session} position={{ x: 10, y: 10 }} onClose={onClose} />
    </div>,
  );
  // SessionMenu defers its document listener so the opening click cannot
  // immediately dismiss the portal menu.
  act(() => vi.advanceTimersByTime(0));
  return view;
}

describe('SessionMenu details entry', () => {
  it('offers Details and closes the menu before opening the modal', () => {
    const onClose = vi.fn();
    const onDetails = vi.fn();
    render(<SessionMenu session={session} position={{ x: 10, y: 10 }} onClose={onClose} onDetails={onDetails} />);

    fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onDetails).toHaveBeenCalledWith(session.id);
  });

  it('routes Rename to the in-app rename flow without using prompt', () => {
    const onClose = vi.fn();
    const onRename = vi.fn();
    const promptSpy = vi.spyOn(window, 'prompt');
    render(<SessionMenu session={session} position={{ x: 10, y: 10 }} onClose={onClose} onRename={onRename} />);
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onRename).toHaveBeenCalledWith(session.id);
    expect(promptSpy).not.toHaveBeenCalled();
  });
});

describe('SessionMenu workspace action', () => {
  it('labels the move action in English and opens the workspace destination list', () => {
    render(<SessionMenu session={session} position={{ x: 10, y: 10 }} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Move to workspace' }));

    expect(screen.getByRole('button', { name: /未分组/ })).toBeTruthy();
  });
});

describe('SessionMenu click-away dismissal', () => {
  it('does not close when clicking inside the portal menu', () => {
    const onClose = vi.fn();
    renderMenuWithCard(onClose);

    const menu = document.body.querySelector('.fixed');
    expect(menu).toBeTruthy();
    fireEvent.click(menu!);

    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes when clicking elsewhere inside the same session card', () => {
    const onClose = vi.fn();
    renderMenuWithCard(onClose);

    fireEvent.click(screen.getByTestId('card-content'));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes when clicking outside the session card', () => {
    const onClose = vi.fn();
    renderMenuWithCard(onClose);
    const outside = document.createElement('button');
    outside.textContent = 'Outside';
    document.body.append(outside);

    fireEvent.click(outside);

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('keeps Escape dismissal available', () => {
    const onClose = vi.fn();
    renderMenuWithCard(onClose);

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
