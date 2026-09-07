// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyText } from './clipboard';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('copyText', () => {
  it('uses the async clipboard API when available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    await copyText('D:\\project\\src\\main.ts');

    expect(writeText).toHaveBeenCalledWith('D:\\project\\src\\main.ts');
  });

  it('falls back to execCommand when the async API is unavailable or rejected', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('permission denied'));
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn() });
    const execCommand = vi.spyOn(document, 'execCommand').mockReturnValue(true);

    await copyText('D:\\project\\README.md');

    expect(execCommand).toHaveBeenCalledWith('copy');
  });

  it('rejects when neither copy mechanism succeeds', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn() });
    vi.spyOn(document, 'execCommand').mockReturnValue(false);

    await expect(copyText('D:\\project\\missing.txt')).rejects.toThrow('Clipboard copy failed');
  });
});
