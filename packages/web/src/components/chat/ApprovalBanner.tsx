import { useMemo } from 'react';
import { ShieldAlert, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useUIStore } from '@/stores/uiStore';
import { useSessionStore } from '@/stores/sessionStore';
import { wsClient } from '@/services/ws';
import { sendSessionWorkerControl } from '@/services/api';
import type { ApprovalRequest } from '@/types';

interface ApprovalChoice {
  key: string;
  label: string;
  decision: string | Record<string, unknown>;
}

function availableDecisions(request: ApprovalRequest): ApprovalChoice[] {
  const raw = request.params.availableDecisions;
  if (Array.isArray(raw)) {
    const choices = raw
      .map((item) => {
        if (typeof item === 'string') {
          return { key: item, label: decisionLabel(item), decision: item };
        }
        if (item && typeof item === 'object') {
          const record = item as Record<string, unknown>;
          const key = Object.keys(record).find((value) =>
            value === 'acceptWithExecpolicyAmendment' || value === 'applyNetworkPolicyAmendment')
            || Object.keys(record)[0];
          if (key) return { key, label: decisionLabel(key), decision: record };
          const value = typeof record.value === 'string'
            ? record.value
            : typeof record.decision === 'string' ? record.decision : null;
          return value ? { key: value, label: decisionLabel(value), decision: value } : null;
        }
        return null;
      })
      .filter((item) => item !== null) as ApprovalChoice[];
    if (choices.length > 0) return choices;
  }
  return [
    { key: 'accept', label: decisionLabel('accept'), decision: 'accept' },
    { key: 'decline', label: decisionLabel('decline'), decision: 'decline' },
  ];
}

function decisionLabel(decision: string): string {
  switch (decision) {
    case 'acceptForSession':
      return 'Allow for session';
    case 'decline':
      return 'Deny';
    case 'cancel':
      return 'Cancel';
    case 'acceptWithExecpolicyAmendment':
      return 'Allow and remember rule';
    case 'applyNetworkPolicyAmendment':
      return 'Apply network rule';
    default:
      return 'Allow';
  }
}

function requestDescription(request: ApprovalRequest): string {
  if (request.method === 'claude/permission') {
    const toolName = typeof request.params.tool_name === 'string'
      ? request.params.tool_name
      : 'Claude tool';
    const input = request.params.input;
    if (input && typeof input === 'object') {
      let rendered = '';
      try {
        rendered = JSON.stringify(input);
      } catch {
        rendered = String(input);
      }
      if (rendered.length > 3000) rendered = `${rendered.slice(0, 3000)}…`;
      return `${toolName}: ${rendered}`;
    }
    return toolName;
  }
  const command = request.params.command;
  if (typeof command === 'string' && command) return command;
  const reason = request.params.reason;
  if (typeof reason === 'string' && reason) return reason;
  if (request.method.includes('fileChange')) return 'Codex requests permission to change files';
  return 'Codex requests permission to continue';
}

function isPermissionRequest(request: ApprovalRequest): boolean {
  return request.method === 'item/permissions/requestApproval';
}

function isClaudePermissionRequest(request: ApprovalRequest): boolean {
  return request.method === 'claude/permission';
}

function permissionDescription(request: ApprovalRequest): string {
  const permissions = request.params.permissions;
  if (!permissions || typeof permissions !== 'object') return 'Additional permissions requested';
  const profile = permissions as Record<string, unknown>;
  const fileSystem = profile.fileSystem;
  const parts: string[] = [];
  if (fileSystem && typeof fileSystem === 'object') {
    const fs = fileSystem as Record<string, unknown>;
    for (const key of ['read', 'write', 'deny']) {
      const paths = fs[key];
      if (Array.isArray(paths) && paths.length > 0) {
        parts.push(`${key}: ${paths.filter((path): path is string => typeof path === 'string').join(', ')}`);
      }
    }
  }
  const network = profile.network;
  if (network && typeof network === 'object' && (network as Record<string, unknown>).enabled === true) {
    parts.push('network access');
  }
  return parts.length > 0 ? parts.join(' · ') : 'Additional permissions requested';
}

export function ApprovalBanner() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const approvalRequests = useUIStore((s) => s.approvalRequests);
  const requests = useMemo(
    () => currentSessionId
      ? approvalRequests.filter((request) => request.sessionId === currentSessionId)
      : [],
    [approvalRequests, currentSessionId],
  );
  const removeApprovalRequest = useUIStore((s) => s.removeApprovalRequest);
  const showToast = useUIStore((s) => s.showToast);

  if (requests.length === 0) return null;

  const respond = async (
    request: ApprovalRequest,
    decision: string | Record<string, unknown>,
  ) => {
    const permissionRequest = isPermissionRequest(request);
    const claudePermissionRequest = isClaudePermissionRequest(request);
    const isStructuredDecision = typeof decision === 'object';
    const control = {
      type: permissionRequest || claudePermissionRequest ? 'permission_response' : 'approval_response',
      request_id: request.requestId,
      ...(claudePermissionRequest
        ? { decision }
        : permissionRequest
        ? {
          permissions: decision === 'decline' ? {} : request.params.permissions ?? {},
          scope: decision === 'acceptForSession' ? 'session' : 'turn',
        }
        : isStructuredDecision ? { result: { decision } } : { decision }),
    };
    const sent = wsClient.send({
      type: 'worker_control',
      sessionId: request.sessionId,
      control,
    });
    if (!sent) {
      try {
        await sendSessionWorkerControl(request.sessionId, control);
      } catch (error) {
        showToast((error as Error).message || '审批未发送', 'error');
        return;
      }
    }
    removeApprovalRequest(request.sessionId, request.requestId);
  };

  return (
    <div className="mx-3 mb-2 rounded-lg border border-warning/50 bg-bg-secondary px-3 py-2 shadow-panel">
      {requests.map((request) => (
        <div key={`${request.workerId}:${String(request.requestId)}`} className="flex items-start gap-2 py-1">
          <ShieldAlert size={17} className="mt-0.5 flex-shrink-0 text-warning" />
          <div className="min-w-0 flex-1">
            <div className="text-xs font-medium text-text-primary">
              {isClaudePermissionRequest(request)
                ? 'Claude requests permission'
                : isPermissionRequest(request) ? 'Codex requests additional permissions' : 'Codex requests approval'}
            </div>
            <div className="mt-1 max-h-20 overflow-auto whitespace-pre-wrap break-all font-mono text-xs text-text-secondary">
              {isPermissionRequest(request) ? permissionDescription(request) : requestDescription(request)}
            </div>
            <div className="flex flex-wrap justify-end gap-1">
              {(isPermissionRequest(request) || isClaudePermissionRequest(request)
                ? [
                  { key: 'accept', label: decisionLabel('accept'), decision: 'accept' },
                  { key: 'acceptForSession', label: decisionLabel('acceptForSession'), decision: 'acceptForSession' },
                  { key: 'decline', label: decisionLabel('decline'), decision: 'decline' },
                ]
                : availableDecisions(request)).map((choice) => (
                <Button
                  key={choice.key}
                  size="sm"
                  variant={choice.key === 'decline' || choice.key === 'cancel' ? 'danger' : 'primary'}
                  onClick={() => respond(request, choice.decision)}
                >
                  {choice.label}
                </Button>
              ))}
            </div>
          </div>
          <button
            type="button"
            className="p-1 text-text-tertiary hover:text-text-primary"
            title="Dismiss (deny)"
            onClick={() => respond(request, 'decline')}
          >
            <X size={15} />
          </button>
        </div>
      ))}
    </div>
  );
}
