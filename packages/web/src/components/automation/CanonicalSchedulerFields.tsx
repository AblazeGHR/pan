import { Button } from '@/components/ui/Button';
import { newId } from '@/types/automationJobs';
import type { JsonObject } from '@/types/canonicalJobs';
import { fieldClass } from './fields';
import { SchedulerInstantInput } from './SchedulerInstantInput';

export const schedulerRule = (type: string, ruleId = newId('rule')): JsonObject => ({
  ruleId,
  type,
  ...(type === 'interval'
    ? { seconds: '3600', anchor: new Date().toISOString(), advanceMode: 'grid' }
    : type === 'once'
      ? { at: '' }
      : type === 'weekly'
        ? { weekday: 0, time: '09:00:00' }
        : type === 'daily'
          ? { time: '09:00:00' }
          : { expression: '0 9 * * *' }),
});
const dst = [
  'strict',
  'gap-skip-fold-first',
  'gap-skip-fold-second',
  'gap-shift-fold-first',
  'gap-shift-fold-second',
];
export function CanonicalSchedulerFields({
  value,
  onChange,
}: {
  value: JsonObject;
  onChange: (value: JsonObject) => void;
}) {
  const rules = Array.isArray(value.rules) ? (value.rules as JsonObject[]) : [];
  const setRules = (next: JsonObject[]) => onChange({ ...value, rules: next });
  const limit = value.driveLimit as JsonObject | undefined;
  return (
    <section aria-label="Time rules" className="space-y-4">
      {rules.map((rule, index) => {
        const update = (key: string, next: unknown) =>
          setRules(
            rules.map((r, i) => {
              if (i !== index) return r;
              const updated = { ...r, [key]: next };
              if (key === 'timezone' && next === '') delete updated.timezone;
              return updated;
            }),
          );
        const text = (key: string, label: string, placeholder?: string) => (
          <label className="block text-sm">
            {label}
            <input
              required={key !== 'timezone'}
              pattern={
                key === 'seconds'
                    ? '(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?'
                    : key === 'time'
                      ? '(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]'
                      : undefined
              }
              className={fieldClass}
              value={String(rule[key] ?? '')}
              placeholder={placeholder}
              onChange={(e) => update(key, e.target.value)}
            />
          </label>
        );
        return (
          <fieldset
            key={String(rule.ruleId)}
            aria-label={`Time rule ${index + 1}`}
            className="min-w-0 space-y-3 rounded border border-border-default p-3"
          >
            <legend>Time rule {index + 1}</legend>
            <p className="break-all text-xs">Rule ID: {String(rule.ruleId)}</p>
            <label className="block text-sm">
              Time rule type
              <select
                aria-label={'Time rule type'}
                className={fieldClass}
                value={String(rule.type)}
                onChange={(e) =>
                  setRules(
                    rules.map((r, i) =>
                      i === index ? schedulerRule(e.target.value, String(rule.ruleId)) : r,
                    ),
                  )
                }
              >
                {['interval', 'once', 'daily', 'weekly', 'cron'].map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
                {!['interval', 'once', 'daily', 'weekly', 'cron'].includes(String(rule.type)) && (
                  <option value={String(rule.type)}>{String(rule.type)} (preserved)</option>
                )}
              </select>
            </label>
            {rule.type === 'interval' && (
              <>
                {text('seconds', 'Interval seconds', '0.125')}
                <SchedulerInstantInput value={String(rule.anchor ?? '')} label="Anchor date and time" onChange={(at) => update('anchor', at)} />
                <label className="block text-sm">
                  Interval advance mode
                  <select
                    aria-label={'Interval advance mode'}
                    className={fieldClass}
                    value={String(rule.advanceMode)}
                    onChange={(e) => update('advanceMode', e.target.value)}
                  >
                    <option value="grid">Keep the anchored grid</option>
                    <option value="afterPublication">After publication</option>
                  </select>
                </label>
              </>
            )}
            {rule.type === 'once' && (
              <>
                <SchedulerInstantInput value={String(rule.at ?? '')} label="Scheduled date and time" onChange={(at) => update('at', at)} />
              </>
            )}
            {rule.type === 'weekly' && (
              <>
                <label className="block text-sm">
                  Weekday
                  <select
                    aria-label={'Weekday'}
                    className={fieldClass}
                    value={String(rule.weekday)}
                    onChange={(e) => update('weekday', Number(e.target.value))}
                  >
                    {[
                      'Monday',
                      'Tuesday',
                      'Wednesday',
                      'Thursday',
                      'Friday',
                      'Saturday',
                      'Sunday',
                    ].map((day, i) => (
                      <option key={day} value={i}>
                        {day}
                      </option>
                    ))}
                  </select>
                </label>
                {text('time', 'Time of day', '09:00:12')}
              </>
            )}
            {rule.type === 'daily' && text('time', 'Time of day', '09:00:12')}
            {rule.type === 'cron' && text('expression', 'Cron expression', '0 9 * * *')}
            {(rule.type === 'daily' || rule.type === 'weekly' || rule.type === 'cron') && (
              <>
                {text(
                  'timezone',
                  'Timezone',
                  'Follow current Pan host timezone (optional override)',
                )}
                <p className="text-xs">
                  Blank follows the current Pan host timezone. DST gaps are skipped; repeated times
                  run once.
                </p>
                {rule.dstPolicy !== undefined && (
                  <label className="block text-sm">
                    DST policy
                    <select
                      aria-label={'DST policy'}
                      className={fieldClass}
                      value={String(rule.dstPolicy)}
                      onChange={(e) => update('dstPolicy', e.target.value)}
                    >
                      {[...new Set([...dst, String(rule.dstPolicy)])].map((policy) => (
                        <option key={policy}>{policy}</option>
                      ))}
                    </select>
                  </label>
                )}
              </>
            )}
            <Button
              type="button"
              disabled={rules.length < 2}
              onClick={() => setRules(rules.filter((_, i) => i !== index))}
            >
              Remove time rule {index + 1}
            </Button>
          </fieldset>
        );
      })}
      <Button
        type="button"
        disabled={rules.length >= 64}
        onClick={() => setRules([...rules, schedulerRule('interval')])}
      >
        Add time rule
      </Button>
      <p className="text-xs text-text-secondary">
        Timestamps retain seconds, fractional digits and explicit UTC offsets. Editing a field keeps
        the other rules and their IDs.
      </p>
      <label className="block text-sm">
        Missed trigger policy
        <select
          aria-label={'Missed trigger policy'}
          className={fieldClass}
          value={String(value.misfirePolicy)}
          onChange={(e) => onChange({ ...value, misfirePolicy: e.target.value })}
        >
          <option value="fire_now">Fire once after downtime</option>
          <option value="skip">Skip missed times</option>
        </select>
      </label>
      <label className="block text-sm">
        Grace period (seconds)
        <input
          required
          pattern="(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
          className={fieldClass}
          value={String(value.graceSeconds ?? '')}
          onChange={(e) => onChange({ ...value, graceSeconds: e.target.value })}
        />
      </label>
      <label className="block text-sm">
        Pause policy
        <select
          aria-label={'Pause policy'}
          className={fieldClass}
          value={String(value.pausePolicy)}
          onChange={(e) => onChange({ ...value, pausePolicy: e.target.value })}
        >
          {[...new Set(['advance', String(value.pausePolicy)])].map((policy) => (
            <option key={policy}>{policy}</option>
          ))}
        </select>
      </label>
      <label className="block text-sm">
        <input
          type="checkbox"
          checked={!!limit}
          onChange={(e) => {
            const next = { ...value };
            if (e.target.checked) next.driveLimit = { maxEvents: 1, initialProducedCount: 0 };
            else delete next.driveLimit;
            onChange(next);
          }}
        />
        Limit produced events
      </label>
      {limit && (
        <>
          <label className="block text-sm">
            Maximum events
            <input
              required
              className={fieldClass}
              type="number"
              min={1}
              step={1}
              value={String(limit.maxEvents)}
              onChange={(e) =>
                onChange({
                  ...value,
                  driveLimit: {
                    ...limit,
                    maxEvents: e.target.value === '' ? '' : Number(e.target.value),
                  },
                })
              }
            />
          </label>
          <label className="block text-sm">
            Initial produced count
            <input
              required
              className={fieldClass}
              type="number"
              min={0}
              step={1}
              value={String(limit.initialProducedCount)}
              onChange={(e) =>
                onChange({
                  ...value,
                  driveLimit: {
                    ...limit,
                    initialProducedCount: e.target.value === '' ? '' : Number(e.target.value),
                  },
                })
              }
            />
          </label>
        </>
      )}
    </section>
  );
}
