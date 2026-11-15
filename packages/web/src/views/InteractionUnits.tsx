import { useRef, useState } from 'react';
import {
  lockerRequest,
  cardChoiceBinding,
  LockerRequestError,
  type InteractionCard,
  type QuestionAnswer,
} from './lockerModel';

export function InteractionUnits({
  card,
  update,
}: {
  card: InteractionCard;
  update: (card: InteractionCard) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, QuestionAnswer>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const intent = useRef<Record<string, unknown> | null>(null);
  const terminal = card.status === 'no_answer_needed';
  const pending = card.units?.filter((u) => u.state === 'pending') ?? [];
  const change = (id: string, answer: QuestionAnswer) =>
    setDrafts((old) => ({ ...old, [id]: answer }));
  const submit = async (ids: string[]) => {
    if (busy) return;
    const answers = Object.fromEntries(
      ids.map((id) => [id, Object.hasOwn(drafts, id) ? drafts[id] : undefined]),
    );
    if (
      !intent.current &&
      ids.some((id) => {
        const a = answers[id];
        return (
          !a ||
          ('text' in a
            ? !a.text.trim()
            : 'choiceId' in a
              ? !a.choiceId
              : 'choiceIds' in a
                ? !a.choiceIds.length
                : false)
        );
      })
    ) {
      setError('请回答或拒绝本次提交的每个问题');
      return;
    }
    setBusy(true);
    setError('');
    intent.current ??= {
      requestId: crypto.randomUUID(),
      expectedRevision: card.revision,
      unitAnswers: answers,
    };
    try {
      const result = await lockerRequest<{
        card: InteractionCard;
        dispatchError?: { message: string };
      }>(`/api/user-inbox/${card.id}/submit`, 'POST', intent.current);
      update(result.card);
      intent.current = null;
      if (result.dispatchError) setError(result.dispatchError.message);
    } catch (e) {
      setError(String(e));
      if (e instanceof LockerRequestError && [403, 409, 422].includes(e.status)) {
        // A proven rejection can be corrected. Unknown transport results keep
        // the exact request identity; retry never creates a second submission.
        intent.current = null;
        try {
          update(
            (await lockerRequest<{ card: InteractionCard }>(`/api/user-inbox/${card.id}`)).card,
          );
        } catch {
          /* retain error */
        }
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="locker-response locker-unit-response" aria-label="Interaction units">
      <p role="status">
        {terminal
          ? '无需回答'
          : card.unitStatus === 'processed'
            ? '全部处理'
            : card.unitStatus === 'partial'
              ? '部分提交'
              : '待答'}
      </p>
      <p>
        回答与补充说明会发送给来源 Agent。BTW 不会修改或阻止已选择的固定动作；拒绝回答不会执行动作。
      </p>
      {card.units?.map((unit, i) => {
        const locked = terminal || unit.state !== 'pending';
        const answer = locked
          ? unit.answer
          : Object.hasOwn(drafts, unit.id)
            ? drafts[unit.id]
            : undefined;
        const rejected = answer && 'rejected' in answer;
        const choices =
          answer && 'choiceIds' in answer
            ? answer.choiceIds
            : answer && 'choiceId' in answer
              ? [answer.choiceId]
              : [];
        return (
          <fieldset className="locker-question" key={unit.id} disabled={busy || !!intent.current}>
            <legend>
              {i + 1}. {unit.prompt} ·{' '}
              {unit.state === 'pending' ? '待答' : unit.state === 'rejected' ? '拒绝' : '回答'}
            </legend>
            {locked ? (
              <div className="locker-question-saved">
                <p>
                  {rejected
                    ? '已拒绝回答此问题'
                    : answer && 'text' in answer
                      ? answer.text
                      : choices
                          .map((id) => unit.choices?.find((c) => c.id === id)?.label ?? id)
                          .join('、')}
                </p>
                {answer?.btw && <p>BTW：{answer.btw}</p>}
                {answer && 'rejected' in answer && answer.reason && <p>{answer.reason}</p>}
                {unit.choices
                  ?.filter((choice) => choices.includes(choice.id))
                  .map((choice) => {
                    const binding = cardChoiceBinding(
                      card,
                      choice.id,
                      card.schemaVersion === 1 ? undefined : unit.id,
                    );
                    return binding?.version === 3 && binding.creation ? (
                      <p key={choice.id}>
                        显式创建 Job：{binding.creation.name} · 创建/启动结果请查看原执行回执
                      </p>
                    ) : null;
                  })}
                <p>{terminal ? '无需回答 · 只读' : '已提交 · 不可修改'}</p>
              </div>
            ) : (
              <>
                {!rejected &&
                  (unit.type === 'text' ? (
                    <label className="locker-answer-label">
                      回答
                      <textarea
                        aria-label={unit.prompt}
                        rows={3}
                        value={answer && 'text' in answer ? answer.text : ''}
                        onChange={(e) =>
                          change(unit.id, { text: e.target.value, btw: answer?.btw })
                        }
                      />
                    </label>
                  ) : (
                    <div className="locker-choices">
                      {unit.choices?.map((choice) => {
                        const binding = cardChoiceBinding(
                          card,
                          choice.id,
                          card.schemaVersion === 1 ? undefined : unit.id,
                        );
                        const creation = binding?.version === 3 ? binding.creation : undefined;
                        return (
                          <div key={choice.id}>
                            <label>
                              <input
                                type={unit.type === 'multi_choice' ? 'checkbox' : 'radio'}
                                name={`${card.id}-${unit.id}`}
                                aria-label={choice.label}
                                checked={choices.includes(choice.id)}
                                disabled={!!(choice.triggerRef || choice.actionRef) && !binding}
                                onChange={(e) =>
                                  change(
                                    unit.id,
                                    unit.type === 'single_choice'
                                      ? { choiceId: choice.id, btw: answer?.btw }
                                      : {
                                          choiceIds: e.target.checked
                                            ? [...choices, choice.id]
                                            : choices.filter((id) => id !== choice.id),
                                          btw: answer?.btw,
                                        },
                                  )
                                }
                              />
                              <span>
                                <strong>{choice.label}</strong>
                                {binding ? (
                                  <small>
                                    {creation
                                      ? `将显式创建完整 Job：${creation.name} · ${creation.startRequested ? '请求启动/启用，实际以原授权和回执为准' : '创建后保持禁用'}`
                                      : `固定执行能力：${binding.summary}`}
                                  </small>
                                ) : choice.actionRef || choice.triggerRef ? (
                                  <small>
                                    缺少受信可绑定能力，此选项不可执行；来源需重新核验固定引用。
                                  </small>
                                ) : null}
                              </span>
                            </label>
                            {binding && (
                              <details className="locker-action-proof">
                                <summary>固定动作详情</summary>
                                <pre>{JSON.stringify(binding, null, 2)}</pre>
                              </details>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ))}
                <button
                  type="button"
                  onClick={() =>
                    change(
                      unit.id,
                      rejected
                        ? unit.type === 'text'
                          ? { text: '', btw: answer?.btw }
                          : unit.type === 'multi_choice'
                            ? { choiceIds: [], btw: answer?.btw }
                            : { choiceId: '', btw: answer?.btw }
                        : { rejected: true, btw: answer?.btw },
                    )
                  }
                >
                  {rejected ? '撤销拒绝' : '拒绝回答此问题'}
                </button>
                {(unit.type !== 'text' || rejected) && (
                  <label className="locker-answer-label">
                    BTW（选填）
                    <textarea
                      aria-label={`${unit.prompt} BTW`}
                      rows={2}
                      maxLength={10000}
                      value={answer?.btw ?? ''}
                      onChange={(e) =>
                        change(unit.id, {
                          ...(answer ??
                            (unit.type === 'multi_choice' ? { choiceIds: [] } : { choiceId: '' })),
                          btw: e.target.value,
                        })
                      }
                    />
                  </label>
                )}
                <button type="button" onClick={() => void submit([unit.id])}>
                  提交此问题
                </button>
              </>
            )}
            {unit.executions?.map((receipt, n) => (
              <p key={receipt.eventId ?? n}>
                执行：{receipt.state} {receipt.receiptId ?? ''}
              </p>
            ))}
          </fieldset>
        );
      })}
      {!terminal && pending.length > 0 && (
        <button type="button" disabled={busy} onClick={() => void submit(pending.map((u) => u.id))}>
          {intent.current ? '查询并重试原提交' : '提交全部未处理问题'}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
