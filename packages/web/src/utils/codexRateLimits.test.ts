import { describe, expect, it } from 'vitest';
import { normalizeCodexQuotaProjection, normalizeCodexRateLimits } from './codexRateLimits';

describe('Codex quota normalization', () => {
  it('maps the provider primary five-hour window and secondary weekly window', () => {
    const normalized = normalizeCodexRateLimits({
      primary: { windowDurationMins: 300, usedPercent: 5 },
      secondary: { windowDurationMins: 10080, usedPercent: 25 },
      unknown: { usedPercent: 77 },
    });

    expect(normalized.fiveHour).toMatchObject({ kind: 'five_hour', usedPercent: 5 });
    expect(normalized.weekly).toMatchObject({ kind: 'weekly', usedPercent: 25 });
    expect(normalized.monthly).toBeUndefined();
  });

  it('projects backend first/secondary windows into five-hour and weekly display fields', () => {
    const normalized = normalizeCodexQuotaProjection({
      windows: {
        first: { kind: 'five_hour', usage: { usedPercent: 20 } },
        secondary: { kind: 'weekly', usage: { remainingPercent: 55 } },
        monthly: { kind: 'monthly', usage: { usedPercent: 60 } },
      },
    });

    expect(normalized.fiveHour).toMatchObject({ kind: 'five_hour', usedPercent: 20 });
    expect(normalized.weekly).toMatchObject({ kind: 'weekly', remainingPercent: 55 });
    expect(normalized.monthly).toMatchObject({ kind: 'monthly', usedPercent: 60 });
  });

  it('does not create display windows from unknown or empty provider data', () => {
    expect(normalizeCodexRateLimits({
      primary: { usedPercent: 5 },
      unknown: { usedPercent: 77 },
    })).toEqual({});

    expect(normalizeCodexQuotaProjection({
      windows: {
        first: { kind: 'unknown', usage: { usedPercent: 5 } },
        secondary: { kind: 'weekly', usage: {} },
      },
    })).toMatchObject({ weekly: { kind: 'weekly' } });
    expect(normalizeCodexQuotaProjection({
      windows: { first: { kind: 'unknown', usage: { usedPercent: 5 } } },
    })).toEqual({});
  });
});
