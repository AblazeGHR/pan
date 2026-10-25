import { RelatedContextButton } from './RelatedContextButton';
import { useState } from 'react';
import { Check, Circle, Trash2 } from 'lucide-react';
import { MarkdownRenderer } from '@/components/chat/MarkdownRenderer';
import { MessageTimestamp } from '@/components/chat/MessageTimestamp';
import { Button } from '@/components/ui/Button';
import { attachmentMarkdown, serverAttachmentDownloadHref } from '@/utils/attachmentMarkdown';
import { useAutomationAuth } from '@/stores/automationStore';
import { lockerRequest, type Locker, type LockerMessage } from './lockerModel';

export function lockerMessageMarkdown(item: LockerMessage) {
  const parts = item.message.parts;
  if (!parts?.some((part) => part.type === 'text' || part.type === 'attachment'))
    return item.message.content;
  return parts
    .map((part) =>
      part.type === 'text'
        ? part.text
        : part.type === 'attachment'
          ? `\n\n${
              attachmentMarkdown({
                displayName: part.displayName,
                href: serverAttachmentDownloadHref(item.ownerSessionId, part.attachmentId),
                location: part.location,
              }) ?? ''
            }\n\n`
          : '',
    )
    .join('');
}

export function LockerMessageRow({
  item,
  source,
  hidden,
  update,
  remove,
}: {
  item: LockerMessage;
  source?: Locker;
  hidden: boolean;
  update: (item: LockerMessage) => void;
  remove: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const act = async (operation: 'read' | 'delete') => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      if (operation === 'delete') {
        await lockerRequest(`/api/user-locker-messages/${item.id}`, 'DELETE');
        remove(item.id);
      } else {
        const result = await lockerRequest<{ message: LockerMessage }>(
          `/api/user-locker-messages/${item.id}/read`,
          'PUT',
          { read: !item.read },
        );
        update(result.message);
      }
    } catch (value) {
      setError(String(value));
    } finally {
      setBusy(false);
    }
  };
  const download = async (href: string) => {
    setError('');
    try {
      const url = new URL(href, window.location.origin);
      const attachmentId = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
      const part = item.message.parts?.find(
        (part) => part.type === 'attachment' && part.attachmentId === attachmentId,
      );
      if (
        !part ||
        part.type !== 'attachment' ||
        url.searchParams.get('session_id') !== item.ownerSessionId
      )
        throw new Error('Attachment is not part of this Locker message.');
      const auth = useAutomationAuth.getState();
      const token = auth.token;
      const response = await fetch(
        `/api/user-locker-messages/${item.id}/attachments/${encodeURIComponent(attachmentId)}`,
        { credentials: 'same-origin', headers: token ? { Authorization: `Bearer ${token}` } : {} },
      );
      if (response.status === 401 && useAutomationAuth.getState() === auth)
        useAutomationAuth.getState().clear();
      if (!response.ok) throw new Error(`Attachment download failed (HTTP ${response.status}).`);
      const blob = await response.blob();
      if (!(token || auth.authenticated) || useAutomationAuth.getState() !== auth) return;
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = part.displayName;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (value) {
      setError(String(value));
    }
  };
  return (
    <article
      hidden={hidden}
      data-locker-message-id={item.id}
      className={`locker-card message-row message-row-${item.message.role === 'user' ? 'user' : 'assistant'} relative px-3 sm:px-6 lg:px-8`}
    >
      <header className="locker-card-origin">
        <span className={`source-dot ${source?.available === false ? 'offline' : ''}`} />
        <span className="truncate">{source?.sourceName ?? item.ownerSessionId}</span>
        <code title={item.ownerSessionId}>{item.ownerSessionId}</code>
        <MessageTimestamp ts={item.message.ts} />
        <span className="locker-status">Locker msg</span>
        <button
          type="button"
          className="locker-read-toggle"
          disabled={busy}
          aria-label={item.read ? 'Mark unread' : 'Mark read'}
          title={item.read ? 'Mark unread' : 'Mark read'}
          onClick={() => void act('read')}
        >
          {item.read ? <Circle size={12} /> : <Check size={13} />}
        </button>
      </header>
      <RelatedContextButton ownerSessionId={item.ownerSessionId} relatedMessageId={item.relatedMessageId} source={source} hidden={hidden} />
      <MarkdownRenderer
        content={lockerMessageMarkdown(item)}
        sourceSessionId={item.ownerSessionId}
        attachmentIds={item.message.parts?.flatMap((part) =>
          part.type === 'attachment' ? [part.attachmentId] : [],
        )}
        onAttachmentDownload={download}
      />
      <div className="locker-delete-row">
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act('delete')}>
          <Trash2 size={12} /> Delete record
        </Button>
      </div>
      {error && (
        <p role="alert" className="locker-error">
          {error}
        </p>
      )}
    </article>
  );
}
