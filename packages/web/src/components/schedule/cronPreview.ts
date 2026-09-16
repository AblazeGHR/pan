/**
 * Front-side 5-field cron helper used only to preview the next fire times of a
 * *draft* schedule while the user types (a draft has no id yet, so
 * `GET /api/scheduler/next` cannot answer for it). The authoritative fire
 * times always come from the backend — this mirrors its rules so the preview
 * and the engine agree:
 *   minute(0-59) hour(0-23) day-of-month(1-31) month(1-12) day-of-week(0-7)
 * with `*` (any), `star/n` (step), `a-b` (range), `a,b` (list) and single
 * values; when both day-of-month and day-of-week are restricted they are
 * OR-ed (Vixie cron semantics).
 * Returns naive local ISO strings ("2026-09-16T09:00:00"), strictly after
 * `from`, newest-last, or `[]` when the expression is invalid.
 *
 * The file also carries the "simple mode" generator / reverse parser that maps
 * friendly selections (every N minutes, every weekday at HH:MM, …) onto the
 * same cron string — see {@link simpleToCron} / {@link parseSimpleSchedule}.
 */

import type { ScheduleKind } from '@/types';

const MAX_DAYS = 366;

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function parseField(raw: string, min: number, max: number): Set<number> | null {
  const values = new Set<number>();
  for (const part of raw.split(',')) {
    const piece = part.trim();
    if (!piece) return null;
    const slash = piece.indexOf('/');
    const body = (slash >= 0 ? piece.slice(0, slash) : piece).trim();
    const stepRaw = slash >= 0 ? piece.slice(slash + 1).trim() : '';
    let step = 1;
    if (stepRaw) {
      const parsed = Number(stepRaw);
      if (!Number.isInteger(parsed) || parsed <= 0) return null;
      step = parsed;
    }
    let lo: number;
    let hi: number;
    if (body === '*') {
      lo = min;
      hi = max;
    } else if (body.includes('-')) {
      const dash = body.indexOf('-');
      lo = Number(body.slice(0, dash).trim());
      hi = Number(body.slice(dash + 1).trim());
    } else {
      lo = Number(body);
      hi = lo;
    }
    if (!Number.isInteger(lo) || !Number.isInteger(hi) || lo > hi) return null;
    if (lo < min || hi > max) return null;
    for (let v = lo; v <= hi; v += step) values.add(v);
  }
  return values.size > 0 ? values : null;
}

function ascending(values: Set<number>): number[] {
  return [...values].sort((a, b) => a - b);
}

/** All five parsed value-sets of a cron expression plus the raw fields. */
export interface CronFields {
  minute: Set<number>;
  hour: Set<number>;
  dom: Set<number>;
  month: Set<number>;
  dow: Set<number>;
  minuteRaw: string;
  hourRaw: string;
  domRaw: string;
  monthRaw: string;
  dowRaw: string;
}

/** Parse a 5-field expression; `null` when it is syntactically invalid. */
export function parseCronFields(expr: string): CronFields | null {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minuteRaw, hourRaw, domRaw, monthRaw, dowRaw] = fields;
  if (!minuteRaw || !hourRaw || !domRaw || !monthRaw || !dowRaw) return null;

  const minute = parseField(minuteRaw, 0, 59);
  const hour = parseField(hourRaw, 0, 23);
  const dom = parseField(domRaw, 1, 31);
  const month = parseField(monthRaw, 1, 12);
  const rawDow = parseField(dowRaw, 0, 7);
  if (!minute || !hour || !dom || !month || !rawDow) return null;

  // 0 and 7 both mean Sunday.
  const dow = new Set<number>();
  for (const d of rawDow) dow.add(d % 7);

  return { minute, hour, dom, month, dow, minuteRaw, hourRaw, domRaw, monthRaw, dowRaw };
}

/** Naive local ISO ("2026-09-16T09:00:00") — the repo's timestamp convention. */
export function toNaiveIso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
}

/**
 * Next `count` fire times of `expr`, evaluated in the browser's local zone.
 * `count` is clamped to 1..20 (the backend's own preview limit).
 */
