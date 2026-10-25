import { AttachedReminder } from './AttachedReminder';
import { TrustedChoice } from './TrustedChoice';
import { CardQuestions, questionErrors } from './CardQuestions';
import { UserCardRow, type UserCardRowState } from './UserCardRow';
import { RelatedContextButton } from './RelatedContextButton';
import { LockerMessageRow } from './LockerMessageRow';
import { CardIdChip } from './CardIdChip';
import { SessionDragGutter } from '@/components/session/SessionDragGutter';
import { createPortal } from 'react-dom';
import { SidebarPanelContext } from '@/components/layout/SidebarPanelContext';
import {
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Archive,
  ArrowDown,
  Link2,
  PanelLeft,
  Check,
  CheckCheck,
  Circle,
  ExternalLink,
  Folder,
  Loader2,
  RefreshCw,
  Search,
  WifiOff,
} from 'lucide-react';
import { MarkdownRenderer } from '@/components/chat/MarkdownRenderer';
import { MessageTimestamp } from '@/components/chat/MessageTimestamp';
import { Button } from '@/components/ui/Button';
import { attachmentMarkdown, serverAttachmentDownloadHref } from '@/utils/attachmentMarkdown';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useAutomationAuth } from '@/stores/automationStore';
import { JobUserAuthorization } from '@/components/automation/JobUserAuthorization';
import { ALL_WORKSPACES, UNGROUPED_WORKSPACES } from '@/utils/sessionFilters';
import {
  cardChoiceBinding,
  type QuestionAnswer,
  lockerProjection,
  lockerTimeline,
  lockerEntryWindow,
  isUserCard,
  type UserCard,
  loadLockerMessages,
  compareLockerItems,
  lockerItemPreview,
  type LockerItem,
  type LockerMessage,
  lockerRequest,
  LockerRequestError,
  type InteractionCard,
  type Locker,
} from './lockerModel';
import '@/components/session/sessionQuickActions.css';

function sourceCards(cards: LockerItem[], lockerId: string) {
  return cards.filter((card) => card.lockerId === lockerId);
}

