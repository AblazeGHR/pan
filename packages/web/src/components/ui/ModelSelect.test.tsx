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

  it('applies custom buttonClassName / menuClassName overrides', () => {
    render(
      <ModelSelect
        value="m1"
        options={['m1', 'm2']}
        onChange={() => {}}
        buttonClassName="btn-custom-pill"
        menuClassName="menu-custom-upward"
      />,
    );
    const btn = screen.getByRole('button');
    expect(btn.className).toContain('btn-custom-pill');
    // 覆盖生效：默认 combobox 按钮样式被替换掉
    expect(btn.className).not.toContain('justify-between');
    fireEvent.click(btn);
    // 菜单用自定义定位 class
    expect(document.querySelector('.menu-custom-upward')).toBeTruthy();
  });
});

// ── 额度倍率后缀（仅显示） ──

const MULTIPLIERS: Record<string, number> = {
  'opencode/big-pickle': 0.08,
  'siliconflow-cn/Qwen/Qwen3-14B': 2,
};

describe('ModelSelect multiplier suffix', () => {
  afterEach(() => {
    cleanup();
    document.body.innerHTML = '';
  });

  it('renders the suffix on the current value and on options (display-only)', () => {
    render(
      <ModelSelect
        value="opencode/big-pickle"
        options={OPTIONS}
        multipliers={MULTIPLIERS}
        onChange={() => {}}
      />,
    );
    // 当前值按钮：模型名 + 居右倍率后缀
    const btn = screen.getByRole('button', { name: 'opencode/big-pickle (x0.08)' });
    expect(btn).toBeTruthy();
    // 无倍率的模型只显示原名（不默认 x1）
    expect(screen.queryByRole('button', { name: /x1/ })).toBeNull();

    fireEvent.click(btn);
    expect(screen.getByRole('option', { name: 'opencode/big-pickle (x0.08)' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'siliconflow-cn/Qwen/Qwen3-14B (x2)' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'opencode/mimo-v2.5-free' })).toBeTruthy();
  });

  it('shows (x0) for a zero multiplier and the raw name for unknown models', () => {
    render(
      <ModelSelect
        value="step-5-preview"
        options={['step-5-preview', 'custom/model-a']}
        multipliers={{ 'step-5-preview': 0 }}
        onChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('option', { name: 'step-5-preview (x0)' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'custom/model-a' })).toBeTruthy();
  });

  it('keeps onChange emitting the raw model ID when multipliers are present', () => {
    const onChange = vi.fn();
    render(
      <ModelSelect
        value="opencode/big-pickle"
        options={OPTIONS}
        multipliers={MULTIPLIERS}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    fireEvent.click(screen.getByRole('option', { name: 'siliconflow-cn/Qwen/Qwen3-14B (x2)' }));
    expect(onChange).toHaveBeenCalledWith('siliconflow-cn/Qwen/Qwen3-14B');
  });

  it('keeps raw-ID search working and additionally matches multiplier text', () => {
    render(
      <ModelSelect
        value="opencode/big-pickle"
        options={OPTIONS}
        multipliers={MULTIPLIERS}
        onChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    const input = screen.getByPlaceholderText('筛选模型…');

    // 原有按模型 ID 的搜索不回归
    fireEvent.change(input, { target: { value: 'qwen3' } });
    expect(screen.getByRole('option', { name: 'siliconflow-cn/Qwen/Qwen3-14B (x2)' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'opencode/big-pickle (x0.08)' })).toBeNull();

    // 倍率文本也可检索
    fireEvent.change(input, { target: { value: 'x0.08' } });
    expect(screen.getByRole('option', { name: 'opencode/big-pickle (x0.08)' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'siliconflow-cn/Qwen/Qwen3-14B (x2)' })).toBeNull();
  });

  it('renders without suffix when multipliers are absent (old behavior)', () => {
    renderSelect({ value: 'opencode/big-pickle' });
    expect(screen.getByRole('button', { name: 'opencode/big-pickle' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('option', { name: 'opencode/big-pickle' })).toBeTruthy();
  });
});