export function nextCronFires(expr: string, count = 5, from?: Date): string[] {
  const wanted = Math.min(Math.max(count, 1), 20);
  const parsed = parseCronFields(expr);
  if (!parsed) return [];
  const { minute: minutes, hour: hours, dom: doms, month: months, dow: dows } = parsed;

  const domRestricted = parsed.domRaw.trim() !== '*';
  const dowRestricted = parsed.dowRaw.trim() !== '*';
  const domList = ascending(doms);
  const monthList = ascending(months);
  const dowList = ascending(dows);
  const hourList = ascending(hours);
  const minuteList = ascending(minutes);

  const start = from ? new Date(from.getTime()) : new Date();
  start.setSeconds(0, 0);
  const minTime = start.getTime();

  const out: string[] = [];
  const cursor = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  for (let day = 0; day < MAX_DAYS && out.length < wanted; day++) {
    const month = cursor.getMonth() + 1;
    const date = cursor.getDate();
    if (monthList.includes(month)) {
      const domOk = domList.includes(date);
      const dowOk = dowList.includes(cursor.getDay());
      const dayOk =
        domRestricted && dowRestricted
          ? domOk || dowOk
          : domRestricted
            ? domOk
            : dowRestricted
              ? dowOk
              : true;
      if (dayOk) {
        for (const hour of hourList) {
          for (const minute of minuteList) {
            const candidate = new Date(cursor.getFullYear(), cursor.getMonth(), date, hour, minute);
            if (candidate.getTime() <= minTime) continue;
            out.push(toNaiveIso(candidate));
            if (out.length >= wanted) break;
          }
          if (out.length >= wanted) break;
        }
      }
    }
    cursor.setDate(date + 1);
  }
  return out;
}

/** True when `expr` is a syntactically valid 5-field cron expression. */
export function isValidCron(expr: string): boolean {
  return nextCronFires(expr, 1).length > 0;
}

// ── Simple mode ──
//
// The five-field expression stays the single storage format: "simple" mode is
// purely a UI generator / reverse parser on top of cron. Anything it cannot
// express round-trips to `null` and falls back to advanced mode instead of
// being silently rewritten.

export type SimpleFreq = 'minutes' | 'hours' | 'daily' | 'weekly' | 'monthly';

export interface SimpleSpec {
  freq: SimpleFreq;
  /** minutes / hours — the N in "every N minutes|hours". */
  n: number;
  /** daily / weekly / monthly — wall-clock time "HH:MM". */
  time: string;
  /** weekly — ISO weekdays 1..7 (Mon..Sun), ascending. */
  weekdays: number[];
  /** monthly — day of month, 1..31. */
  day: number;
}

export const MINUTES_RANGE = { min: 1, max: 59 } as const;
export const HOURS_RANGE = { min: 1, max: 23 } as const;
export const MONTH_DAY_RANGE = { min: 1, max: 31 } as const;

export const DEFAULT_SIMPLE: SimpleSpec = {
  freq: 'daily',
  n: 30,
  time: '09:00',
  weekdays: [1],
  day: 1,
};

function clamp(value: number, min: number, max: number): number {
  const n = Number.isFinite(value) ? Math.trunc(value) : Number.NaN;
  if (!Number.isFinite(n) || n < min) return min;
  return n > max ? max : n;
}

function clockTime(hour: number, minute: number): string {
  return `${pad(clamp(hour, 0, 23))}:${pad(clamp(minute, 0, 59))}`;
}

function splitTime(value: string): [number, number] {
  const match = /^(\d{1,2}):(\d{1,2})$/.exec(value.trim());
  return match?.[1] !== undefined && match?.[2] !== undefined
    ? [clamp(Number(match[1]), 0, 23), clamp(Number(match[2]), 0, 59)]
    : [clamp(Number(DEFAULT_SIMPLE.time.slice(0, 2)), 0, 23), 0];
}

/** ISO weekday (1=Mon … 7=Sun) → cron day-of-week (0=Sun … 6=Sat). */
function toCronDow(iso: number): number {
  return clamp(iso, 1, 7) % 7;
}

/** cron day-of-week → ISO weekday. */
function toIsoDow(cron: number): number {
  return cron === 0 ? 7 : clamp(cron, 0, 6);
}

function isFullSet(values: Set<number>, min: number, max: number): boolean {
  if (values.size !== max - min + 1) return false;
  for (let v = min; v <= max; v++) if (!values.has(v)) return false;
  return true;
}

function singleValue(values: Set<number>): number | null {
  if (values.size !== 1) return null;
  const [only] = values;
  return only === undefined ? null : only;
}