function Card({
  card,
  source,
  update,
  remove,
  hidden,
}: {
  card: InteractionCard;
  hidden?: boolean;
  source?: Locker;
  update: (card: InteractionCard) => void;
  remove: (id: string) => void;
}) {
  const [answer, setAnswer] = useState(
    card.response && card.response.outcome !== 'reject' ? (card.response.answer ?? '') : '',
  );
  const [answers, setAnswers] = useState<Record<string, QuestionAnswer>>({});
  const [questionProblems, setQuestionProblems] = useState<Record<string, string>>({});
  const questions = card.definition.questions;
  const [choiceId, setChoiceId] = useState(card.response?.choiceId ?? '');
  const [reason, setReason] = useState(
    card.response?.outcome === 'reject' ? (card.response.answer ?? '') : '',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const intent = useRef<{
    operation: 'submit' | 'reject' | 'delete';
    payload: Record<string, unknown>;
  } | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const open = card.status === 'open';
  const noAnswer = card.status === 'no_answer_needed';
  useEffect(() => {
    if (!open) {
      setAnswer(
        card.response && card.response.outcome !== 'reject' ? (card.response.answer ?? '') : '',
      );
      setReason(card.response?.outcome === 'reject' ? (card.response.answer ?? '') : '');
      setChoiceId(card.response?.choiceId ?? '');
    }
  }, [card.response, open]);
  const act = async (operation: 'submit' | 'reject' | 'delete') => {
    if (busy) return;
    if (operation === 'submit' && questions && !intent.current) {
      const problems = questionErrors(questions, answers);
      setQuestionProblems(problems);
      if (Object.keys(problems).length) return;
    }
    setBusy(true);
    setError('');
    try {
      if (operation === 'delete') {
        await lockerRequest(`/api/user-inbox/${card.id}`, 'DELETE');
        remove(card.id);
        return;
      }
      if (intent.current && intent.current.operation !== operation)
        throw new Error(
          'The previous action is uncertain. Retry the same action to resolve its result.',
        );
      const payload: Record<string, unknown> = intent.current?.payload ?? {
        requestId: crypto.randomUUID(),
        expectedRevision: card.revision,
      };
      if (operation === 'submit') {
        if (!intent.current) {
          if (questions)
            payload.answers = structuredClone(
              Object.fromEntries(
                Object.entries(answers).filter(
                  ([, a]) =>
                    'rejected' in a ||
                    ('text' in a
                      ? a.text.trim()
                      : 'choiceId' in a
                        ? a.choiceId
                        : a.choiceIds.length),
                ),
              ),
            );
          else {
            if (answer.trim()) payload.answer = answer;
            if (choiceId) payload.choiceId = choiceId;
          }
        }
      } else if (!intent.current && reason.trim()) payload.answer = reason.trim();
      intent.current = { operation, payload };
      const result = await lockerRequest<{
        card: InteractionCard;
        dispatchError?: { code: string };
      }>(`/api/user-inbox/${card.id}/${operation}`, 'POST', payload);
      update(result.card);
      intent.current = null;
      if (result.dispatchError) setError(result.dispatchError.code);
    } catch (value) {
      if (
        value instanceof LockerRequestError &&
        value.status === 409 &&
        value.code === 'conflict'
      ) {
        try {
          const current = await lockerRequest<{ card: InteractionCard }>(
            `/api/user-inbox/${card.id}`,
          );
          update(current.card);
          if (current.card.status !== 'open') intent.current = null;
        } catch {
          /* Preserve the original action error if refresh is unavailable. */
        }
      }
      if (
        value instanceof LockerRequestError &&
        (value.status === 422 ||
          (value.status === 403 && value.code === 'forbidden') ||
          (value.status === 409 && ['capacity', 'stale', 'incomplete'].includes(value.code ?? '')))
      )
        intent.current = null;
      setError(String(value));
    } finally {
      setBusy(false);
    }
  };
  const attachments = (card.definition.attachments ?? [])
    .map((attachment) =>
      attachmentMarkdown({
        displayName: attachment.name,
        href: serverAttachmentDownloadHref(card.ownerSessionId, attachment.id),
      }),
    )
    .filter(Boolean)
    .join('\n\n');
  const responseChoice = card.definition.choices?.find(
    (choice) => choice.id === (open ? choiceId : card.response?.choiceId),
  );
  return (
    <article
      hidden={hidden}
      data-card-id={card.id}
      className="locker-card message-row message-row-assistant relative px-3 sm:px-6 lg:px-8"
    >
      <header className="locker-card-origin">
        <span className={`source-dot ${source?.available === false ? 'offline' : ''}`} />
        <span className="truncate">{source?.sourceName ?? card.ownerSessionId}</span>
        <code title={card.ownerSessionId}>{card.ownerSessionId}</code>
        <MessageTimestamp ts={card.createdAt} />
        <span className={`locker-status ${open ? 'open' : card.status}`}>
          {open
            ? 'Awaiting response'
            : noAnswer
              ? '无需回答 · No answer needed'
              : card.status === 'rejected'
                ? 'Rejected'
                : 'Submitted'}
        </span>
        <button
          type="button"
          className="locker-read-toggle"
          hidden={noAnswer}
          disabled={busy || noAnswer}
          aria-label={card.read ? 'Mark card as unread' : 'Mark card as read'}
          title={card.read ? 'Mark as unread' : 'Mark as read'}
          onClick={() => {
            void lockerRequest<{ card: InteractionCard }>(
              `/api/user-inbox/${card.id}/read`,
              'PUT',
              { read: !card.read },
            )
              .then((result) => update(result.card))
              .catch((value) => setError(String(value)));
          }}
        >
          {card.read ? <Circle size={12} /> : <Check size={13} />}
        </button>
      </header>
      <RelatedContextButton
        ownerSessionId={card.ownerSessionId}
        relatedMessageId={card.relatedMessageId}
        source={source}
        hidden={hidden}
      />
      <h2 className="locker-card-title">{card.definition.title}</h2>
      <MarkdownRenderer
        sourceSessionId={card.ownerSessionId}
        content={card.definition.body + (attachments ? `\n\n${attachments}` : '')}
        attachmentIds={(card.definition.attachments ?? []).map((attachment) => attachment.id)}
      />
      {open ? (
        <section className="locker-response" aria-label="Interaction response">
          {questions && (
            <CardQuestions
              card={card}
              answers={answers}
              errors={questionProblems}
              disabled={busy || !!intent.current}
              onChange={(id, value) => {
                setAnswers((current) => ({ ...current, [id]: value }));
                setQuestionProblems((current) => ({ ...current, [id]: '' }));
              }}
            />
          )}
          {!!card.definition.choices?.length && (
            <fieldset className="locker-choices">
              <legend>Choose an option</legend>
              {card.definition.choices.map((choice) => (
                <label key={choice.id} className={choiceId === choice.id ? 'selected' : ''}>
                  <input
                    aria-label={choice.label}
                    type="radio"
                    name={`choice-${card.id}`}
                    value={choice.id}
                    checked={choiceId === choice.id}
                    onChange={() => setChoiceId(choice.id)}
                    disabled={
                      busy ||
                      !!intent.current ||
                      (!!choice.triggerRef && !cardChoiceBinding(card, choice.id))
                    }
                  />
                  <span>
                    <strong>{choice.label}</strong>
                    {choice.triggerRef && (
                      <small>
                        {cardChoiceBinding(card, choice.id)?.summary ??
                          'Execution meaning is unavailable; this choice cannot run.'}
                      </small>
                    )}
                  </span>
                  {choiceId === choice.id && <Check size={14} />}
                </label>
              ))}
            </fieldset>
          )}
          {responseChoice?.triggerRef && (
            <TrustedChoice
              choice={responseChoice}
              binding={cardChoiceBinding(card, responseChoice.id)}
            />
          )}
          {!questions && (
            <label className="locker-answer-label" htmlFor={`answer-${card.id}`}>
              {card.definition.answerInput?.label ?? 'Answer or BTW'} <span>(optional)</span>
              <textarea
                id={`answer-${card.id}`}
                value={answer}
                maxLength={card.definition.answerInput?.maxLength ?? 32000}
                disabled={busy || !!intent.current}
                onChange={(event) => setAnswer(event.target.value)}
                placeholder="Add your answer or a note…"
                rows={3}
              />
            </label>
          )}
          <div className="locker-card-actions">
            <Button
              variant="primary"
              size="sm"
              disabled={busy || (!questions && !!card.definition.choices?.length && !choiceId)}
              onClick={() => void act('submit')}
            >
              Submit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => {
                setRejecting(true);
              }}
            >
              Reject
            </Button>
            <span>Replies go only to the source.</span>
          </div>
          {rejecting && (
            <div className="locker-reject-reason">
              <label className="locker-answer-label" htmlFor={`reason-${card.id}`}>
                Reason for rejection <span>(optional)</span>
                <textarea
                  aria-label="Reason for rejection"
                  id={`reason-${card.id}`}
                  autoFocus
                  value={reason}
                  disabled={busy || !!intent.current}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Tell the source why, if helpful…"
                  rows={2}
                />
              </label>
              <p>
                {questions
                  ? 'A rejection notification will be sent only to the source agent.'
                  : reason.trim()
                    ? 'Your reason will be sent only to the source agent.'
                    : 'Without a reason, no notification is sent.'}
              </p>
              <Button variant="danger" size="sm" disabled={busy} onClick={() => void act('reject')}>
                Confirm rejection
              </Button>
            </div>
          )}
        </section>
      ) : (
        <section className="locker-response completed" aria-label="Completed interaction">
          <div className="locker-completed-label">
            <CheckCheck size={14} />
            {noAnswer
              ? '无需回答 · No answer needed'
              : card.status === 'rejected'
                ? 'Rejected'
                : 'Submitted'}{' '}
            <span>Read-only</span>
          </div>
          {noAnswer && (
            <p>This question no longer requires an answer. Only deletion is available.</p>
          )}
          {questions && (
            <CardQuestions card={card} answers={card.response?.answers ?? {}} readOnly />
          )}
          {responseChoice && (
            <p className="locker-saved-choice">
              <Check size={13} />
              {responseChoice.label}
            </p>
          )}
          {card.response?.answer && (
            <div className="locker-saved-answer">
              <span>{card.status === 'rejected' ? 'Reason' : 'Answer or BTW'}</span>
              <p>{card.response.answer}</p>
            </div>
          )}
          {responseChoice?.triggerRef && (
            <TrustedChoice
              choice={responseChoice}
              binding={cardChoiceBinding(card, responseChoice.id)}
            />
          )}
          {card.status !== 'open' && (
            <div className="locker-delete-row">
              {busy ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => void act('delete')}
                >
                  Delete record
                </Button>
              )}
            </div>
          )}
        </section>
      )}
      {card.delivery.state !== 'not_requested' && (
        <details className="locker-delivery">
          <summary>Delivery details</summary>
          <span>Trigger delivery: {card.delivery.state}</span>
        </details>
      )}
      {card.rejectionDelivery && card.rejectionDelivery.state !== 'not_requested' && (
        <details className="locker-delivery">
          <summary>Rejection notification details</summary>
          <span>{card.rejectionDelivery?.state}</span>
        </details>
      )}
      {error && (
        <p role="alert" className="locker-error">
          {error}
        </p>
      )}
      <CardIdChip id={card.id} testId="locker-card-id" kind="interaction" />
    </article>
  );
}
export default function LockerView() {
  const sidebarPanel = useContext(SidebarPanelContext);
  const token = useAutomationAuth((state) => state.token);
  const navigate = useNavigate();
  const activeWorkspaceId = useUIStore((state) => state.activeWorkspaceId);
  const workspaces = useWorkspaceStore((state) => state.workspaces);
  const workspaceLoaded = useWorkspaceStore((state) => state.loaded);
  const [storedLockers, setLockers] = useState<Locker[]>([]);
  const [storedItems, setCards] = useState<LockerItem[]>([]);
  const [userCardRowStates, setUserCardRowStates] = useState<Record<string, UserCardRowState>>({});
  const [loadedToken, setLoadedToken] = useState<string | null>(null);
  const dataVisible = !!token && loadedToken === token;
  const lockers = dataVisible ? storedLockers : [];
  const cards = dataVisible ? storedItems : [];
  const [selected, setSelected] = useState('aggregate');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [order, setOrder] = useState<string[]>([]);
  const [dragging, setDragging] = useState<string | null>(null);
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const run = ++generation.current;
    setLoading(true);
    setError('');
    if (!token) {
      setCards([]);
      setLockers([]);
      setLoadedToken(null);
      setLoading(false);
      return;
    }
    try {
      const [sourceResult, firstPage, messages, userCards] = await Promise.all([
        lockerRequest<{ lockers: Locker[] }>(
          '/api/user-lockers',
          'GET',
          undefined,
          controller.signal,
        ),
        lockerRequest<{ cards: InteractionCard[]; nextAfter: string | null }>(
          '/api/user-inbox?limit=500',
          'GET',
          undefined,
          controller.signal,
        ),
        loadLockerMessages(controller.signal),
        lockerRequest<{ cards: UserCard[] }>(
          '/api/reminding-cards',
          'GET',
          undefined,
          controller.signal,
        ),
      ]);
      const allCards: LockerItem[] = [...firstPage.cards, ...messages, ...userCards.cards];
      let after = firstPage.nextAfter;
      const cursors = new Set<string>();
      while (after) {
        if (cursors.has(after))
          throw new Error('Locker pagination did not advance. Retry refresh.');
        cursors.add(after);
        const page = await lockerRequest<{ cards: InteractionCard[]; nextAfter: string | null }>(
          `/api/user-inbox?limit=500&after=${encodeURIComponent(after)}`,
          'GET',
          undefined,
          controller.signal,
        );
        allCards.push(...page.cards);
        after = page.nextAfter;
      }
      if (generation.current !== run || useAutomationAuth.getState().token !== token) return;
      setLockers(sourceResult.lockers.filter((locker) => locker.total > 0));
      setCards(allCards);
      setLoadedToken(token);
      setOrder((previous) => {
        const ids = sourceResult.lockers
          .filter((locker) => locker.total > 0)
          .map((locker) => locker.id);
        return [
          ...previous.filter((id) => ids.includes(id)),
          ...ids.filter((id) => !previous.includes(id)),
        ];
      });
    } catch (value) {
      if (!controller.signal.aborted && generation.current === run) {
        setError(String(value));
        if (/unauthorized|user_auth_unavailable/.test(String(value))) {
          setCards([]);
          setLockers([]);
        }
      }
    } finally {
      if (generation.current === run) setLoading(false);
    }
  }, [token]);
  useEffect(() => {
    void refresh();
    if (!workspaceLoaded) void useWorkspaceStore.getState().loadWorkspaces();
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    return () => {
      request.current?.abort();
      ++generation.current;
      window.removeEventListener('focus', onFocus);
    };
  }, [refresh, workspaceLoaded]);
  const update = useCallback((card: LockerItem) => {
    request.current?.abort();
    ++generation.current;
    setLoading(false);
    setCards((previous) =>
      previous.map((item) => (item.id === card.id && card.revision >= item.revision ? card : item)),
    );
  }, []);
  const remove = useCallback((id: string) => {
    request.current?.abort();
    ++generation.current;
    setLoading(false);
    setCards((previous) => {
      const removed = new Set([id]);
      const children = new Map<string, string[]>();
      for (const item of previous)
        if (isUserCard(item) && item.parent) {
          const siblings = children.get(item.parent.id) ?? [];
          siblings.push(item.id);
          children.set(item.parent.id, siblings);
        }
      const stack = [id];
      while (stack.length)
        for (const child of children.get(stack.pop()!) ?? []) {
          if (!removed.has(child)) {
            removed.add(child);
            stack.push(child);
          }
        }
      return previous.filter((card) => !removed.has(card.id));
    });
  }, []);
  const workspaceCards = useMemo(
    () => lockerProjection(lockers, cards, activeWorkspaceId, selected),
    [lockers, cards, activeWorkspaceId, selected],
  );
  const scopeLockers = useMemo(() => {
    const scope = lockers
      .filter((locker) => cards.some((card) => card.lockerId === locker.id))
      .filter((locker) =>
        activeWorkspaceId === ALL_WORKSPACES
          ? true
          : activeWorkspaceId === UNGROUPED_WORKSPACES
            ? locker.workspaceIds.length === 0
            : locker.workspaceIds.includes(activeWorkspaceId),
      );
    return scope.sort(
      (a, b) =>
        order.indexOf(a.id) - order.indexOf(b.id) || a.sourceName.localeCompare(b.sourceName),
    );
  }, [lockers, cards, activeWorkspaceId, order]);
  // Deep link from a Session row: /locker?session=<sessionId> opens that
  // Session's Locker. Applied once the authenticated data is actually loaded
  // (the query is consumed afterwards so a second click for the same Session
  // still jumps back). If the Locker exists but the current workspace scope
  // hides it, widen the scope instead of silently falling back to aggregate.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedSession = searchParams.get('session') ?? '';
  useEffect(() => {
    if (!requestedSession || !dataVisible) return;
    const target = lockers.find((locker) => locker.sessionId === requestedSession);
    if (!target) return;
    if (!scopeLockers.some((locker) => locker.id === target.id))
      useUIStore.getState().setActiveWorkspace(ALL_WORKSPACES);
    setSelected(target.id);
    setSearchParams({}, { replace: true });
  }, [requestedSession, dataVisible, lockers, scopeLockers, setSearchParams]);
  useEffect(() => {
    if (selected !== 'aggregate' && !scopeLockers.some((locker) => locker.id === selected))
      setSelected('aggregate');
  }, [scopeLockers, selected]);
  const visible = workspaceCards
    .filter((card) => !unreadOnly || !card.read)
    .filter((card) => {
      if (!query) return true;
      const source = lockers.find((locker) => locker.id === card.lockerId);
      return `${source?.sourceName ?? ''} ${source?.sessionId ?? card.ownerSessionId} ${lockerItemPreview(card)}`
        .toLowerCase()
        .includes(query.toLowerCase());
    });
  // History-style tail following and prepend anchoring, in chronological DOM order.
  const timeline = useRef<HTMLElement>(null);
  const follow = useRef(true);
  const scope = `${token}:${activeWorkspaceId}:${selected}:${query}:${unreadOnly}`;
  const previousScope = useRef('');
  const [windowSize, setWindowSize] = useState(50);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  const [nearBottom, setNearBottom] = useState(true);
  const [entryNotice, setEntryNotice] = useState('');
  const entryScope = useRef('');
  const visibleIds = new Set(visible.map((item) => item.id));
  const arranged = lockerTimeline(workspaceCards);
  const relevantRoots = new Set(
    arranged.filter((entry) => visibleIds.has(entry.item.id)).map((entry) => entry.rootId),
  );
  const ordered = arranged.filter(
    (entry) => entry.item.id === entry.rootId && relevantRoots.has(entry.rootId),
  );
  const entryWindow = lockerEntryWindow(
    ordered.filter((entry) => !entry.parent).map((entry) => entry.item),
  );
  const entering = entryScope.current !== scope;
  const effectiveWindowSize = entering ? entryWindow.size : windowSize;
  const firstVisible = useRef<string | undefined>(undefined);
  const oldStart = ordered.findIndex((entry) => entry.key === firstVisible.current);
  const start =
    !follow.current && previousScope.current === scope && oldStart >= 0
      ? Math.min(oldStart, Math.max(0, ordered.length - effectiveWindowSize))
      : Math.max(0, ordered.length - effectiveWindowSize);
  const windowItems = ordered.slice(start);
  firstVisible.current = windowItems[0]?.key;
  const rootIds = new Set(windowItems.map((entry) => entry.item.id));
  const windowIds = new Set(
    arranged
      .filter(
        (entry) =>
          rootIds.has(entry.rootId) &&
          (visibleIds.has(entry.item.id) || relevantRoots.has(entry.item.id)),
      )
      .map((entry) => entry.key),
  );
  useLayoutEffect(() => {
    const node = timeline.current;
    if (!node) return;
    if (previousScope.current !== scope) {
      previousScope.current = scope;
      prepend.current = null;
    }
    if (entering && dataVisible && !loading) {
      entryScope.current = scope;
      setWindowSize(entryWindow.size);
      setEntryNotice(
        entryWindow.limited
          ? '已向前加载 5 页，仍未找到第一条未读。当前停在最早已加载项，可继续加载更早消息。'
          : '',
      );
      const target = entryWindow.limited ? windowItems[0]?.item.id : entryWindow.firstUnreadId;
      follow.current = !target;
      if (target) {
        const row = Array.from(node.querySelectorAll<HTMLElement>('[data-locker-entry]')).find(
          (element) => element.dataset.lockerEntry === target,
        );
        if (row)
          node.scrollTop =
            row.getBoundingClientRect().top - node.getBoundingClientRect().top + node.scrollTop;
      } else node.scrollTop = node.scrollHeight;
      setNearBottom(node.scrollHeight - node.scrollTop - node.clientHeight <= 48);
      return;
    }
    if (prepend.current) {
      node.scrollTop = prepend.current.top + node.scrollHeight - prepend.current.height;
      prepend.current = null;
    } else if (follow.current) node.scrollTop = node.scrollHeight;
    setNearBottom(node.scrollHeight - node.scrollTop - node.clientHeight <= 48);
  });
  useEffect(() => {
    const node = timeline.current;
    if (!node || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (follow.current) node.scrollTop = node.scrollHeight;
      setNearBottom(node.scrollHeight - node.scrollTop - node.clientHeight <= 48);
    });
    for (const child of node.children) observer.observe(child);
    return () => observer.disconnect();
  }, [cards, scope, windowSize]);
  const workspaceName =
    activeWorkspaceId === ALL_WORKSPACES
      ? 'All Workspaces'
      : activeWorkspaceId === UNGROUPED_WORKSPACES
        ? 'No Workspace'
        : (workspaces.find((workspace) => workspace.id === activeWorkspaceId)?.name ?? 'Workspace');
  const markSourceRead = async (locker: Locker) => {
    const unread = sourceCards(cards, locker.id).filter(
      (card) =>
        !card.read &&
        (isUserCard(card)
          ? card.status === 'answered'
          : 'message' in card || card.status !== 'no_answer_needed'),
    );
    await Promise.all(
      unread.map((card) =>
        lockerRequest<{ card?: InteractionCard | UserCard; message?: LockerMessage }>(
          `/api/${isUserCard(card) ? 'reminding-cards' : 'message' in card ? 'user-locker-messages' : 'user-inbox'}/${card.id}/read`,
          'PUT',
          { read: true },
        )
          .then((result) => {
            const item = result.card ?? result.message;
            if (item) update(item);
          })
          .catch((value) => setError(String(value))),
      ),
    );
  };
  const navigateToChat = async (locker: Locker) => {
    if (!locker.available) return;
    try {
      if (!useSessionStore.getState().sessions.some((session) => session.id === locker.sessionId))
        await useSessionStore.getState().loadSessions({ throwOnError: true });
      if (!useSessionStore.getState().sessions.some((session) => session.id === locker.sessionId))
        throw new Error('Source session is unavailable. Refresh Locker to update its status.');
      await useSessionStore.getState().selectSession(locker.sessionId);
      navigate('/');
    } catch (value) {
      setError(String(value));
    }
  };
  const reorder = (sourceId: string, targetId: string) => {
    if (sourceId === targetId) return;
    setOrder((previous) => {
      const next = previous.filter((id) => id !== sourceId);
      const index = next.indexOf(targetId);
      next.splice(index < 0 ? next.length : index, 0, sourceId);
      return next;
    });
    setDragging(null);
  };
  const sourceList = (
    <nav
      aria-label="Locker sources"
      className={`locker-source-list ${sourcesOpen ? 'is-open' : ''} ${sidebarPanel ? 'in-sidebar' : ''}`}
    >
      <div className="locker-source-tools">
        <div className="locker-source-search">
          <Search size={12} />
          <input
            aria-label="Search Locker sources"
            placeholder="Filter sources…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>
      <div
        className={`locker-source-item aggregate ${selected === 'aggregate' ? 'active' : ''}`}
        role="button"
        tabIndex={0}
        aria-current={selected === 'aggregate' ? 'true' : undefined}
        onClick={() => {
          setSelected('aggregate');
          setSourcesOpen(false);
          useUIStore.getState().setMobileSidebarOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') setSelected('aggregate');
        }}
      >
        <Archive size={14} className="text-accent shrink-0" />
        <span className="locker-source-copy">
          <strong>All Lockers</strong>
          <span>
            {scopeLockers.reduce((sum, locker) => sum + sourceCards(cards, locker.id).length, 0)}{' '}
            messages
          </span>
          <small>Aggregate view</small>
        </span>
        <div className="session-quick-action-rail">
          <div
            className="session-quick-action-group"
            role="group"
            aria-label="Aggregate Locker actions"
          >
            <button
              type="button"
              className="session-quick-action-button"
              aria-label="Read all"
              title="Read all"
              onClick={(event) => {
                event.stopPropagation();
                void Promise.all(scopeLockers.map(markSourceRead));
              }}
            >
              <CheckCheck size={12} />
            </button>
          </div>
        </div>
      </div>
      {scopeLockers.map((locker) => {
        const sourceItems = sourceCards(cards, locker.id);
        const unread = sourceItems.filter((card) => !card.read).length;
        return (
          <div
            key={locker.id}
            data-testid={`locker-source-${locker.id}`}
            className={`locker-source-item ${selected === locker.id ? 'active' : ''}`}
            role="button"
            tabIndex={0}
            aria-current={selected === locker.id ? 'true' : undefined}
            onClick={() => {
              setSelected(locker.id);
              setSourcesOpen(false);
              useUIStore.getState().setMobileSidebarOpen(false);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') setSelected(locker.id);
            }}
            onDragOver={(event) => {
              if (dragging && dragging !== locker.id) event.preventDefault();
            }}
            onDrop={(event) => {
              event.preventDefault();
              if (dragging) reorder(dragging, locker.id);
            }}
          >
            <SessionDragGutter
              draggable
              data-testid={`locker-drag-${locker.id}`}
              title="Drag to reorder Locker"
              onClick={(event) => event.stopPropagation()}
              onDragStart={(event) => {
                event.stopPropagation();
                setDragging(locker.id);
                event.dataTransfer.effectAllowed = 'move';
              }}
              onDragEnd={() => setDragging(null)}
            >
              <span className={`source-dot ${locker.available ? '' : 'offline'}`} />
            </SessionDragGutter>
            <span className="locker-source-copy">
              <strong>{locker.sourceName}</strong>
              <span>{lockerItemPreview([...sourceItems].sort(compareLockerItems).at(-1))}</span>
              <small>
                {locker.sessionId}
                {!locker.available && (
                  <em>
                    <WifiOff size={10} />
                    Source unavailable
                  </em>
                )}
              </small>
            </span>
            {unread > 0 && <span className="unread-count">{unread}</span>}
            <div className="session-quick-action-rail">
              <div
                className="session-quick-action-group"
                role="group"
                aria-label={`${locker.sourceName} actions`}
              >
                <button
                  type="button"
                  className="session-quick-action-button"
                  aria-label="Navigate to chat"
                  title={locker.available ? 'Navigate to chat' : 'Source unavailable'}
                  disabled={!locker.available}
                  onClick={(event) => {
                    event.stopPropagation();
                    void navigateToChat(locker);
                  }}
                >
                  <ExternalLink size={12} />
                </button>
                <button
                  type="button"
                  className="session-quick-action-button"
                  aria-label="Read all"
                  title="Read all"
                  onClick={(event) => {
                    event.stopPropagation();
                    void markSourceRead(locker);
                  }}
                >
                  <CheckCheck size={12} />
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </nav>
  );
  return (
    <main aria-label="Locker" className="locker-view flex h-full min-h-0 min-w-0 flex-col">
      <header className="locker-topbar">
        <Archive size={16} className="text-accent" />
        <div className="locker-topbar-title">
          <strong>Locker</strong>
          <span>{workspaceName}</span>
        </div>
        <div className="locker-topbar-actions">
          {!sidebarPanel && (
            <button
              className="locker-sources-toggle"
              type="button"
              aria-label="Toggle Locker sources"
              aria-expanded={sourcesOpen}
              onClick={() => setSourcesOpen((value) => !value)}
            >
              <PanelLeft size={15} />
            </button>
          )}
          <button
            type="button"
            aria-label="Refresh Locker"
            title="Refresh Locker"
            disabled={loading}
            onClick={() => void refresh()}
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          </button>
          <button
            type="button"
            aria-label={unreadOnly ? 'Show all Locker messages' : 'Show unread Locker messages'}
            title={unreadOnly ? 'Show all' : 'Unread only'}
            className={unreadOnly ? 'active' : ''}
            onClick={() => setUnreadOnly((previous) => !previous)}
          >
            <Circle size={14} />
          </button>
        </div>
      </header>
      <details className="locker-auth" open={!token}>
        <summary>User authorization</summary>
        <JobUserAuthorization purpose="locker" />
      </details>
      {error && (
        <div role="alert" className="locker-view-error">
          {error}
        </div>
      )}
      <div className="locker-scope-bar">
        <span>
          <Folder size={12} />
          {workspaceName}
        </span>
        <span>
          {visible.length} messages{unreadOnly ? ' · Unread only' : ''}
        </span>
      </div>
      <div className="locker-workspace-layout relative">
        {sidebarPanel ? sidebarPanel[0] && createPortal(sourceList, sidebarPanel[0]) : sourceList}
        {entryNotice && (
          <p role="status" className="locker-entry-notice">
            {entryNotice}
          </p>
        )}
        <section
          ref={timeline}
          aria-label="Locker messages"
          className="locker-timeline"
          onScroll={() => {
            const node = timeline.current;
            if (!node) return;
            follow.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 48;
            setNearBottom(follow.current);
            if (
              node.scrollTop <= 48 &&
              start > 0 &&
              !prepend.current &&
              !entryNotice &&
              !entering
            ) {
              prepend.current = { height: node.scrollHeight, top: node.scrollTop };
              setWindowSize(windowItems.length + 50);
            }
          }}
        >
          {start > 0 && (
            <button
              type="button"
              onClick={() => {
                const node = timeline.current;
                if (node) prepend.current = { height: node.scrollHeight, top: node.scrollTop };
                follow.current = false;
                setWindowSize(windowItems.length + 50);
              }}
            >
              Load older messages
            </button>
          )}
          {loading && (
            <div role="status" className="locker-state">
              <Loader2 size={18} className="animate-spin" />
              Loading Locker…
            </div>
          )}
          {!loading && !visible.length && (
            <div className="locker-state">
              <Archive size={24} />
              <strong>{unreadOnly ? 'No unread messages' : 'No Locker messages'}</strong>
              <span>Messages from this Workspace appear here after a source sends them.</span>
            </div>
          )}
          {lockerTimeline(cards).map(({ item: card, key: positionKey, parent }) => (
            <div
              key={`${token}:${positionKey}`}
              data-locker-entry={card.id}
              data-parent-id={parent?.id}
              hidden={!windowIds.has(positionKey)}
              className={parent ? 'locker-attached-entry' : 'locker-root-entry'}
            >
              {card.id === entryWindow.firstUnreadId && !parent && (
                <div role="separator" className="locker-unread-divider">
                  以下为未读 · Unread messages
                </div>
              )}
              {parent && (
                <div
                  className="locker-parent-chain"
                  aria-label={`Attached to ${parent.type} ${parent.id}`}
                >
                  <Link2 size={14} />
                  <span>{parent.id}</span>
                </div>
              )}
              {isUserCard(card) ? (
                <UserCardRow
                  key={`${token}:${positionKey}`}
                  card={card}
                  hidden={!windowIds.has(positionKey)}
                  source={lockers.find((locker) => locker.id === card.lockerId)}
                  update={update}
                  remove={remove}
                  state={userCardRowStates[card.id] ?? { busy: false, error: '' }}
                  setState={(patch) =>
                    setUserCardRowStates((previous) => ({
                      ...previous,
                      [card.id]: { ...(previous[card.id] ?? { busy: false, error: '' }), ...patch },
                    }))
                  }
                />
              ) : 'message' in card ? (
                <LockerMessageRow
                  key={`${token}:${positionKey}`}
                  item={card}
                  hidden={!windowIds.has(positionKey)}
                  source={lockers.find((locker) => locker.id === card.lockerId)}
                  update={update}
                  remove={remove}
                />
              ) : (
                <Card
                  key={`${token}:${positionKey}`}
                  hidden={!windowIds.has(positionKey)}
                  card={card}
                  source={lockers.find((locker) => locker.id === card.lockerId)}
                  update={update}
                  remove={remove}
                />
              )}
              <AttachedReminder
                item={card}
                created={(created) =>
                  setCards((previous) =>
                    previous.some((item) => item.id === created.id)
                      ? previous
                      : [...previous, created],
                  )
                }
              />
            </div>
          ))}
        </section>
        {!nearBottom && (
          <button
            type="button"
            title="Scroll to bottom"
            aria-label="Scroll to bottom"
            className="absolute bottom-2 right-4 rounded-full bg-accent text-white p-2 shadow-lg hover:bg-accent-hover transition-colors z-10"
            onClick={() => {
              const node = timeline.current;
              if (node) node.scrollTo({ top: node.scrollHeight, behavior: 'smooth' });
            }}
          >
            <ArrowDown size={16} />
          </button>
        )}
      </div>
    </main>
  );
}

import './lockerView.css';
