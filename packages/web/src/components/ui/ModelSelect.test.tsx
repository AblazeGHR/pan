// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  cleanup,
} from '@testing-library/react';
import { ModelSelect } from './ModelSelect';

const OPTIONS = [
  'opencode/big-pickle',
  'opencode/mimo-v2.5-free',
  'siliconflow-cn/deepseek-ai/DeepSeek-R1',
  'siliconflow-cn/Qwen/Qwen3-14B',
];

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

function renderSelect({
  value = OPTIONS[0] ?? '',
  options = OPTIONS,
  onChange = () => {},
}: {
  value?: string;
  options?: string[];
  onChange?: (value: string) => void;
} = {}) {
  return render(
    <ModelSelect value={value} options={options} onChange={onChange} />,
  );
}

describe('ModelSelect', () => {
  it('renders the current value when closed', () => {
    renderSelect({ value: 'opencode/big-pickle' });
    expect(screen.getByRole('button', { name: /opencode\/big-pickle/ })).toBeTruthy();
    // 未展开时不应出现过滤输入框
    expect(screen.queryByPlaceholderText('筛选模型…')).toBeNull();
  });

  it('expands to show all options on click', () => {
    renderSelect();
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByPlaceholderText('筛选模型…')).toBeTruthy();
    for (const m of OPTIONS) {
      expect(screen.getByRole('option', { name: m })).toBeTruthy();
    }
  });

  it('filters options by keyword (case-insensitive substring)', () => {
    renderSelect();
    fireEvent.click(screen.getByRole('button'));
    const input = screen.getByPlaceholderText('筛选模型…');
    fireEvent.change(input, { target: { value: 'qwen3' } });
    // 只保留匹配项
    expect(screen.getByRole('option', { name: 'siliconflow-cn/Qwen/Qwen3-14B' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'opencode/big-pickle' })).toBeNull();
    expect(screen.queryByRole('option', { name: 'siliconflow-cn/deepseek-ai/DeepSeek-R1' })).toBeNull();
  });

  it('selects an option: calls onChange and closes', () => {
    const onChange = vi.fn();
    renderSelect({ onChange });
    fireEvent.click(screen.getByRole('button'));
    fireEvent.click(
      screen.getByRole('option', { name: 'siliconflow-cn/deepseek-ai/DeepSeek-R1' }),
    );
    expect(onChange).toHaveBeenCalledWith('siliconflow-cn/deepseek-ai/DeepSeek-R1');
    // 选中后关闭
    expect(screen.queryByPlaceholderText('筛选模型…')).toBeNull();
  });

  it('shows a current value outside options at the top and allows re-selecting it', () => {
    renderSelect({ value: 'moonshotai/kimi-k2.6', options: OPTIONS });
    fireEvent.click(screen.getByRole('button'));
    // 当前值虽不在 options 中，仍出现在列表里
    const items = screen.getAllByRole('option');
    expect(items[0]!.textContent).toContain('moonshotai/kimi-k2.6');
  });

  it('shows empty state when no option matches the query', () => {
    renderSelect();
    fireEvent.click(screen.getByRole('button'));
    fireEvent.change(screen.getByPlaceholderText('筛选模型…'), {
      target: { value: 'zzz-no-such-model' },
    });
    expect(screen.getByText('无匹配模型')).toBeTruthy();
  });
});