/** N of an expanded `star/N` field; null unless every value is a multiple. */
function stepOf(values: Set<number>, max: number): number | null {
  const list = ascending(values);
  const step = list[1];
  if (list[0] !== 0 || step === undefined || step <= 0) return null;
  for (let v = 0; v <= max; v += step) if (!values.has(v)) return null;
  for (const v of list) if (v % step !== 0) return null;
  return step;
}

/** Turn a simple-mode spec into its cron string (always kind=cron). */
export function simpleToCron(spec: SimpleSpec): string {
  switch (spec.freq) {
    case 'minutes':
      return `*/${clamp(spec.n, MINUTES_RANGE.min, MINUTES_RANGE.max)} * * * *`;
    case 'hours':
      return `0 */${clamp(spec.n, HOURS_RANGE.min, HOURS_RANGE.max)} * * *`;
    case 'daily': {
      const [hour, minute] = splitTime(spec.time);
      return `${minute} ${hour} * * *`;
    }
    case 'weekly': {
      const [hour, minute] = splitTime(spec.time);
      const days = [...new Set(spec.weekdays.map(toCronDow))].sort((a, b) => a - b);
      return `${minute} ${hour} * * ${days.length > 0 ? days.join(',') : '*'}`;
    }
    case 'monthly': {
      const [hour, minute] = splitTime(spec.time);
      return `${minute} ${hour} ${clamp(spec.day, MONTH_DAY_RANGE.min, MONTH_DAY_RANGE.max)} * *`;
    }
  }
}

/** Input shape accepted by {@link parseSimpleSchedule}. */
export interface SimpleParseInput {
  kind: ScheduleKind;
  intervalSec?: number | null;
  cron?: string | null;
}

/** An existing interval task shown as "every N minutes|hours" when it is one. */
function parseIntervalToSimple(intervalSec: number): SimpleSpec | null {
  if (!Number.isInteger(intervalSec) || intervalSec <= 0) return null;
  if (intervalSec % 3600 === 0) {
    const hours = intervalSec / 3600;
    if (hours >= HOURS_RANGE.min && hours <= HOURS_RANGE.max) {
      return { ...DEFAULT_SIMPLE, freq: 'hours', n: hours };
    }
  }
  if (intervalSec % 60 === 0) {
    const minutes = intervalSec / 60;
    if (minutes >= MINUTES_RANGE.min && minutes <= MINUTES_RANGE.max) {
      return { ...DEFAULT_SIMPLE, freq: 'minutes', n: minutes };
    }
  }
  return null;
}

/**
 * Reverse-parse a stored schedule into simple mode, or `null` when it uses a
 * combination simple mode cannot express (callers then fall back to advanced
 * mode so the user's cron is never rewritten).
 */
export function parseSimpleSchedule(schedule: SimpleParseInput): SimpleSpec | null {
  if (schedule.kind === 'interval') return parseIntervalToSimple(schedule.intervalSec ?? 0);
  if (schedule.kind !== 'cron' || !schedule.cron) return null;

  const parsed = parseCronFields(schedule.cron);
  if (!parsed) return null;

  const monthFull = isFullSet(parsed.month, 1, 12);
  const domFull = isFullSet(parsed.dom, 1, 31);
  const dowFull = isFullSet(parsed.dow, 0, 6);
  const hourFull = isFullSet(parsed.hour, 0, 23);
  if (!monthFull) return null;

  const minute = singleValue(parsed.minute);
  const hour = singleValue(parsed.hour);
  const dom = singleValue(parsed.dom);
  if (minute !== null && hour !== null) {
    const time = clockTime(hour, minute);
    if (dom !== null && dowFull) return { ...DEFAULT_SIMPLE, freq: 'monthly', time, day: dom };
    if (!dowFull && domFull) {
      const weekdays = ascending(parsed.dow).map(toIsoDow).sort((a, b) => a - b);
      return { ...DEFAULT_SIMPLE, freq: 'weekly', time, weekdays };
    }
    if (dowFull && domFull) return { ...DEFAULT_SIMPLE, freq: 'daily', time };
    return null;
  }

  if (!domFull || !dowFull) return null;
  if (minute === 0) {
    // Every N hours: minute pinned to 0 and the hour field is a pure step.
    const hours = stepOf(parsed.hour, 23);
    if (hours !== null) return { ...DEFAULT_SIMPLE, freq: 'hours', n: hours };
  }
  if (hourFull) {
    const minutes = stepOf(parsed.minute, 59);
    if (minutes !== null) return { ...DEFAULT_SIMPLE, freq: 'minutes', n: minutes };
  }
  return null;
}
