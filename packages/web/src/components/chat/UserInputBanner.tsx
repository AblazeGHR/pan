import { useMemo, useState } from 'react';
import { MessageCircleQuestion, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { wsClient } from '@/services/ws';
import type { UserInputQuestion, UserInputRequest } from '@/types';

function questionOptions(question: UserInputQuestion): Array<{ label: string; description?: string }> {
  return Array.isArray(question.options)
    ? question.options.filter((option) => option && typeof option.label === 'string')
    : [];
}

function UserInputForm({ request }: { request: UserInputRequest }) {
  const removeRequest = useUIStore((s) => s.removeUserInputRequest);
  const showToast = useUIStore((s) => s.showToast);
  const [values, setValues] = useState<Record<string, string>>({});

  const respond = (answers: Record<string, { answers: string[] }>) => {
    const sent = wsClient.send({
      type: 'worker_control',
      workerId: request.workerId,
      control: {
        type: 'user_input_response',
        request_id: request.requestId,
        answers,
      },
    });
    if (!sent) {
      showToast('未连接到服务器，回答未发送', 'error');
      return;
    }
    removeRequest(request.sessionId, request.requestId);
  };

  const submit = () => {
    const answers: Record<string, { answers: string[] }> = {};
    for (const question of request.questions) {
      const value = values[question.id]?.trim();
      if (!value) continue;
      const options = questionOptions(question);
      const isOther = value === '__other__' || value.startsWith('__other__:');
      const answerValue = value.startsWith('__other__:') ? value.slice('__other__:'.length).trim() : value;
      const isFreeForm = options.length === 0 || isOther;
      if (!answerValue) continue;
      answers[question.id] = {
        answers: [isFreeForm ? `user_note: ${answerValue}` : answerValue],
      };
    }
    respond(answers);
  };

  return (
    <div className="rounded-lg border border-accent/50 bg-bg-secondary px-3 py-2 shadow-panel">
      <div className="flex items-start gap-2">
        <MessageCircleQuestion size={17} className="mt-0.5 flex-shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-text-primary">Codex needs your input</div>
          <div className="mt-2 space-y-3">
            {request.questions.map((question) => {
              const options = questionOptions(question);
              const value = values[question.id] ?? '';
              const showFreeForm = options.length === 0 || value === '__other__';
              return (
                <div key={question.id}>
                  <label className="mb-1 block text-xs text-text-secondary">
                    {question.header || question.id}: {question.question || ''}
                  </label>
                  {options.length > 0 && (
                    <select
                      value={value}
                      onChange={(event) => setValues((current) => ({ ...current, [question.id]: event.target.value }))}
                      className="w-full rounded border border-border-default bg-bg-primary px-2 py-1 text-xs text-text-primary"
                    >
                      <option value="">Select an option</option>
                      {options.map((option) => (
                        <option key={option.label} value={option.label}>
                          {option.label}{option.description ? ` — ${option.description}` : ''}
                        </option>
                      ))}
                      {question.isOther && <option value="__other__">Other</option>}
                    </select>
                  )}
                  {showFreeForm && (
                    <input
                      type={question.isSecret ? 'password' : 'text'}
                      value={options.length > 0 && (value === '__other__' || value.startsWith('__other__:'))
                        ? value === '__other__' ? '' : value.slice('__other__:'.length)
                        : value}
                      onChange={(event) => setValues((current) => ({
                        ...current,
                        [question.id]: options.length > 0 && value === '__other__'
                          ? '__other__:' + event.target.value
                          : event.target.value,
                      }))}
                      placeholder="Type your answer"
                      className="mt-1 w-full rounded border border-border-default bg-bg-primary px-2 py-1 text-xs text-text-primary placeholder:text-text-tertiary"
                    />
                  )}
                </div>
              );
            })}
          </div>
          <div className="mt-3 flex gap-1">
            <Button size="sm" variant="primary" onClick={submit}>Submit</Button>
            <Button size="sm" variant="ghost" onClick={() => respond({})}>Skip</Button>
          </div>
        </div>
        <button
          type="button"
          className="p-1 text-text-tertiary hover:text-text-primary"
          title="Skip"
          onClick={() => respond({})}
        >
          <X size={15} />
        </button>
      </div>
    </div>
  );
}

export function UserInputBanner() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const requests = useUIStore((s) => s.userInputRequests);
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
        <UserInputForm key={`${request.workerId}:${String(request.requestId)}`} request={request} />
      ))}
    </div>
  );
}
