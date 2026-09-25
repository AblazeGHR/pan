import { describe, it, expect } from 'vitest';
import {
  DEFAULT_SIMPLE,
  isValidCron,
  nextCronFires,
  parseSimpleSchedule,
  simpleToCron,
} from './cronPreview';
import type { SimpleSpec } from './cronPreview';

const FROM = new Date(2026, 8, 16, 8, 30, 0, 0); // 2026-09-16 08:30 local

describe('nextCronFires', () => {
  it('rejects malformed expressions', () => {
    expect(nextCronFires('', 5, FROM)).toEqual([]);
    expect(nextCronFires('0 9 * *', 5, FROM)).toEqual([]);
    expect(nextCronFires('99 9 * * *', 5, FROM)).toEqual([]);
    expect(nextCronFires('0 9 * * *', 5, FROM).length).toBeGreaterThan(0);
    expect(isValidCron('0 9 * * *')).toBe(true);
    expect(isValidCron('nope')).toBe(false);
  });

  it('previews a daily expression', () => {
    const fires = nextCronFires('0 9 * * *', 3, FROM);
    expect(fires).toEqual([
      '2026-09-16T09:00:00',
      '2026-09-17T09:00:00',
      '2026-09-18T09:00:00',
    ]);
  });

  it('previews a weekday-only expression (skips the weekend)', () => {
    // 2026-09-16 is a Wednesday; the next Mon-Fri slots skip Sat/Sun.
    const fires = nextCronFires('0 9 * * 1-5', 4, FROM);
    expect(fires).toEqual([
      '2026-09-16T09:00:00',
      '2026-09-17T09:00:00',
      '2026-09-18T09:00:00',
      '2026-09-21T09:00:00',
    ]);
  });

  it('honours steps and lists', () => {
    expect(nextCronFires('*/30 * * * *', 2, FROM)).toEqual([
      '2026-09-16T09:00:00',
      '2026-09-16T09:30:00',
    ]);
    expect(nextCronFires('0 9,18 * * *', 2, FROM)).toEqual([
      '2026-09-16T09:00:00',
      '2026-09-16T18:00:00',
    ]);
  });

  it('treats day-of-week 7 as Sunday', () => {
    const fires = nextCronFires('0 9 * * 7', 1, FROM);
    // 2026-09-20 is a Sunday.
    expect(fires).toEqual(['2026-09-20T09:00:00']);
  });

  it('clamps the requested count to 1..20', () => {
    expect(nextCronFires('0 9 * * *', 0, FROM)).toHaveLength(1);
    expect(nextCronFires('0 9 * * *', 999, FROM)).toHaveLength(20);
  });
});

// ── Simple mode: generator + reverse parser ──

const SIMPLE_CASES: { spec: SimpleSpec; cron: string }[] = [
  { spec: { ...DEFAULT_SIMPLE, freq: 'minutes', n: 5 }, cron: '*/5 * * * *' },
  { spec: { ...DEFAULT_SIMPLE, freq: 'minutes', n: 30 }, cron: '*/30 * * * *' },
  { spec: { ...DEFAULT_SIMPLE, freq: 'hours', n: 1 }, cron: '0 */1 * * *' },
  { spec: { ...DEFAULT_SIMPLE, freq: 'hours', n: 4 }, cron: '0 */4 * * *' },
  { spec: { ...DEFAULT_SIMPLE, freq: 'daily', time: '09:30' }, cron: '30 9 * * *' },
  {
    spec: { ...DEFAULT_SIMPLE, freq: 'weekly', time: '09:00', weekdays: [1, 3, 5] },
    cron: '0 9 * * 1,3,5',
  },
  {
    spec: { ...DEFAULT_SIMPLE, freq: 'weekly', time: '08:05', weekdays: [1, 7] },
    cron: '5 8 * * 0,1',
  },
  {
    spec: { ...DEFAULT_SIMPLE, freq: 'monthly', time: '23:59', day: 31 },
    cron: '59 23 31 * *',
  },
];

describe('simpleToCron', () => {
  it('generates a cron string per simple frequency', () => {
    for (const { spec, cron } of SIMPLE_CASES) {
      expect(simpleToCron(spec)).toBe(cron);
    }
  });

  it('round-trips every generated cron back to the same spec', () => {
    for (const { spec, cron } of SIMPLE_CASES) {
      expect(parseSimpleSchedule({ kind: 'cron', cron })).toEqual(spec);
    }
  });

  it('clamps out-of-range values instead of emitting invalid cron', () => {
    expect(simpleToCron({ ...DEFAULT_SIMPLE, freq: 'minutes', n: 999 })).toBe('*/59 * * * *');
    expect(simpleToCron({ ...DEFAULT_SIMPLE, freq: 'hours', n: 0 })).toBe('0 */1 * * *');
    expect(simpleToCron({ ...DEFAULT_SIMPLE, freq: 'monthly', day: 40 })).toBe('0 9 31 * *');
    // A weekly selection can never end up empty (it would mean "every day").
    expect(simpleToCron({ ...DEFAULT_SIMPLE, freq: 'weekly', weekdays: [] })).toBe('0 9 * * *');
  });
});

describe('parseSimpleSchedule', () => {
  const parse = (cron: string) => parseSimpleSchedule({ kind: 'cron', cron });

  it('reverse-parses the regular shapes', () => {
    expect(parse('*/15 * * * *')).toEqual({ ...DEFAULT_SIMPLE, freq: 'minutes', n: 15 });
    expect(parse('0 */6 * * *')).toEqual({ ...DEFAULT_SIMPLE, freq: 'hours', n: 6 });
    expect(parse('0 * * * *')).toEqual({ ...DEFAULT_SIMPLE, freq: 'hours', n: 1 });
    expect(parse('30 18 * * *')).toEqual({ ...DEFAULT_SIMPLE, freq: 'daily', time: '18:30' });
    expect(parse('0 9 * * 0')).toEqual({
      ...DEFAULT_SIMPLE,
      freq: 'weekly',
      time: '09:00',
      weekdays: [7],
    });
    expect(parse('0 9 1 * *')).toEqual({ ...DEFAULT_SIMPLE, freq: 'monthly', time: '09:00', day: 1 });
  });

  it('reads an existing interval task back as every-N minutes/hours', () => {
    expect(parseSimpleSchedule({ kind: 'interval', intervalSec: 1800 })).toEqual({
      ...DEFAULT_SIMPLE,
      freq: 'minutes',
      n: 30,
    });
    expect(parseSimpleSchedule({ kind: 'interval', intervalSec: 7200 })).toEqual({
      ...DEFAULT_SIMPLE,
      freq: 'hours',
      n: 2,
    });
  });

  it('returns null for anything it cannot express, so callers keep the raw cron', () => {
    expect(parse('0 9,18 * * 1-5')).toBeNull();
    expect(parse('15,45 * * * *')).toBeNull();
    expect(parse('30 * * * *')).toBeNull();
    expect(parse('0 9 * 3 *')).toBeNull();
    expect(parse('0 9 15 * 3')).toBeNull();
    expect(parse('bogus')).toBeNull();
    expect(parseSimpleSchedule({ kind: 'once' })).toBeNull();
    // Non-hour/non-minute intervals (and the 24h one, invalid as star/24).
    expect(parseSimpleSchedule({ kind: 'interval', intervalSec: 45 })).toBeNull();
    expect(parseSimpleSchedule({ kind: 'interval', intervalSec: 86400 })).toBeNull();
  });
});
