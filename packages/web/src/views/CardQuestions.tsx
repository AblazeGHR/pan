import { TrustedChoice } from './TrustedChoice';
import {
  cardChoiceBinding,
  type CardQuestion,
  type InteractionCard,
  type QuestionAnswer,
} from './lockerModel';

export function questionErrors(questions: CardQuestion[], answers: Record<string, QuestionAnswer>) {
  const errors: Record<string, string> = Object.create(null);
  for (const question of questions) {
    const answer = Object.hasOwn(answers, question.id) ? answers[question.id] : undefined;
    if (answer && 'rejected' in answer) continue;
    const filled =
      answer &&
      ('text' in answer
        ? !!answer.text.trim()
        : 'choiceId' in answer
          ? !!answer.choiceId
          : answer.choiceIds.length > 0);
    if (question.required !== false && !filled) errors[question.id] = '请作答或拒绝此题';
    if (answer && 'text' in answer && Array.from(answer.text).length > 10000)
      errors[question.id] = '文本答案不能超过 10000 字';
  }
  return errors;
}

export function CardQuestions({
  card,
  answers,
  errors = {},
  disabled,
  readOnly,
  onChange,
}: {
  card: InteractionCard;
  answers: Record<string, QuestionAnswer>;
  errors?: Record<string, string>;
  disabled?: boolean;
  readOnly?: boolean;
  onChange?: (id: string, answer: QuestionAnswer) => void;
}) {
  return (
    <div className="locker-questions">
      {card.definition.questions?.map((question, index) => {
        const answer = Object.hasOwn(answers, question.id) ? answers[question.id] : undefined;
        const rejected = answer && 'rejected' in answer ? answer : undefined;
        const selected =
          answer && 'choiceIds' in answer
            ? answer.choiceIds
            : answer && 'choiceId' in answer
              ? [answer.choiceId]
              : [];
        const problem = Object.hasOwn(errors, question.id) ? errors[question.id] : undefined;
        const errorId = `question-error-${card.id}-${question.id}`;
        return (
          <fieldset
            key={question.id}
            className="locker-question"
            disabled={disabled}
            aria-describedby={problem ? errorId : undefined}
          >
            <legend>
              {index + 1}. {question.prompt} {question.required === false ? '(可选)' : '(必填)'}
            </legend>
            {readOnly ? (
              <div className="locker-question-saved">
                {rejected ? (
                  <p>已拒绝此题{rejected.reason ? `：${rejected.reason}` : ''}</p>
                ) : answer && 'text' in answer ? (
                  <p>{answer.text}</p>
                ) : selected.length ? (
                  <ul>
                    {selected.map((id) => (
                      <li key={id}>{question.choices?.find((c) => c.id === id)?.label ?? id}</li>
                    ))}
                  </ul>
                ) : (
                  <p>
                    {card.status === 'rejected'
                      ? '整卡已拒绝'
                      : card.status === 'no_answer_needed'
                        ? '无需回答'
                        : '未作答（可选）'}
                  </p>
                )}
              </div>
            ) : (
              <>
                {!rejected &&
                  (question.type === 'text' ? (
                    <label className="locker-answer-label">
                      <span>文本答案</span>
                      <textarea
                        aria-label={question.prompt}
                        value={answer && 'text' in answer ? answer.text : ''}
                        aria-invalid={!!problem}
                        aria-describedby={problem ? errorId : undefined}
                        rows={3}
                        onChange={(event) => onChange?.(question.id, { text: event.target.value })}
                      />
                    </label>
                  ) : (
                    <div className="locker-choices">
                      {question.choices?.map((choice) => {
                        const binding = cardChoiceBinding(card, choice.id, question.id);
                        return (
                          <label key={choice.id}>
                            <input
                              type={question.type === 'multi_choice' ? 'checkbox' : 'radio'}
                              name={`question-${card.id}-${question.id}`}
                              checked={selected.includes(choice.id)}
                              disabled={!!choice.triggerRef && !binding}
                              onChange={(event) =>
                                onChange?.(
                                  question.id,
                                  question.type === 'single_choice'
                                    ? { choiceId: choice.id }
                                    : {
                                        choiceIds: event.target.checked
                                          ? [...selected, choice.id]
                                          : selected.filter((id) => id !== choice.id),
                                      },
                                )
                              }
                            />
                            <span>
                              {choice.label}
                              {choice.triggerRef && (
                                <small>
                                  {binding?.summary ?? '执行授权不可用'}
                                  {binding && (
                                    <>
                                      {' '}
                                      · Targets: {binding.targetIds.join(', ')} · Action:{' '}
                                      {binding.actionId} · r{binding.triggerRevision}
                                    </>
                                  )}
                                </small>
                              )}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  ))}
                <button
                  type="button"
                  onClick={() =>
                    onChange?.(
                      question.id,
                      rejected
                        ? question.type === 'text'
                          ? { text: '' }
                          : question.type === 'multi_choice'
                            ? { choiceIds: [] }
                            : { choiceId: '' }
                        : { rejected: true },
                    )
                  }
                >
                  {rejected ? '撤销此题拒绝' : '拒绝此题'}
                </button>
                {rejected && (
                  <label className="locker-answer-label">
                    已拒绝此题 · 原因（可选）
                    <textarea
                      aria-label={`${question.prompt}：拒绝原因`}
                      value={rejected.reason ?? ''}
                      maxLength={10000}
                      rows={2}
                      onChange={(event) =>
                        onChange?.(question.id, {
                          rejected: true,
                          ...(event.target.value ? { reason: event.target.value } : {}),
                        })
                      }
                    />
                  </label>
                )}
              </>
            )}
            {question.choices
              ?.filter((choice) => selected.includes(choice.id) && choice.triggerRef)
              .map((choice) => (
                <TrustedChoice
                  key={choice.id}
                  choice={choice}
                  binding={cardChoiceBinding(card, choice.id, question.id)}
                />
              ))}
            {problem && (
              <p id={errorId} role="alert" className="locker-error">
                {problem}
              </p>
            )}
          </fieldset>
        );
      })}
    </div>
  );
}
