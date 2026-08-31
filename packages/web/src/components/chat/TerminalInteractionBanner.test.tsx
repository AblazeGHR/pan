// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { TerminalInteractionBanner } from './TerminalInteractionBanner';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

describe('TerminalInteractionBanner', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
    useSessionStore.setState({ currentSessionId: 'session-1' });
    useUIStore.setState({
      terminalInteractions: [{
        sessionId: 'session-1', workerId: 'worker-1', itemId: 'item-1',
        processId: 'process-1', stdin: 'Password: ', params: {},
      }],
    });
  });

  it('hides terminal interactions by default', () => {
    const { container } = render(<TerminalInteractionBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders terminal interactions when enabled', () => {
    useAppSettingsStore.setState({ showCodexTerminalInput: true });
    const { getByText } = render(<TerminalInteractionBanner />);
    expect(getByText('Codex is waiting for terminal input')).toBeTruthy();
  });
});
