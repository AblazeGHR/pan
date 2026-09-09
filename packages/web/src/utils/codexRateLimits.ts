export type CodexQuotaWindowKind = 'weekly' | 'monthly';

export interface CodexQuotaWindow {
  kind: CodexQuotaWindowKind;
  usedPercent?: number;
  remainingPercent?: number;
  usedAmount?: { value: number; unit: 'token' | 'credit' | 'provider' };
  remainingAmount?: { value: number; unit: 'token' | 'credit' | 'provider' };
  resetsAt?: number;
}

export interface CodexQuotaWindows {
  weekly?: CodexQuotaWindow;
  monthly?: CodexQuotaWindow;
}

type RecordValue = Record<string, unknown>;

function asRecord(value: unknown): RecordValue | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as RecordValue
    : null;
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function readNumber(record: RecordValue, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = finiteNumber(record[key]);
    if (value !== undefined) return value;
  }
  return undefined;
}

function readString(record: RecordValue, keys: string[]): string | undefined {
  for (const key of keys) {
    if (typeof record[key] === 'string' && record[key].trim()) return record[key].trim();
  }
  return undefined;
}

function windowDurationMinutes(record: RecordValue): number | undefined {
  const minutes = readNumber(record, [
    'windowDurationMins',
    'window_duration_mins',
    'durationMins',
    'durationMinutes',
    'duration_minutes',
  ]);
  if (minutes !== undefined) return minutes;
  const seconds = readNumber(record, [
    'windowDurationSeconds',
    'window_duration_seconds',
    'limit_window_seconds',
    'durationSeconds',
    'duration_seconds',
  ]);
  return seconds === undefined ? undefined : seconds / 60;
}

function classifyWindow(record: RecordValue, sourceKey: string): CodexQuotaWindowKind | undefined {
  const duration = windowDurationMinutes(record);
  if (duration !== undefined) {
    // An explicit provider duration wins over a label. In particular, a 5h
    // primary window must never be presented as a week or a month.
    if (duration === 7 * 24 * 60) return 'weekly';
    if (duration >= 28 * 24 * 60 && duration <= 31 * 24 * 60) return 'monthly';
    return undefined;
  }

  const name = [
    sourceKey,
    readString(record, ['name', 'windowName', 'window_name', 'limitName', 'limit_name', 'period', 'type']),
  ].filter(Boolean).join(' ').toLowerCase();
  if (/\b(week|weekly|7\s*day|7-day)\b/.test(name)) return 'weekly';
  if (/\b(month|monthly|28\s*day|30\s*day|31\s*day)\b/.test(name)) return 'monthly';
  return undefined;
}

function amount(record: RecordValue, keys: string[]): { value: number; unit: 'token' | 'credit' | 'provider' } | undefined {
  for (const key of keys) {
    const value = finiteNumber(record[key]);
    if (value === undefined) continue;
    const lower = key.toLowerCase();
    return {
      value,
      unit: lower.includes('credit') ? 'credit' : lower.includes('token') ? 'token' : 'provider',
    };
  }
  return undefined;
}

function normalizeWindow(record: RecordValue, kind: CodexQuotaWindowKind): CodexQuotaWindow {
  return {
    kind,
    usedPercent: readNumber(record, ['usedPercent', 'used_percent']),
    remainingPercent: readNumber(record, ['remainingPercent', 'remaining_percent']),
    usedAmount: amount(record, [
      'usedTokens', 'used_tokens', 'tokensUsed', 'tokens_used',
      'usedCredits', 'used_credits', 'creditsUsed', 'credits_used',
      'used',
    ]),
    remainingAmount: amount(record, [
      'remainingTokens', 'remaining_tokens', 'tokensRemaining', 'tokens_remaining',
      'remainingCredits', 'remaining_credits', 'creditsRemaining', 'credits_remaining',
      'remaining',
    ]),
    resetsAt: readNumber(record, ['resetsAt', 'resets_at', 'resetAt', 'reset_at']),
  };
}

/**
 * Normalize only windows whose provider data proves that they are weekly or
 * monthly. The raw app-server snapshot is intentionally untyped because its
 * schema is provider-owned and can add fields without a Pan release.
 */
export function normalizeCodexRateLimits(rateLimits: Record<string, unknown> | undefined): CodexQuotaWindows {
  if (!rateLimits) return {};

  const candidates: Array<{ sourceKey: string; value: RecordValue }> = [];
  for (const [sourceKey, value] of Object.entries(rateLimits)) {
    if (Array.isArray(value)) {
      value.forEach((item, index) => {
        const record = asRecord(item);
        if (record) candidates.push({ sourceKey: `${sourceKey}[${index}]`, value: record });
      });
    } else {
      const record = asRecord(value);
      if (record) candidates.push({ sourceKey, value: record });
    }
  }

  const result: CodexQuotaWindows = {};
  for (const candidate of candidates) {
    const kind = classifyWindow(candidate.value, candidate.sourceKey);
    if (!kind || result[kind]) continue;
    result[kind] = normalizeWindow(candidate.value, kind);
  }
  return result;
}

/** Normalize the stable backend quota projection used by Session Details. */
export function normalizeCodexQuotaProjection(
  quota: unknown,
): CodexQuotaWindows {
  if (!quota) return {};
  const windows = asRecord(quota.windows);
  const result: CodexQuotaWindows = {};
  if (windows) {
    for (const value of Object.values(windows)) {
      const record = asRecord(value);
      if (!record) continue;
      const kind = record.kind;
      if (kind !== 'weekly' && kind !== 'monthly') continue;
      const usage = asRecord(record.usage) ?? {};
      const normalized = normalizeWindow({
        ...record,
        ...usage,
        usedPercent: usage.usedPercent,
        remainingPercent: usage.remainingPercent,
      }, kind);
      if (!result[kind]) result[kind] = normalized;
    }
  }
  if (Object.keys(result).length > 0) return result;

  const rawSnapshots = asRecord(quota.rawSnapshots);
  if (rawSnapshots) {
    const snapshots = Object.values(rawSnapshots);
    for (let index = snapshots.length - 1; index >= 0; index -= 1) {
      const raw = asRecord(snapshots[index]);
      const normalized = normalizeCodexRateLimits(raw ?? undefined);
      if (normalized.weekly || normalized.monthly) return normalized;
    }
  }
  return normalizeCodexRateLimits(asRecord(quota.raw) ?? undefined);
}
