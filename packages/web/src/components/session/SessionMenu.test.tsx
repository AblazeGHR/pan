// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SessionMenu } from './SessionMenu';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { Session, Workspace } from '@/types';

const session: Session = {
  id: 'ses-menu-test',
  name: 'Menu test',
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
};

const workspace: Workspace = { id: 'workspace-a', name: 'Alpha', order: null };

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

describe('SessionMenu Reimport Workspace snapshot', () => {
  it('passes the Workspace active when Reimport starts, before closing the menu', async () => {
    const originalReimport = useSessionStore.getState().reimport;
    const originalWorkspaceId = useUIStore.getState().activeWorkspaceId;
    const reimport = vi.fn(async () => {});
    const onClose = vi.fn(() => useUIStore.setState({ activeWorkspaceId: 'ws-after-click' }));
    useSessionStore.setState({ reimport });
    useUIStore.setState({ activeWorkspaceId: 'ws-at-click' });
    try {
      render(
        <SessionMenu
          session={{ ...session, cliSessionId: 'native-menu-test' }}
          position={{ x: 10, y: 10 }}
          onClose={onClose}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: 'Reimport' }));

      expect(onClose).toHaveBeenCalledTimes(1);
      expect(reimport).toHaveBeenCalledWith(session.id, 'ws-at-click');
      await act(async () => { await Promise.resolve(); });
    } finally {
      useSessionStore.setState({ reimport: originalReimport });
      useUIStore.setState({ activeWorkspaceId: originalWorkspaceId });
    }
  });
});

describe('SessionMenu workspace entry removal', () => {
  // The workspace move action lives in the Manage panel's Workspaces tab now.
  // The menu must not render it (nor its old in-menu submenu), and no remaining
  // menu action may reach for a workspace store mutation.
  it('no longer renders Move to workspace or its dedicated submenu view', () => {
    const moveSessions = vi.fn(async () => []);
    const originalMove = useWorkspaceStore.getState().moveSessions;
    useWorkspaceStore.setState({ moveSessions, workspaces: [workspace], loaded: true });
    try {
      render(<SessionMenu session={session} position={{ x: 10, y: 10 }} onClose={vi.fn()} />);

      expect(screen.queryByRole('button', { name: 'Move to workspace' })).toBeNull();
      expect(screen.queryByText('Back to menu')).toBeNull();
      expect(screen.queryByText('未分组（无工作区）')).toBeNull();
      expect(screen.queryByText('还没有工作区')).toBeNull();
      // The workspace list never leaks into the menu either.
      expect(screen.queryByText('Alpha')).toBeNull();

      // The single main view keeps every remaining action.
      for (const label of ['Rename', 'Manage', 'msgBridge', 'Details', 'Select', 'Delete']) {
        expect(screen.getByRole('button', { name: label })).toBeTruthy();
      }
      expect(moveSessions).not.toHaveBeenCalled();
    } finally {
      useWorkspaceStore.setState({ moveSessions: originalMove });
    }
  });

  it('issues no workspace request when the remaining actions run', () => {
    const moveSessions = vi.fn(async () => []);
    const originalMove = useWorkspaceStore.getState().moveSessions;
    useWorkspaceStore.setState({ moveSessions, workspaces: [workspace], loaded: true });
    const onClose = vi.fn();
    const onRename = vi.fn();
    const onManage = vi.fn();
    const onDetails = vi.fn();
    try {
      render(
        <SessionMenu
          session={session}
          position={{ x: 10, y: 10 }}
          onClose={onClose}
          onRename={onRename}
          onManage={onManage}
          onDetails={onDetails}
        />,
      );

      for (const label of ['Rename', 'Manage', 'msgBridge', 'Details', 'Select']) {
        fireEvent.click(screen.getByRole('button', { name: label }));
      }

      expect(moveSessions).not.toHaveBeenCalled();
      expect(onRename).toHaveBeenCalledWith(session.id);
      expect(onManage).toHaveBeenCalledWith(session.id);
      expect(onDetails).toHaveBeenCalledWith(session.id);
    } finally {
      useWorkspaceStore.setState({ moveSessions: originalMove });
    }
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
