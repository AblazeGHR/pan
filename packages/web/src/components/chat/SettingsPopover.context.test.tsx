// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SettingsPopover } from './SettingsPopover';
import { useAdapterStore } from '@/stores/adapterStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { fetchSession } from '@/services/api';
import type { AdapterConfig, Session } from '@/types';

vi.mock('@/services/api', async () => {
  const actual = await vi.importActual<typeof import('@/services/api')>('@/services/api');
  return { ...actual, fetchSession: vi.fn() };
});

const codexConfig: AdapterConfig = {
  models: ['gpt-5-codex'],
  defaultModel: 'gpt-5-codex',
  effortValues: ['low', 'high'],
  permissionModes: [{ value: 'default', label: 'Default' }],
  defaultPermissionMode: 'default',
  supportedSettings: [
    'model',
    'permissionMode',
    'effort',
    'modelContextWindow',
    'modelAutoCompactTokenLimit',
  ],
};

const cbcConfig: AdapterConfig = {
  ...codexConfig,
  supportedSettings: ['model', 'permissionMode', 'effort'],
};

function makeSession(adapter = 'codex'): Session {
  return {
    id: 'session-1',
    name: 'Codex session',
    adapter,
    model: 'gpt-5-codex',
    permissionMode: 'default',
    alwaysThinkingEnabled: false,
    effort: 'low',
    modelContextWindow: 64000,
    modelAutoCompactTokenLimit: 60800,
    history: [],
  };
}

function setup(session = makeSession(), config = codexConfig) {
  const applySettings = vi.fn().mockResolvedValue({ requireRestart: true });
  const loadSessions = vi.fn().mockResolvedValue(undefined);
  const showToast = vi.fn();
  const onClose = vi.fn();

  useSessionStore.setState({
    sessions: [session],
    currentSessionId: session.id,
    loadSessions,
  });
  useAdapterStore.setState({
    currentAdapter: session.adapter || 'cbc',
    adapterConfigs: { [session.adapter || 'cbc']: config },
    applySettings,
  });
  useUIStore.setState({ showToast });
  useWorkerStore.setState({ currentWorker: null });
  vi.mocked(fetchSession).mockResolvedValue(session);

  const anchor = document.createElement('button');
  document.body.append(anchor);
  const anchorRef = { current: anchor };
  const rendered = render(<SettingsPopover open onClose={onClose} anchorRef={anchorRef} />);
  return { ...rendered, applySettings, loadSessions, showToast, onClose };
}

describe('Codex context settings in SettingsPopover', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    document.body.replaceChildren();
  });

  it('keeps More collapsed, edits positive integers, rejects invalid values, and restores defaults', async () => {
    const { applySettings } = setup();
    await act(async () => {});

    const more = Array.from(document.querySelectorAll('button[aria-expanded="false"]')).find(
      (button) => button.textContent?.includes('More'),
    );
    expect(more?.textContent).toContain('More');
    expect(document.querySelector('input[aria-label="model_context_window"]')).toBeNull();

    fireEvent.click(more!);
    const contextInput = document.querySelector<HTMLInputElement>(
      'input[aria-label="model_context_window"]',
    );
    const autoCompactInput = document.querySelector<HTMLInputElement>(
      'input[aria-label="model_auto_compact_token_limit"]',
    );
    expect(contextInput?.value).toBe('64000');
    expect(autoCompactInput?.value).toBe('60800');

    fireEvent.change(contextInput!, { target: { value: '0' } });
    fireEvent.blur(contextInput!);
    expect(document.body.textContent).toContain('请输入正整数');
    expect(applySettings).not.toHaveBeenCalled();

    fireEvent.change(contextInput!, { target: { value: '64001' } });
    await act(async () => fireEvent.blur(contextInput!));
    expect(applySettings).toHaveBeenCalledWith('session-1', { modelContextWindow: 64001 });

    const restore = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Restore defaults'),
    );
    expect(restore).toBeDefined();
    expect((restore as HTMLButtonElement).disabled).toBe(false);
    await act(async () => fireEvent.click(restore!));
    expect(applySettings).toHaveBeenLastCalledWith('session-1', {
      modelContextWindow: null,
      modelAutoCompactTokenLimit: null,
    });
  });

  it('does not render Codex More controls for another adapter', async () => {
    const session = makeSession('cbc');
    setup(session, cbcConfig);
    await act(async () => {});
    expect(document.body.textContent).not.toContain('More');
    expect(document.querySelector('input[aria-label="model_context_window"]')).toBeNull();
  });

  it('closes on any outside pointer but keeps settings and portal controls open', async () => {
    const { onClose } = setup();
    await act(async () => {});

    const settings = document.querySelector('[data-settings-popover]');
    expect(settings).toBeTruthy();
    fireEvent.pointerDown(settings!);
    expect(onClose).not.toHaveBeenCalled();

    const modelButton = screen.getByRole('button', { name: /gpt-5-codex/ });
    fireEvent.click(modelButton);
    const modelMenu = document.querySelector('[data-model-select-menu]');
    expect(modelMenu).toBeTruthy();
    fireEvent.pointerDown(modelMenu!);
    expect(onClose).not.toHaveBeenCalled();

    const outside = document.createElement('div');
    document.body.append(outside);
    fireEvent.pointerDown(outside);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('keeps Escape available as a direct close action', async () => {
    const { onClose } = setup();
    await act(async () => {});

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
