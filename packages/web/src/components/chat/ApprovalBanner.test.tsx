// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { ApprovalBanner } from '@/components/chat/ApprovalBanner';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

const wsMock = vi.hoisted(() => ({
  send: vi.fn(() => true),
}));
const apiMock = vi.hoisted(() => ({
  sendSessionWorkerControl: vi.fn(),
}));

vi.mock('@/services/ws', () => ({ wsClient: wsMock }));
vi.mock('@/services/api', () => ({ sendSessionWorkerControl: apiMock.sendSessionWorkerControl }));

describe('ApprovalBanner Claude permissions', () => {
  beforeEach(() => {
    wsMock.send.mockClear();
    apiMock.sendSessionWorkerControl.mockReset();
    useSessionStore.setState({ currentSessionId: 'session-1' });
    useUIStore.setState({
      approvalRequests: [{
        sessionId: 'session-1',
        workerId: 'worker-1',
        requestId: 'request-1',
        method: 'claude/permission',
        params: { tool_name: 'Bash', input: { command: 'git status' } },
      }],
    });
  });

  afterEach(() => {
    cleanup();
    useUIStore.setState({ approvalRequests: [] });
  });

  it('renders Claude tool details and sends a permission response', () => {
    render(<ApprovalBanner />);

    expect(screen.getByText('Claude requests permission')).toBeTruthy();
    expect(screen.getByText('Bash: {"command":"git status"}')).toBeTruthy();
    const allowButton = screen.getAllByRole('button').find(
      (button) => button.textContent === 'Allow',
    );
    expect(allowButton).toBeTruthy();
    fireEvent.click(allowButton!);

    expect(wsMock.send).toHaveBeenCalledWith({
      type: 'worker_control',
      sessionId: 'session-1',
      control: {
        type: 'permission_response',
        request_id: 'request-1',
        decision: 'accept',
      },
    });
  });
});
