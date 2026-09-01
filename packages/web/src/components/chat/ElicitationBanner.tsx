import { useMemo, useState } from 'react';
import { ExternalLink, MessageCircleQuestion, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { wsClient } from '@/services/ws';
import { sendSessionWorkerControl } from '@/services/api';
import type { ElicitationRequest } from '@/types';

interface ElicitationField {
  key: string;
  label: string;
  description?: string;
  type: string;
  required: boolean;
  enumValues: string[];
  secret: boolean;
}

function fieldsFor(request: ElicitationRequest): ElicitationField[] {
  const schema = request.params.requestedSchema;
  if (!schema || typeof schema !== 'object') return [];
  const properties = (schema as Record<string, unknown>).properties;
  if (!properties || typeof properties !== 'object') return [];
  const required = new Set(
    Array.isArray((schema as Record<string, unknown>).required)
      ? ((schema as Record<string, unknown>).required as unknown[]).filter(
        (key): key is string => typeof key === 'string',
      )
      : [],
  );
  return Object.entries(properties as Record<string, unknown>).map(([key, value]) => {
    const definition = value && typeof value === 'object' ? value as Record<string, unknown> : {};
    const enumValues = Array.isArray(definition.enum)
      ? definition.enum.filter((item): item is string => typeof item === 'string')
      : [];
    return {
      key,
      label: typeof definition.title === 'string' ? definition.title : key,
      description: typeof definition.description === 'string' ? definition.description : undefined,
      type: typeof definition.type === 'string' ? definition.type : 'string',
      required: required.has(key),
      enumValues,
      secret: definition.format === 'password' || definition.secret === true,
    };
  });
}

function requestMessage(request: ElicitationRequest): string {
  const message = request.params.message;
  return typeof message === 'string' && message ? message : 'An MCP server needs additional information';
}

function requestMode(request: ElicitationRequest): string {
  const mode = request.params.mode;
  return typeof mode === 'string' ? mode : 'form';
}

function ElicitationForm({ request }: { request: ElicitationRequest }) {
  const removeRequest = useUIStore((s) => s.removeElicitationRequest);
  const showToast = useUIStore((s) => s.showToast);
  const fields = useMemo(() => fieldsFor(request), [request]);
  const [values, setValues] = useState<Record<string, string>>({});

  const respond = async (action: 'accept' | 'decline' | 'cancel', content: Record<string, unknown> | null = null) => {
    const control = {
      type: 'elicitation_response',
      request_id: request.requestId,
      action,
      content,
    };
    const sent = wsClient.send({
      type: 'worker_control',
      sessionId: request.sessionId,
      control,
    });
    if (!sent) {
      try {
        await sendSessionWorkerControl(request.sessionId, control);
      } catch (error) {
        showToast((error as Error).message || 'MCP 请求未响应', 'error');
        return;
      }
    }
    removeRequest(request.sessionId, request.requestId);
  };

  const submit = () => {
    const content: Record<string, unknown> = {};
    for (const field of fields) {
      const raw = values[field.key] ?? '';
      if (field.required && !raw.trim()) {
        showToast(`请填写 ${field.label}`, 'error');
        return;
      }
      if (!raw.trim()) continue;
      if (field.type === 'boolean') content[field.key] = raw === 'true';
      else if (field.type === 'number' || field.type === 'integer') {
        const number = Number(raw);
        if (!Number.isFinite(number)) {
          showToast(`${field.label} 必须是数字`, 'error');
          return;
        }
        content[field.key] = number;
      } else content[field.key] = raw;
    }
    respond('accept', content);
  };

  const mode = requestMode(request);
  return (
    <div className="rounded-lg border border-accent/50 bg-bg-secondary px-3 py-2 shadow-panel">
      <div className="flex items-start gap-2">
        <MessageCircleQuestion size={17} className="mt-0.5 flex-shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-text-primary">MCP server needs your input</div>
          <div className="mt-1 whitespace-pre-wrap break-words text-xs text-text-secondary">{requestMessage(request)}</div>
          {mode === 'url' && typeof request.params.url === 'string' && (
            <a
              href={request.params.url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex items-center gap-1 break-all text-xs text-accent hover:underline"
            >
              {request.params.url}
              <ExternalLink size={13} />
            </a>
          )}
          {mode !== 'url' && fields.length > 0 && (
            <div className="mt-2 space-y-2">
              {fields.map((field) => (
                <label key={field.key} className="block text-xs text-text-secondary">
                  <span className="mb-1 block">{field.label}{field.required ? ' *' : ''}</span>
                  {field.description && <span className="mb-1 block text-text-tertiary">{field.description}</span>}
                  {field.enumValues.length > 0 ? (
                    <select
                      value={values[field.key] ?? ''}
                      onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))}
                      className="w-full rounded border border-border-default bg-bg-primary px-2 py-1 text-xs text-text-primary"
                    >
                      <option value="">Select an option</option>
                      {field.enumValues.map((option) => <option key={option} value={option}>{option}</option>)}
                    </select>
                  ) : field.type === 'boolean' ? (
                    <select
                      value={values[field.key] ?? ''}
                      onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))}
                      className="w-full rounded border border-border-default bg-bg-primary px-2 py-1 text-xs text-text-primary"
                    >
                      <option value="">Select an option</option>
                      <option value="true">Yes</option>
                      <option value="false">No</option>
                    </select>
                  ) : (
                    <input
                      type={field.secret ? 'password' : field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
                      value={values[field.key] ?? ''}
                      onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))}
                      className="w-full rounded border border-border-default bg-bg-primary px-2 py-1 text-xs text-text-primary placeholder:text-text-tertiary"
                    />
                  )}
                </label>
              ))}
            </div>
          )}
          <div className="mt-3 flex gap-1">
            {mode === 'url' || fields.length === 0 ? (
              <Button size="sm" variant="primary" onClick={() => respond('accept', {})}>Continue</Button>
            ) : (
              <Button size="sm" variant="primary" onClick={submit}>Submit</Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => respond('decline')}>Decline</Button>
          </div>
        </div>
        <button
          type="button"
          className="p-1 text-text-tertiary hover:text-text-primary"
          title="Cancel"
          onClick={() => respond('cancel')}
        >
          <X size={15} />
        </button>
      </div>
    </div>
  );
}

export function ElicitationBanner() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const requests = useUIStore((s) => s.elicitationRequests);
  const visibleRequests = useMemo(
    () => currentSessionId
      ? requests.filter((request) => request.sessionId === currentSessionId)
      : [],
    [currentSessionId, requests],
  );

  if (visibleRequests.length === 0) return null;
  return (
    <div className="mx-3 mb-2 space-y-2">
      {visibleRequests.map((request) => (
        <ElicitationForm key={`${request.workerId}:${String(request.requestId)}`} request={request} />
      ))}
    </div>
  );
}
