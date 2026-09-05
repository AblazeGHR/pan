import { useState } from 'react';
import type { Session } from '@/types';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';

interface Props {
  sessions: Session[];
  specialIds: string[];
  normalIds: string[];
  descendantCount: number;
  onClose: () => void;
  onConfirm: (cascade: boolean) => void;
  onCancelSpecial: () => void;
}

export function SessionDeleteModal({ sessions, specialIds, normalIds, descendantCount, onClose, onConfirm, onCancelSpecial }: Props) {
  const [cascade, setCascade] = useState(true);
  const totalSelected = specialIds.length + normalIds.length;
  return (
    <Modal open title="Confirm session deletion" onClose={onClose} size="md">
      <div className="space-y-3 text-sm text-text-secondary">
        <p>
          This request includes {totalSelected} selected session{totalSelected === 1 ? '' : 's'}.
          {specialIds.length > 0 && ` ${specialIds.length} have managed children.`}
        </p>
        <p>
          {cascade ? `This will process up to ${specialIds.length + descendantCount} managed session${specialIds.length + descendantCount === 1 ? '' : 's'} recursively` : `This will process ${specialIds.length} managed parent session${specialIds.length === 1 ? '' : 's'} only`}.
          {normalIds.length > 0 && ` ${normalIds.length} session${normalIds.length === 1 ? '' : 's'} without children will also be deleted.`}
        </p>
        <label className="flex items-start gap-2 cursor-pointer text-text-primary">
          <input type="checkbox" checked={cascade} onChange={(e) => setCascade(e.target.checked)} className="mt-0.5 accent-accent" />
          <span>同时删除所有被管理的子 Session</span>
        </label>
        <p className="text-xs text-text-tertiary">
          {cascade ? `Recursive descendants included: ${descendantCount}.` : `Managed-child sessions not selected for recursion will be kept.`}
          {sessions.length > 0 && ' Missing or repeated relationship entries are ignored safely.'}
        </p>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onCancelSpecial}>Cancel special deletion</Button>
          <Button variant="danger" onClick={() => onConfirm(cascade)}>Delete selected</Button>
        </div>
      </div>
    </Modal>
  );
}
