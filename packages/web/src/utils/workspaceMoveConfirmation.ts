export interface WorkspaceMoveConfirmationRequest {
  sessionName: string;
  subtreeCount: number;
  managerName: string;
  targetWorkspaceName: string;
}

/** Resolve through the app's modal host. No host means fail closed. */
export function confirmWorkspaceManagerChange(
  request: WorkspaceMoveConfirmationRequest,
): Promise<boolean> {
  return new Promise((resolve) => {
    const event = new CustomEvent('pan:confirm-workspace-manager-change', {
      detail: { ...request, resolve },
    });
    window.dispatchEvent(event);
  });
}
