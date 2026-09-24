import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import type { WorkspaceMoveConfirmationRequest } from '@/utils/workspaceMoveConfirmation';

interface Props {
  request: (WorkspaceMoveConfirmationRequest & { resolve: (accepted: boolean) => void }) | null;
  onClose: () => void;
  onConfirm: () => void;
}

function subtreeDescription(count: number): string {
  const descendants = Math.max(0, count - 1);
  return descendants === 1
    ? 'and its 1 managed descendant'
    : `and its ${descendants} managed descendants`;
}

export function WorkspaceManagerChangeConfirmationModal({ request, onClose, onConfirm }: Props) {
  const isDetach = request?.changeType === 'detach';
  return (
    <Modal
      open={!!request}
      title={isDetach ? 'Confirm detach from manager' : 'Confirm management change'}
      onClose={onClose}
      size="md"
    >
      {request && (
        <div className="space-y-4">
          {isDetach ? (
            <p className="text-sm text-text-secondary">
              Moving <strong>{request.sessionName}</strong> {subtreeDescription(request.subtreeCount)} to <strong>{request.targetWorkspaceName}</strong> will detach it from <strong>{request.managerName}</strong> and make it a new management root.
            </p>
          ) : (
            <p className="text-sm text-text-secondary">
              Reparenting <strong>{request.sessionName}</strong> {subtreeDescription(request.subtreeCount)} under <strong>{request.managerName}</strong> will make the subtree inherit the manager tree&apos;s workspace: <strong>{request.targetWorkspaceName}</strong>.
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button variant="primary" onClick={onConfirm}>
              {isDetach ? 'Move and detach' : 'Reparent subtree'}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
