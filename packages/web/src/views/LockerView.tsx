import { useCallback, useEffect, useRef, useState } from 'react';
import { MarkdownRenderer } from '@/components/chat/MarkdownRenderer';
import { MessageTimestamp } from '@/components/chat/MessageTimestamp';
import { attachmentMarkdown, serverAttachmentDownloadHref } from '@/utils/attachmentMarkdown';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { lockerProjection, lockerRequest, type InteractionCard, type Locker } from './lockerModel';

function Card({ card, source, update, remove }: {
  card: InteractionCard; source?: Locker;
  update: (card: InteractionCard) => void; remove: (id: string) => void;
}) {
  const [answer, setAnswer] = useState(card.response?.answer ?? '');
  const [choiceId, setChoiceId] = useState(card.response?.choiceId ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Preserve the same identity AND exact payload after an uncertain HTTP result.
  const intent = useRef<{ operation: string; payload: Record<string, unknown> } | null>(null);
  const element = useRef<HTMLElement>(null);
  const acknowledged = useRef(false);
  const [unreadOverride, setUnreadOverride] = useState(false);
  const open = card.status === 'open';
  useEffect(() => {
    if (card.read || unreadOverride || acknowledged.current) return;
    const mark = () => {
      if (acknowledged.current) return;
      acknowledged.current = true;
      void lockerRequest<{ card: InteractionCard }>(`/api/user-inbox/${card.id}/read`, 'PUT', { read: true })
        .then(r => update(r.card)).catch(e => { acknowledged.current = false; setError(String(e)); });
    };
    const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) mark(); });
    if (element.current) observer.observe(element.current);
    return () => observer.disconnect();
  }, [card.id, card.read, update, unreadOverride]);
  async function act(operation: 'submit' | 'reject' | 'delete') {
    if (busy) return;
    setBusy(true); setError('');
    try {
      if (operation === 'delete') {
        await lockerRequest(`/api/user-inbox/${card.id}`, 'DELETE'); remove(card.id);
      } else {
        if (intent.current && intent.current.operation !== operation) throw new Error('请先刷新核对上次操作结果');
        const payload = intent.current?.payload ?? {
          requestId: crypto.randomUUID(), expectedRevision: card.revision,
          ...(operation === 'submit' && card.definition.answerInput ? { answer } : {}),
          ...(operation === 'submit' && choiceId ? { choiceId } : {}),
        };
        intent.current = { operation, payload };
        const result = await lockerRequest<{ card: InteractionCard; dispatchError?: { code: string } }>(
          `/api/user-inbox/${card.id}/${operation}`, 'POST', payload);
        update(result.card);
        intent.current = null;
        if (result.dispatchError) setError(result.dispatchError.code);
      }
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const attachments = (card.definition.attachments ?? []).map(a => attachmentMarkdown({
    displayName: a.name, href: serverAttachmentDownloadHref(card.ownerSessionId, a.id),
  })).filter(Boolean).join('\n\n');
  return <article ref={element} data-card-id={card.id} className="rounded border border-border-default p-4 space-y-3">
    <header className="flex flex-wrap gap-2 text-xs text-text-tertiary">
      <span>{source?.sourceName ?? card.ownerSessionId}</span><code className="break-all">{card.ownerSessionId}</code>
      <MessageTimestamp ts={card.createdAt} />
      <span>{card.read ? '已读' : '未读'}</span><span>{card.status === 'rejected' ? '已拒收' : open ? '待回应' : '已提交'}</span>
      <button disabled={busy} onClick={() => {
        setUnreadOverride(true);
        void lockerRequest<{ card: InteractionCard }>(`/api/user-inbox/${card.id}/read`, 'PUT', { read: !card.read })
          .then(r => update(r.card)).catch(e => setError(String(e)));
      }}>{card.read ? '标为未读' : '标为已读'}</button>
    </header>
    <h2 className="font-medium">{card.definition.title}</h2>
    <MarkdownRenderer sourceSessionId={card.ownerSessionId} content={card.definition.body + (attachments ? '\n\n' + attachments : '')}
      attachmentIds={(card.definition.attachments ?? []).map(a => a.id)} />
    {card.definition.answerInput && <label className="block">{card.definition.answerInput.label ?? 'Answer'}
      <textarea aria-label={card.definition.answerInput.label ?? 'Answer'} value={open ? answer : card.response?.answer ?? ''}
        disabled={!open || busy || !!intent.current} maxLength={card.definition.answerInput.maxLength ?? 32000}
        className="block w-full border border-border-default rounded bg-bg-primary p-2"
        onChange={e => setAnswer(e.target.value)} /></label>}
    {!!card.definition.choices?.length && <label className="block">Choice
      <select aria-label="Choice" value={open ? choiceId : card.response?.choiceId ?? ''} disabled={!open || busy || !!intent.current}
        className="ml-2 bg-bg-primary border border-border-default rounded p-2" onChange={e => setChoiceId(e.target.value)}>
        <option value="">选择…</option>
        {card.definition.choices.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select></label>}
    <div className="flex gap-3">
      {open ? <><button disabled={busy} onClick={() => void act('submit')}>Submit</button>
        <button disabled={busy} onClick={() => void act('reject')}>Reject</button></>
        : <button disabled={busy} onClick={() => void act('delete')}>Delete</button>}
    </div>
    {card.delivery.state !== 'not_requested' && <p className="text-xs">Trigger: {card.delivery.state}</p>}
    {card.rejectionDelivery && card.rejectionDelivery.state !== 'not_requested' && <p className="text-xs">Rejection notification: {card.rejectionDelivery.state}</p>}
    {error && <p role="alert">{error}</p>}
  </article>;
}

export default function LockerView() {
  const [lockers, setLockers] = useState<Locker[]>([]);
  const [cards, setCards] = useState<InteractionCard[]>([]);
  const [selected, setSelected] = useState('aggregate');
  const [workspace, setWorkspace] = useState('');
  const [count, setCount] = useState(20);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const workspaces = useWorkspaceStore(s => s.workspaces);
  const refresh = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    const run = ++generation.current;
    setLoading(true); setError('');
    try {
      const sources = await lockerRequest<{ lockers: Locker[] }>('/api/user-lockers', 'GET', undefined, controller.signal);
      const result: InteractionCard[] = [];
      let after: string | null = null;
      do {
        const page: { cards: InteractionCard[]; nextAfter: string | null } = await lockerRequest(
          `/api/user-inbox?limit=100${after ? `&after=${encodeURIComponent(after)}` : ''}`, 'GET', undefined, controller.signal);
        result.push(...page.cards); after = page.nextAfter;
      } while (after);
      if (generation.current === run) { setLockers(sources.lockers); setCards(result); }
    } catch (e) {
      if (!controller.signal.aborted && generation.current === run) {
        setError(String(e));
        if (/unauthorized|user_auth_unavailable/.test(String(e))) { setCards([]); setLockers([]); }
      }
    }
    finally { if (generation.current === run) setLoading(false); }
  }, []);
  useEffect(() => {
    void refresh(); void useWorkspaceStore.getState().loadWorkspaces();
    const focus = () => void refresh(); window.addEventListener('focus', focus);
    return () => { request.current?.abort(); ++generation.current; window.removeEventListener('focus', focus); };
  }, [refresh]);
  const update = useCallback((card: InteractionCard) => {
    request.current?.abort(); ++generation.current; setLoading(false);
    setCards(old => old.map(c => c.id === card.id && card.revision >= c.revision ? card : c));
  }, []);
  const visible = lockerProjection(lockers, cards, workspace, selected);
  return <main aria-label="Locker" className="flex h-full min-h-0 min-w-0 flex-col p-4 pt-12 sm:pt-4 gap-3">
    <header className="flex flex-wrap items-center gap-3"><h1 className="text-lg font-semibold">Locker</h1>
      <label>Workspace <select aria-label="Workspace" className="bg-bg-primary" value={workspace} onChange={e => {
        setWorkspace(e.target.value); setSelected('aggregate'); setCount(20);
      }}><option value="">全部</option>{workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
      <button disabled={loading} onClick={() => void refresh()}>刷新</button>
    </header>
    {error && <p role="alert">{error}</p>}{loading && <p role="status">加载中…</p>}
    <div className="flex flex-1 min-h-0 gap-4">
      <nav aria-label="Locker 列表" className="w-28 sm:w-44 text-xs sm:text-sm shrink-0 overflow-y-auto space-y-2">
        <button aria-pressed={selected === 'aggregate'} onClick={() => { setSelected('aggregate'); setCount(20); }}>聚合 Locker</button>
        {lockers.filter(l => !workspace || l.workspaceIds.includes(workspace)).map(l => <button key={l.id}
          aria-pressed={selected === l.id} className="block w-full text-left break-all rounded border border-border-default p-2"
          onClick={() => { setSelected(l.id); setCount(20); }}>
          <span className="block">{l.sourceName}</span><small>{l.sessionId}</small>
          <span className="block text-xs">{cards.filter(c => c.lockerId === l.id && !c.read).length} 未读{l.available ? '' : ' · 来源不可用'}</span>
        </button>)}
      </nav>
      <section aria-label="Locker Message" className="min-w-0 flex-1 overflow-y-auto space-y-4">
        {!loading && !visible.length && <p>暂无 Locker Message</p>}
        {visible.slice(0, count).map(c => <Card key={c.id} card={c} source={lockers.find(l => l.id === c.lockerId)}
          update={update} remove={id => { request.current?.abort(); ++generation.current; setLoading(false); setCards(old => old.filter(item => item.id !== id)); }} />)}
        {visible.length > count && <button onClick={() => setCount(n => n + 20)}>加载更多</button>}
      </section>
    </div>
  </main>;
}
