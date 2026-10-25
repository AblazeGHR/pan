import { Check, Circle, Trash2, ArrowUpRight } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { MarkdownRenderer } from '@/components/chat/MarkdownRenderer';
import { MessageTimestamp } from '@/components/chat/MessageTimestamp';
import { useAutomationAuth } from '@/stores/automationStore';
import { attachmentMarkdown, serverAttachmentDownloadHref } from '@/utils/attachmentMarkdown';
import { CardIdChip } from './CardIdChip';
import { lockerRequest, type UserCard, type Locker } from './lockerModel';

export interface UserCardRowState {
  busy: boolean;
  error: string;
}
export function UserCardRow({
  card,
  source,
  hidden,
  update,
  remove,
  state,
  setState,
}: {
  card: UserCard;
  source?: Locker;
  hidden: boolean;
  update: (card: UserCard) => void;
  remove: (id: string) => void;
  state: UserCardRowState;
  setState: (patch: Partial<UserCardRowState>) => void;
}) {
  const { busy, error } = state;
  const setBusy = (value: boolean) => setState({ busy: value });
  const setError = (value: string) => setState({ error: value });
  const answered = card.status === 'answered';
  const act = async (operation: 'read' | 'delete') => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      if (operation === 'delete') {
        await lockerRequest(`/api/reminding-cards/${card.id}`, 'DELETE');
        remove(card.id);
      } else {
        const result = await lockerRequest<{ card: UserCard }>(
          `/api/reminding-cards/${card.id}/read`,
          'PUT',
          { read: !card.read },
        );
        update(result.card);
      }
    } catch (value) {
      setError(String(value));
    } finally {
      setBusy(false);
    }
  };
  const download = async (href: string) => {
    try {
      const url = new URL(href, window.location.origin);
      const id = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
      const attachment = card.attachments.find((a) => a.id === id);
      if (!attachment || url.searchParams.get('session_id') !== card.ownerSessionId)
        throw new Error('Attachment is not part of this reminding card.');
      const auth = useAutomationAuth.getState();
      const token = auth.token;
      const response = await fetch(
        `/api/reminding-cards/${card.id}/attachments/${encodeURIComponent(id)}`,
        {
          credentials: 'same-origin',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (response.status === 401 && useAutomationAuth.getState() === auth)
        useAutomationAuth.getState().clear();
      if (!response.ok) throw new Error(`Attachment download failed (HTTP ${response.status}).`);
      const blob = await response.blob();
      if (!(token || auth.authenticated) || useAutomationAuth.getState() !== auth) return;
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = attachment.name;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (value) {
      setError(String(value));
    }
  };
  const attachments = card.attachments
    .map((a) =>
      attachmentMarkdown({
        displayName: a.name,
        href: serverAttachmentDownloadHref(card.ownerSessionId, a.id),
      }),
    )
    .filter(Boolean)
    .join('\n\n');
  return (
    <article
      hidden={hidden}
      data-reminding-card-id={card.id}
      className="locker-card reminding-card-row message-row message-row-user relative px-3 sm:px-6 lg:px-8"
    >
      <header className="locker-card-origin">
        <span className="source-dot" /><span className="truncate">{source?.sourceName ?? card.ownerSessionId}</span>
        <code>{card.ownerSessionId}</code>
        <span>
          Created <MessageTimestamp ts={card.createdAt} />
        </span>
        {card.answeredAt && (
          <span>
            Answered <MessageTimestamp ts={card.answeredAt} />
          </span>
        )}
        <span className="locker-status reminding-card-direction"><ArrowUpRight size={11} />
          {card.kind === 'question' ? 'Reminding · Question' : 'Reminding · Task'}
        </span>
        <span className={`locker-status ${answered ? 'responded' : ''}`}>
          {answered ? 'Answered' : 'Awaiting answer'}
        </span>
        {answered && (
          <button
            className="locker-read-toggle"
            title={card.read ? 'Read — mark as unread' : 'Unread — mark as read'}
            type="button"
            disabled={busy}
            onClick={() => void act('read')}
            aria-label={card.read ? 'Mark reminding card as unread' : 'Mark reminding card as read'}
          >
            {card.read ? <Circle size={12} /> : <Check size={13} />}
          </button>
        )}
      </header>
      <MarkdownRenderer
        sourceSessionId={card.ownerSessionId}
        content={card.body + (attachments ? `\n\n${attachments}` : '')}
        attachmentIds={card.attachments.map((a) => a.id)}
        onAttachmentDownload={download}
      />
      {answered && (
        <section aria-label="Agent answer" className="locker-response completed">
          <span className="reminding-card-answer-label">Agent answer</span>
          <MarkdownRenderer
            sourceSessionId={card.ownerSessionId}
            content={card.answer || '(Empty task answer)'}
          />
        </section>
      )}
      <div className="locker-delete-row">
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act('delete')}>
          <Trash2 size={12} /> Delete reminding card
        </Button>
      </div>
      {error && (
        <p role="alert" className="locker-error">
          {error}
        </p>
      )}
      <CardIdChip id={card.id} testId="reminding-card-id" kind="reminding" />
    </article>
  );
}
