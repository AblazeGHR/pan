// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NewSessionModal } from './NewSessionModal';
import { useSessionStore } from '@/stores/sessionStore';
import { useAdapterStore } from '@/stores/adapterStore';

const apiMock = vi.hoisted(() => ({
  fetchSessionTemplates: vi.fn(async () => []),
  pickDirectory: vi.fn(),
}));

vi.mock('@/services/api', () => apiMock);

describe('NewSessionModal working directory picker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.pickDirectory.mockResolvedValue({ supported: true, path: null });
    useAdapterStore.setState({
      adapters: [{ name: 'cbc', defaultModel: '', supportsResume: false, supportsFork: false }],
      adapterConfigs: {
        cbc: {
          models: [],
          defaultModel: '',
          effortValues: [],
          permissionModes: [],
          defaultPermissionMode: '',
          supportedSettings: [],
          executionModes: ['stream'],
        },
      },
      loadAdapterList: vi.fn(async () => {}),
      loadConfig: vi.fn(async () => {}),
    });
    useSessionStore.setState({ sessions: [] });
  });

  afterEach(cleanup);

  it('shows an add-folder button next to Working Directory', () => {
    render(<NewSessionModal open onClose={() => {}} />);

    expect(screen.getByLabelText('Add folder')).toBeTruthy();
    expect(screen.getByText('Working Directory')).toBeTruthy();
  });

  it('writes a selected directory back to the input', async () => {
    apiMock.pickDirectory.mockResolvedValue({
      supported: true,
      path: 'D:\\projects\\pan',
    });
    render(<NewSessionModal open onClose={() => {}} />);

    fireEvent.click(screen.getByLabelText('Add folder'));

    await waitFor(() =>
      expect((screen.getByPlaceholderText('/path/to/project') as HTMLInputElement).value).toBe(
        'D:\\projects\\pan',
      ),
    );
    expect(apiMock.pickDirectory).toHaveBeenCalledWith(undefined);
  });

  it('keeps the existing directory when selection is cancelled', async () => {
    apiMock.pickDirectory.mockResolvedValue({ supported: true, path: null });
    render(<NewSessionModal open onClose={() => {}} />);
    const input = screen.getByPlaceholderText('/path/to/project');
    fireEvent.change(input, { target: { value: 'D:\\existing' } });

    fireEvent.click(screen.getByLabelText('Add folder'));

    await waitFor(() => expect((input as HTMLInputElement).value).toBe('D:\\existing'));
    expect(apiMock.pickDirectory).toHaveBeenCalledWith('D:\\existing');
  });

  it('keeps manual entry available when the picker is unsupported', async () => {
    apiMock.pickDirectory.mockResolvedValue({
      supported: false,
      path: null,
      reason: 'Folder selection is currently supported on Windows only.',
    });
    render(<NewSessionModal open onClose={() => {}} />);
    const input = screen.getByPlaceholderText('/path/to/project');
    fireEvent.change(input, { target: { value: '/existing' } });

    fireEvent.click(screen.getByLabelText('Add folder'));

    await waitFor(() => expect((input as HTMLInputElement).value).toBe('/existing'));
  });
});
