// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePlatformStore } from '@/stores/platformStore';
import { DirectoryInput } from './DirectoryInput';

const { fetchDirectoriesMock } = vi.hoisted(() => ({ fetchDirectoriesMock: vi.fn() }));

vi.mock('@/services/api', () => ({ fetchDirectories: fetchDirectoriesMock }));

function listing(current: string, parent: string | null, entries: { name: string; path: string }[]) {
  return {
    current,
    parent,
    entries: entries.map((entry) => ({ ...entry, isDirectory: true })),
  };
}

describe('DirectoryInput path joining', () => {
  beforeEach(() => {
    fetchDirectoriesMock.mockReset();
    usePlatformStore.setState({ platform: null, capabilities: null });
  });
  afterEach(cleanup);

  it('appends a backslash for a Windows listing path', async () => {
    fetchDirectoriesMock.mockResolvedValue(
      listing('D:\\workspace', 'D:\\', [{ name: 'app', path: 'D:\\workspace\\app' }]),
    );
    const onChange = vi.fn();
    render(<DirectoryInput value="D:\\workspace" onChange={onChange} />);
    fireEvent.click(await screen.findByText('app'));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('D:\\workspace\\app\\'));
  });

  it('appends a forward slash for a POSIX listing path', async () => {
    fetchDirectoriesMock.mockResolvedValue(
      listing('/srv', '/', [{ name: 'app', path: '/srv/app' }]),
    );
    const onChange = vi.fn();
    render(<DirectoryInput value="/srv" onChange={onChange} />);
    fireEvent.click(await screen.findByText('app'));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('/srv/app/'));
  });

  it('keeps "up one level" POSIX-shaped', async () => {
    fetchDirectoriesMock.mockResolvedValue(listing('/srv/app', '/srv', []));
    const onChange = vi.fn();
    render(<DirectoryInput value="/srv/app/" onChange={onChange} />);
    fireEvent.click(await screen.findByRole('button', { name: /Up one level/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('/srv/'));
  });

  it('returns the exact directory for New Session selection (no trailing separator)', async () => {
    fetchDirectoriesMock.mockResolvedValue(
      listing('/srv', '/', [{ name: 'app', path: '/srv/app' }]),
    );
    const onChange = vi.fn();
    const onSelect = vi.fn();
    render(
      <DirectoryInput value="/srv" onChange={onChange} onSelect={onSelect} selectDirectories />,
    );
    fireEvent.click(await screen.findByText('app'));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('/srv/app'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('does not double a trailing separator the server already returned', async () => {
    fetchDirectoriesMock.mockResolvedValue(
      listing('\\\\server\\share', '\\\\server', [{ name: 'team', path: '\\\\server\\share\\team' }]),
    );
    const onChange = vi.fn();
    render(<DirectoryInput value="\\\\server\\share" onChange={onChange} />);
    fireEvent.click(await screen.findByText('team'));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('\\\\server\\share\\team\\'));
  });

  it('uses a POSIX placeholder once the platform probe answers', () => {
    usePlatformStore.setState({ platform: 'linux', capabilities: null });
    render(<DirectoryInput value="" onChange={() => {}} />);
    expect(screen.getByLabelText('Directory path').getAttribute('placeholder'))
      .toBe('/home/user/project');
  });

  it('keeps the Windows placeholder while the platform is unknown', () => {
    render(<DirectoryInput value="" onChange={() => {}} />);
    expect(screen.getByLabelText('Directory path').getAttribute('placeholder'))
      .toBe('D:\\project\\app');
  });
});
