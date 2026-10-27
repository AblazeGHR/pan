import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { canonicalJobs, type TimeInputResolution } from '@/services/canonicalJobs';
import { fieldClass } from './fields';

const explicit = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$/;

/** Local input is a draft only; the rule receives one resolved instant. */
export function SchedulerInstantInput({
  value,
  label,
  onChange,
}: {
  value: string;
  label: string;
  onChange: (at: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [zone, setZone] = useState('');
  const [resolution, setResolution] = useState<TimeInputResolution | null>(null);
  const field = useRef<HTMLInputElement>(null);
  const written = useRef(value);
  const callback = useRef(onChange);
  callback.current = onChange;
  const sequence = useRef(0);
  const commit = (at: string) => {
    written.current = at;
    callback.current(at);
  };
  useEffect(() => {
    if (value !== written.current) {
      written.current = value;
      setDraft(value);
      setZone('');
    }
  }, [value]);
  useEffect(() => {
    const generation = ++sequence.current;
    const controller = new AbortController();
    if (explicit.test(draft)) {
      setResolution({ status: 'resolved', at: draft, zoneSelection: 'explicit-offset' });
      if (written.current !== draft) commit(draft);
      field.current?.setCustomValidity('');
      return () => controller.abort();
    }
    setResolution(null);
    if (written.current !== '') commit('');
    field.current?.setCustomValidity(
      draft ? 'Resolving time using the Pan host.' : 'Enter a date and time.',
    );
    if (draft)
      void canonicalJobs
        .resolveTimeInput(draft, zone.trim() || undefined, controller.signal)
        .then((result) => {
          if (generation !== sequence.current || controller.signal.aborted) return;
          setResolution(result);
          if (result.status === 'resolved' && result.at) {
            commit(result.at);
            field.current?.setCustomValidity('');
          } else
            field.current?.setCustomValidity(
              result.status === 'ambiguous'
                ? 'Choose one of the two actual times.'
                : result.status === 'nonexistent'
                  ? 'This local time does not exist in the selected timezone.'
                  : 'This time could not be resolved.',
            );
        })
        .catch(() => {
          if (generation !== sequence.current || controller.signal.aborted) return;
          setResolution({ status: 'unavailable' });
          field.current?.setCustomValidity('Pan time resolution is unavailable.');
        });
    return () => {
      controller.abort();
    };
  }, [draft, zone]);
  function invalidate() {
    ++sequence.current;
    field.current?.setCustomValidity('Resolve the new local time before saving.');
    setResolution(null);
    commit('');
  }
  return (
    <section aria-label={`${label} input`} className="space-y-2">
      <label className="block text-sm">
        {label}
        <input
          ref={field}
          required
          className={fieldClass}
          value={draft}
          placeholder="2027-11-01 09:00:12.125"
          onChange={(e) => {
            invalidate();
            setDraft(e.target.value);
          }}
        />
      </label>
      <label className="block text-sm">
        {label} timezone override
        <input
          className={fieldClass}
          value={zone}
          disabled={explicit.test(draft)}
          placeholder="Follow current Pan host timezone"
          onChange={(e) => {
            invalidate();
            setZone(e.target.value);
          }}
        />
      </label>
      <p className="text-xs">
        Blank timezone uses the current Pan server timezone, independently of your browser. Once
        saved, this instant stays fixed when the host timezone changes. An explicit offset is also
        accepted.
      </p>
      {draft && !resolution && <p role="status">Resolving local time…</p>}
      {resolution?.status === 'resolved' && (
        <p role="status">
          Resolved instant: {resolution.at}
          {resolution.timezone ? ` · ${resolution.timezone}` : ''}
        </p>
      )}
      {resolution?.status === 'nonexistent' && (
        <p role="alert">
          This local time does not exist in {resolution.timezone} because of a DST gap. Choose
          another local time.
        </p>
      )}
      {resolution?.status === 'ambiguous' && (
        <fieldset>
          <legend>
            This local time occurs twice in {resolution.timezone}. Choose the actual time.
          </legend>
          {resolution.options?.map((option) => (
            <Button
              key={option.at}
              type="button"
              onClick={() => {
                commit(option.at);
                field.current?.setCustomValidity('');
                setResolution({ ...resolution, status: 'resolved', at: option.at });
              }}
            >
              {option.localTime} · {option.at}
            </Button>
          ))}
        </fieldset>
      )}
      {resolution?.status === 'invalid' && (
        <p role="alert">Enter a valid local date and time (YYYY-MM-DD HH:mm:ss).</p>
      )}
      {resolution?.status === 'unavailable' && (
        <p role="alert">
          The selected timezone or Pan time resolver is unavailable. The time has not been saved.
        </p>
      )}
    </section>
  );
}
