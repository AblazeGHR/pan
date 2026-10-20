import { describe, it, expect } from 'vitest';
import { formatModelLabel, modelMultiplierSuffix } from './modelLabel';

describe('formatModelLabel', () => {
  it('appends the credit multiplier suffix', () => {
    expect(formatModelLabel('glm-5.3-flash', { 'glm-5.3-flash': 0.08 }))
      .toBe('glm-5.3-flash (x0.08)');
    expect(formatModelLabel('kimi-k3-1', { 'kimi-k3-1': 1.62 }))
      .toBe('kimi-k3-1 (x1.62)');
    expect(formatModelLabel('m', { m: 2 })).toBe('m (x2)');
    expect(formatModelLabel('m', { m: 0.8 })).toBe('m (x0.8)');
  });

  it('treats 0 as a valid multiplier (free model), not as unknown', () => {
    expect(formatModelLabel('step-5-preview', { 'step-5-preview': 0 }))
      .toBe('step-5-preview (x0)');
  });

  it('shows the original name only when the multiplier is unknown', () => {
    // 无倍率表 / 模型不在表中 → 原名，且不得默认 x1
    expect(formatModelLabel('custom/model-a')).toBe('custom/model-a');
    expect(formatModelLabel('custom/model-a', null)).toBe('custom/model-a');
    expect(formatModelLabel('custom/model-a', {})).toBe('custom/model-a');
    expect(formatModelLabel('custom/model-a', { 'glm-5.3-flash': 0.08 }))
      .toBe('custom/model-a');
  });

  it('falls back to the original name for abnormal values', () => {
    expect(formatModelLabel('m', { m: -1 })).toBe('m');
    expect(formatModelLabel('m', { m: Number.NaN })).toBe('m');
    expect(formatModelLabel('m', { m: Number.POSITIVE_INFINITY })).toBe('m');
    expect(formatModelLabel('m', { m: Number.NEGATIVE_INFINITY })).toBe('m');
  });

  it('handles empty model names', () => {
    expect(formatModelLabel('')).toBe('');
    expect(formatModelLabel('', { '': 0.08 })).toBe('');
  });

  it('is idempotent: never duplicates the same suffix', () => {
    const once = formatModelLabel('m', { m: 0.08 });
    expect(formatModelLabel(once, { m: 0.08 })).toBe('m (x0.08)');
    // 模型 ID 自身以同值后缀结尾时也不重复追加
    expect(formatModelLabel('m (x0.08)', { 'm (x0.08)': 0.08 })).toBe('m (x0.08)');
  });
});

describe('modelMultiplierSuffix', () => {
  it('returns the suffix with a leading space', () => {
    expect(modelMultiplierSuffix('m', { m: 0.08 })).toBe(' (x0.08)');
    expect(modelMultiplierSuffix('m', { m: 0 })).toBe(' (x0)');
  });

  it('returns an empty string when there is no valid multiplier', () => {
    expect(modelMultiplierSuffix('m')).toBe('');
    expect(modelMultiplierSuffix('m', {})).toBe('');
    expect(modelMultiplierSuffix('m', { m: -1 })).toBe('');
    expect(modelMultiplierSuffix('')).toBe('');
  });
});
