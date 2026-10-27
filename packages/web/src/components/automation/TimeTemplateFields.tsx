import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { canonicalJobs, canonicalError } from '@/services/canonicalJobs';
import type { JsonObject } from '@/types/canonicalJobs';
import type { SchedulerProvider } from '@/types/automationJobs';
import { newId } from '@/types/automationJobs';
import { CanonicalDefinitionFields } from './CanonicalDefinitionFields';
import { CanonicalSchedulerFields, schedulerRule } from './CanonicalSchedulerFields';
import { fieldClass } from './fields';

/** Passive time compositions use the existing instantiate operation and graph. */
export function TimeTemplateFields({
  value,
  onChange,
}: {
  value: JsonObject;
  onChange: (value: JsonObject) => void;
}) {
  const [provider, setProvider] = useState<SchedulerProvider>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void canonicalJobs
      .creatorProviders()
      .then((page) => {
        if (active)
          setProvider(
            page.items.find(
              (p) => p.providerRef.type === 'time' && p.providerRef.id === 'scheduler',
            ),
          );
      })
      .catch((e: unknown) => {
        if (active) setError(canonicalError(e));
      });
    return () => {
      active = false;
    };
  }, []);
  const composition = (value.composition ?? {}) as JsonObject;
  const creators = (composition.creators ?? []) as JsonObject[];
  const triggers = (composition.triggers ?? []) as JsonObject[];
  const updateCreator = (index: number, definition: JsonObject) =>
    onChange({
      ...value,
      composition: {
        ...composition,
        creators: creators.map((s, i) => (i === index ? { ...s, definition } : s)),
      },
    });
  return (
    <section aria-label="Time Job Template" className="space-y-4">
      <label className="block text-sm">
        Job name
        <input
          required
          className={fieldClass}
          value={String((value.jobDefinition as JsonObject)?.name ?? '')}
          onChange={(e) =>
            onChange({
              ...value,
              jobDefinition: { ...(value.jobDefinition as JsonObject), name: e.target.value },
            })
          }
        />
      </label>
      <CanonicalDefinitionFields
        kind="jobs"
        value={(value.jobDefinition ?? {}) as JsonObject}
        onChange={(jobDefinition) => onChange({ ...value, jobDefinition })}
      />
      {error && <p role="alert">{error}</p>}
      {creators.map((spec, index) => {
        const definition = spec.definition as JsonObject | undefined;
        if (spec.mode !== 'create' || (definition?.providerRef as JsonObject)?.type !== 'time')
          return <p key={String(spec.key)}>Preserved source {String(spec.key)}</p>;
        return (
          <fieldset key={String(spec.key)} aria-label={`Template Scheduler ${index + 1}`}>
            <CanonicalSchedulerFields
              value={definition!.config as JsonObject}
              onChange={(config) => updateCreator(index, { ...definition, config })}
            />
          </fieldset>
        );
      })}
      <Button
        type="button"
        disabled={!provider}
        onClick={() => {
          const key = newId('clock');
          const { configSchema: _schema, ...metadata } = provider!;
          onChange({
            ...value,
            composition: {
              ...composition,
              creators: [
                ...creators,
                {
                  mode: 'create',
                  key,
                  definition: {
                    ...metadata,
                    name: 'Scheduler',
                    config: {
                      rules: [schedulerRule('daily')],
                      misfirePolicy: 'fire_now',
                      graceSeconds: '300',
                      pausePolicy: 'advance',
                    },
                  },
                },
              ],
              triggers: [
                ...triggers,
                {
                  key: newId('trigger'),
                  targetJobKey: 'job',
                  creatorKeys: [key],
                  definition: {
                    name: 'Time trigger',
                    eventTypes: provider!.eventTypes,
                    creatorSelector: { mode: 'ids', ids: [key] },
                    targetJobId: 'job',
                  },
                },
              ],
            },
          });
        }}
      >
        Add Template Scheduler
      </Button>
      <p className="text-xs">
        Saving a Template does not start its Scheduler. Instantiate it to create the Job and time
        subscription together.
      </p>
    </section>
  );
}
